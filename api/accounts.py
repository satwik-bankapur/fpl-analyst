"""Accounts, sessions, magic links and Google OAuth (accounts build, 2026-08-04).

Layer 5 §5 said "no user accounts in v1" and delivered paid access through signed
links in email. That is superseded — see the layer5 §8 drift note. What has NOT
changed, and is the constraint this whole module is shaped around:

    THE FREE BRIEF STAYS OPEN. No route in the free funnel — `POST /api/brief`,
    the draft picker, `GET /api/preview/*`, `GET /api/players` — gains a login,
    a session requirement, or a 401. Nothing here is a middleware; every session
    read is an OPTIONAL dependency whose `None` case is the anonymous free view.
    A login placed anywhere upstream of the paywall is a bug, not a hardening.

## The shape of the thing

Three secrets, all of them bearer tokens, all stored as SHA-256 and never in
plaintext:

  - **magic link token** — 15 min, single use, in an email. Proves control of an
    address.
  - **session token** — 30 days, in an httpOnly cookie. Proves "this browser is
    that account".
  - **OAuth state** — one round trip, in a short-lived cookie. Proves the callback
    belongs to a flow this browser started (CSRF).

The database stores hashes, so a dump of `sessions` or `magic_link_tokens` yields
nothing a caller can present. This is the same reason `send_log` stores no message
bodies: a table that would be dangerous to leak is a table worth not filling.

## What this module does NOT decide

Entitlement. `billing.is_entitled` is still the one entitlement function and the
allow-list `ENTITLED_STATUSES` is still the whole rule; this module only answers
"which account is this?", and `billing.entitled_for_account` joins the two. An
account is an identity, never a grant — that separation is what keeps a bug here
from being a free subscription.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import delete, select, update

from fpl.models import Account, AuthSession, MagicLinkToken

from .config import settings

log = logging.getLogger("api.accounts")

# Cookie names. `__Host-` is deliberately NOT used: that prefix forbids a Domain
# attribute, and the launch topology needs one to share the session between the
# Next.js app and the API on a sibling subdomain.
SESSION_COOKIE = "fpl_session"
OAUTH_STATE_COOKIE = "fpl_oauth_state"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class AuthError(Exception):
    """Auth failed. The reason is for logs; routes answer with a generic message."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(code)


# --- primitives -------------------------------------------------------------

def normalize_email(raw: str | None) -> str | None:
    """Strip + lowercase, or None if it cannot be an address.

    THE single normalisation point for account identity. `accounts.email` is
    UNIQUE and plain TEXT rather than `citext`, so this function is what makes
    the uniqueness case-insensitive — every write path must go through it. The
    rest of the codebase already lowercases addresses at its own boundaries
    (`billing._email_of`, `service.unsubscribe`, `tokens.unsubscribe_token`);
    this makes that habit a named, tested rule.

    The check is deliberately thin — one `@`, no spaces, something either side.
    Validating an address properly means sending mail to it, which is exactly
    what the magic link does.
    """
    if not raw:
        return None
    email = raw.strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return None
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain or any(c.isspace() for c in email):
        return None
    return email


def new_token() -> str:
    """32 bytes of CSPRNG entropy, url-safe. The only way any token here is made."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex of a bearer token.

    Plain SHA-256, not bcrypt/argon2, and that is correct HERE specifically: this
    hashes a 256-bit random value, not a human-chosen password. Key stretching
    defends against guessing a low-entropy secret; there is nothing to guess in
    `secrets.token_urlsafe(32)`, and a slow hash on the session-lookup path would
    be a self-inflicted denial of service on every request.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    """Constant-time equality on two hashes.

    Belt and braces. The real control is that every lookup is BY hash, so the
    secret itself is never compared against anything and no comparison timing can
    leak it. This guards the residual case — a caller who learns a hash learning
    whether it exists — at the cost of nothing.
    """
    return hmac.compare_digest(a, b)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; Postgres does not. Every column here is written
    aware-UTC, so stamping UTC back on is a statement of what it already is."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mask(email: str | None) -> str:
    """A stable, non-reversible handle for logs (audit §D4.3).

    NO ADDRESS EVER REACHES A LOG LINE FROM THIS MODULE. Auth logs are the
    highest-value ones to correlate — "who signed in, when, from where" is a
    social graph — so the rule the send job follows applies here without exception.
    """
    if not email:
        return "-"
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]


# --- accounts ---------------------------------------------------------------

def get_account_by_email(session, email: str) -> Account | None:
    return session.scalars(select(Account).where(Account.email == email)).first()


def needs_terms_acceptance(account: Account | None) -> bool:
    """Has this account accepted the CURRENT terms version?

    A NULL `terms_accepted_at` counts as NOT accepted — an account row that
    predates terms tracking has not agreed to anything, and reading absence as
    consent is how a consent record becomes decorative.
    """
    if account is None:
        return True
    if account.terms_accepted_at is None:
        return True
    return (account.terms_version or "") != settings.terms_version


def accept_terms(account: Account, *, now: datetime | None = None) -> None:
    account.terms_accepted_at = now or datetime.now(timezone.utc)
    account.terms_version = settings.terms_version


def find_or_create_account(session, email: str, *, provider: str,
                           provider_id: str | None = None,
                           accept_terms_now: bool = False,
                           now: datetime | None = None) -> tuple[Account, bool]:
    """Resolve an address to an account, creating it if absent. Returns (account, created).

    `email` must already be normalised — callers use `normalize_email`.

    The account is found by ADDRESS, never by provider id, so signing in with
    Google to an address that already has a magic-link account lands in the same
    account rather than forking the customer in two. `auth_provider` therefore
    records how the account was FIRST created and is never rewritten; `provider_id`
    is filled in the first time Google is used.

    `accept_terms_now` is passed only where a caller actually ticked the box. It
    is not defaulted true, because the one thing a consent record must never do is
    record consent nobody gave.
    """
    now = now or datetime.now(timezone.utc)
    account = get_account_by_email(session, email)
    if account is not None:
        if provider_id and not account.provider_id:
            account.provider_id = provider_id
        if accept_terms_now and needs_terms_acceptance(account):
            accept_terms(account, now=now)
        return account, False

    account = Account(
        email=email,
        auth_provider=provider,
        provider_id=provider_id,
        created_at=now,
    )
    if accept_terms_now:
        accept_terms(account, now=now)
    session.add(account)
    session.flush()
    log.info("accounts: created account id=%s provider=%s email_hash=%s",
             account.id, provider, mask(email))
    return account, True


# --- sessions ---------------------------------------------------------------

def create_session(session, account_id: int, *, now: datetime | None = None) -> str:
    """Mint a session and return the PLAINTEXT token (the only time it exists).

    SESSION FIXATION: there is no code path anywhere that turns a client-supplied
    value into a session row. A token is always freshly generated here, so an
    attacker cannot plant one and wait for the victim to authenticate into it.
    Callers that are logging someone in ALSO call `destroy_session` on whatever
    cookie arrived with the request — see `login` in api/routes.py — so the
    identifier changes at the privilege boundary even when one was already valid.
    """
    now = now or datetime.now(timezone.utc)
    token = new_token()
    session.add(AuthSession(
        token_hash=hash_token(token),
        account_id=account_id,
        expires_at=now + timedelta(days=settings.session_ttl_days),
        created_at=now,
    ))
    session.flush()
    return token


def account_for_session(session, token: str | None, *,
                        now: datetime | None = None) -> Account | None:
    """Resolve a session cookie to an account. None for absent/unknown/expired.

    NEVER RAISES. Every caller is on a path whose anonymous behaviour is the free
    view, so a bad cookie has to degrade to "not signed in" rather than error —
    the same contract `tokens.team_id_if_valid` has, for the same reason.
    """
    if not token:
        return None
    now = now or datetime.now(timezone.utc)
    digest = hash_token(token)
    row = session.get(AuthSession, digest)
    if row is None or not _same(row.token_hash, digest):
        return None
    expires = _aware(row.expires_at)
    if expires is None or now >= expires:
        return None
    return session.get(Account, row.account_id)


def destroy_session(session, token: str | None) -> bool:
    """Delete one session row. Idempotent; True if something was deleted."""
    if not token:
        return False
    result = session.execute(
        delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    return bool(result.rowcount)


def destroy_sessions_for_account(session, account_id: int) -> int:
    """Sign an account out everywhere. Used by the delete path."""
    result = session.execute(
        delete(AuthSession).where(AuthSession.account_id == account_id))
    return result.rowcount or 0


def purge_expired(session, *, now: datetime | None = None) -> int:
    """Drop expired sessions and consumed/expired magic links.

    Unbounded growth was audit finding H5 and again H1; a table that only ever
    grows is the same bug regardless of which table it is. Called from the sync
    handler alongside the rate-limit sweep.
    """
    now = now or datetime.now(timezone.utc)
    dropped = session.execute(
        delete(AuthSession).where(AuthSession.expires_at < now)).rowcount or 0
    dropped += session.execute(
        delete(MagicLinkToken).where(MagicLinkToken.expires_at < now)).rowcount or 0
    return dropped


# --- magic links ------------------------------------------------------------

def issue_magic_link(session, email: str, *, now: datetime | None = None) -> str:
    """Create a single-use sign-in token for `email`; returns the PLAINTEXT token.

    Called for EVERY well-formed address, whether or not an account exists — the
    account is created on verification, not here. That is what lets the route
    answer identically in both cases (see `request_link` in api/routes.py): if the
    work differed, the response time would be the enumeration oracle the identical
    body was trying to close.
    """
    now = now or datetime.now(timezone.utc)
    token = new_token()
    session.add(MagicLinkToken(
        token_hash=hash_token(token),
        email=email,
        expires_at=now + timedelta(minutes=settings.magic_link_ttl_minutes),
        created_at=now,
    ))
    session.flush()
    return token


def consume_magic_link(session, token: str | None, *,
                       now: datetime | None = None) -> str | None:
    """Claim a magic link exactly once. Returns the email, or None.

    SINGLE USE IS AN ATOMIC UPDATE, NOT A READ-THEN-WRITE. The claim is
    `UPDATE ... WHERE token_hash = ? AND consumed_at IS NULL`, and it succeeds for
    exactly one caller no matter how many arrive together; a `SELECT` followed by
    a write would let two concurrent clicks — a mail scanner prefetching the link
    and the human clicking it — both create a session.

    Expiry is checked BEFORE the claim so an expired token is not consumed by
    looking at it, and `consumed_at` is left in place rather than deleted so a
    replay is distinguishable from a token that never existed, and can be logged
    as the replay it is.
    """
    if not token:
        return None
    now = now or datetime.now(timezone.utc)
    digest = hash_token(token)
    row = session.get(MagicLinkToken, digest)
    if row is None or not _same(row.token_hash, digest):
        log.info("accounts: magic link not found")
        return None

    expires = _aware(row.expires_at)
    if expires is None or now >= expires:
        log.info("accounts: magic link expired email_hash=%s", mask(row.email))
        return None
    if row.consumed_at is not None:
        log.warning("accounts: magic link REPLAYED email_hash=%s", mask(row.email))
        return None

    claimed = session.execute(
        update(MagicLinkToken)
        .where(MagicLinkToken.token_hash == digest,
               MagicLinkToken.consumed_at.is_(None))
        .values(consumed_at=now)
    ).rowcount
    if not claimed:
        log.warning("accounts: magic link lost the claim race email_hash=%s",
                    mask(row.email))
        return None
    return row.email


# --- Google OAuth -----------------------------------------------------------

def google_enabled() -> bool:
    """Both halves or neither. A half-configured provider fails at the redirect,
    which is the worst place to discover it — the routes 404 instead."""
    return bool(settings.google_client_id and settings.google_client_secret)


def pack_state(nonce: str, target: str) -> str:
    """`state` = the CSRF nonce plus where to land, as one url-safe string.

    The nonce alone goes in the cookie; the target rides in `state` because it is
    not a secret and does not need to be — it is reduced by `safe_redirect` before
    it reaches a Location header either way, so an attacker who edits this half
    gains a redirect to a path on our own site.

    Keeping the COOKIE a bare token matters: a cookie value containing `/` gets
    quoted by the server and arrives back quoted, and the comparison then fails
    against a nonce that was never wrong. Structural, not cosmetic — a CSRF check
    that fails for the wrong reason is one somebody eventually "fixes" by loosening.
    """
    return f"{nonce}.{_b64u_str(target)}"


def unpack_state(state: str) -> tuple[str, str]:
    """`state` -> (nonce, target). Never raises; a malformed half yields ("", "/")."""
    nonce, _, encoded = (state or "").partition(".")
    try:
        target = _b64u_str_decode(encoded) if encoded else "/"
    except Exception:
        target = "/"
    return nonce, target


def _b64u_str(raw: str) -> str:
    import base64

    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _b64u_str_decode(encoded: str) -> str:
    import base64

    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()


def google_authorize_url(state: str) -> str:
    """Build the consent-screen URL. `state` is the CSRF nonce (see the callback)."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        # We want the verified address and nothing else. No refresh token is
        # requested because there is no Google API this product ever calls on the
        # user's behalf — a credential you keep is a credential you can leak.
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _decode_id_token_payload(id_token: str) -> dict:
    """Read the claims out of a Google id_token WITHOUT verifying its signature.

    That is safe HERE and nowhere else: this token was not supplied by the
    browser. It was returned in the body of a direct, TLS-authenticated,
    client-secret-authenticated POST from `oauth2.googleapis.com` two lines above
    the call site. OpenID Connect Core §3.1.3.7 says exactly this — a client using
    the code flow MAY rely on TLS server validation of the token endpoint instead
    of checking the signature, because the channel already proves the issuer.

    If this ever moves to the implicit flow, or the id_token starts arriving from
    anything the user's browser touched, THIS FUNCTION BECOMES A VULNERABILITY and
    must be replaced with real JWKS verification. `aud` and `iss` are still
    checked by the caller, so a token minted for a different client is refused.
    """
    import base64
    import json

    parts = id_token.split(".")
    if len(parts) != 3:
        raise AuthError("google_failed", "id_token is not a JWT")
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        return json.loads(raw)
    except Exception as exc:
        raise AuthError("google_failed", f"unparseable id_token: {exc}") from exc


def google_exchange(code: str, *, post=None) -> tuple[str, str | None]:
    """Exchange an authorization code for a VERIFIED (email, subject) pair.

    The subject (`sub`) is Google's stable per-account id. It is STORED but never
    used to look an account up — matching is by address, so that signing in with
    Google to an address that already has a magic-link account lands in the same
    account instead of forking the customer in two.

    `post` is injectable so tests never touch the network — the same seam
    `billing.create_checkout` uses.

    Three checks on the claims, all of them load-bearing:
      - `aud` must be our client id — a code obtained for another Google client
        must not sign anyone in here.
      - `iss` must be Google.
      - `email_verified` must be true. An unverified Google address is a claim
        about an address, not proof of it, and this account model joins to
        subscriptions BY ADDRESS — accepting one would let a Google account with
        an unverified address inherit a stranger's subscription.
    """
    if not google_enabled():
        raise AuthError("google_disabled", "Google sign-in is not configured")

    import httpx

    post = post or httpx.post
    try:
        resp = post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except AuthError:
        raise
    except Exception as exc:
        # The provider's error body can echo the client secret back; it goes to
        # the log at warning, never into a response.
        log.warning("accounts: google token exchange failed: %s", exc)
        raise AuthError("google_failed", "provider rejected the code") from exc

    id_token = payload.get("id_token")
    if not id_token:
        raise AuthError("google_failed", "token response carried no id_token")

    claims = _decode_id_token_payload(id_token)
    if claims.get("aud") != settings.google_client_id:
        raise AuthError("google_failed", "id_token aud is not this client")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("google_failed", f"unexpected iss {claims.get('iss')!r}")
    if claims.get("email_verified") not in (True, "true"):
        raise AuthError("google_unverified", "Google address is not verified")

    email = normalize_email(claims.get("email"))
    if not email:
        raise AuthError("google_failed", "id_token carried no usable email")
    subject = claims.get("sub")
    return email, (str(subject) if subject else None)


# --- redirect safety --------------------------------------------------------

def safe_redirect(target: str | None, *, default: str = "/") -> str:
    """Reduce a post-login `next` to a SAFE, SAME-SITE path.

    Open-redirect protection, and it is an allow-list of shapes rather than a
    blocklist of bad ones — the same reasoning as `FREE_SECTIONS` and
    `ENTITLED_STATUSES`. Only a path is ever accepted, so there is no host to
    compare and nothing to normalise around:

      "/brief/123/5"      -> kept
      "//evil.com/x"      -> rejected (protocol-relative; a browser reads the host)
      "/\\evil.com"       -> rejected (backslash; some browsers treat it as "/")
      "https://evil.com"  -> rejected (absolute)
      "javascript:..."    -> rejected (not a path)

    An open redirect on a login route is how a phishing page borrows your domain
    for the address bar and hands the victim to itself with a fresh session.
    """
    if not target or not target.startswith("/"):
        return default
    if target.startswith("//") or target.startswith("/\\"):
        return default
    if "\n" in target or "\r" in target:          # header splitting
        return default
    return target
