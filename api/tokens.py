"""Signed full-view brief links (spec §1.2) — HS256 JWT, stdlib only.

## Why this is hand-rolled rather than PyJWT

Not to save a dependency, though it does. **The algorithm-confusion class of bug
exists because a verifier reads `alg` out of the attacker-supplied header and
dispatches on it.** This module never parses `alg` for dispatch at all: it
recomputes an HMAC-SHA256 over the received `header.payload` and compares. There
is no code path in which a token can select its own verification algorithm, so
`alg: none` and RS256->HS256 confusion are not "rejected", they are
*unrepresentable*. The header is still validated (§`_check_header`) so a token
claiming something else is refused loudly rather than silently accepted.

The primitives are `hmac` + `hashlib` from the standard library. This is not
rolling crypto; it is 40 lines of encoding around a stdlib HMAC.

## What a leaked token actually grants — the blast radius

Deliberately small, and it must stay that way:

- **Read access to the paid sections of ONE brief, for ONE team, until `exp`.**
  Nothing else. The claims are `{sub, team_id, exp, iat}` and the only route that
  reads them scopes its query to exactly that `team_id` and gameweek.
- It is **not a session**. It authorizes no writes, no account, no billing view,
  no other team's brief, and it cannot be exchanged for anything.
- It **cannot be extended**. `exp` is `current_period_end` at mint time; a renewal
  mints a *new* token in the next email rather than lengthening this one.
- The underlying content is not itself a secret (FPL squads are public data — the
  spec §1.2 tradeoff). The paid product is generation + delivery + full analysis.

The moment a token carries "this person is paid" as a general claim rather than
"this brief, this team, this deadline", the blast radius becomes the whole account
and this docstring is a lie. Do not add claims.

## Rotation

`BRIEF_TOKEN_SECRET_PREV` is accepted on verify but never used to sign. Rotate by
moving the old value into `_PREV`, setting a new `BRIEF_TOKEN_SECRET`, and
dropping `_PREV` once the longest outstanding `exp` has passed. Without the
overlap, rotating the secret dead-links every brief email already delivered.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from .config import settings

log = logging.getLogger("api.tokens")

# HS256 and only HS256. Not a default, not a parameter — the module has one.
_ALG = "HS256"
_HEADER = {"alg": _ALG, "typ": "JWT"}

# 32 bytes of entropy. `secrets.token_urlsafe(32)` produces 43 chars, so the
# practical floor on a correctly generated secret is comfortably above this.
MIN_SECRET_CHARS = 32


class TokenError(Exception):
    """Verification failed. Carries a reason for logs — never for a response body."""


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def assert_secret_usable() -> None:
    """Fail closed at startup on a missing or weak signing secret.

    Called from the app lifespan when the paywall or the send job is on. A short
    secret is not a lint issue: HS256 with a guessable key is a token forge, and a
    forged token is free access to the paid product for every team at once.
    """
    secret = settings.brief_token_secret
    if not secret:
        raise RuntimeError(
            "BRIEF_TOKEN_SECRET is empty. It is required whenever PAYWALL_ENABLED "
            "or SEND_ENABLED is true — brief links cannot be signed without it. "
            'Generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if len(secret) < MIN_SECRET_CHARS:
        raise RuntimeError(
            f"BRIEF_TOKEN_SECRET is {len(secret)} characters; the minimum is "
            f"{MIN_SECRET_CHARS}. A short HMAC key is brute-forceable offline, and "
            "forging a token is free access to the paid product. "
            'Generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )


def _sign(signing_input: bytes, secret: str) -> str:
    return _b64u(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())


def mint(*, subscription_id: int | str, team_id: int, expires_at: datetime) -> str:
    """Sign a full-view token. Claims are exactly {sub, team_id, exp, iat}.

    Raises if the secret is unusable — minting with a weak key would produce a
    token that looks valid to us and is forgeable by anyone else.
    """
    assert_secret_usable()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    claims = {
        "sub": str(subscription_id),
        "team_id": int(team_id),
        "exp": int(expires_at.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    head = _b64u(json.dumps(_HEADER, separators=(",", ":"), sort_keys=True).encode())
    body = _b64u(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{head}.{body}".encode()
    return f"{head}.{body}.{_sign(signing_input, settings.brief_token_secret)}"


def _check_header(head_b64: str) -> None:
    """Reject a header that is not exactly ours.

    Nothing downstream *uses* `alg` — verification is hard-wired to HMAC-SHA256 —
    so this cannot be the control that stops confusion attacks. It is here so a
    token claiming `alg: none` or `RS256` fails with a clear reason instead of
    failing as a signature mismatch, which is a much worse thing to debug.
    """
    try:
        header = json.loads(_b64u_decode(head_b64))
    except Exception as exc:
        raise TokenError(f"unparseable header: {exc}") from exc
    if header.get("alg") != _ALG:
        raise TokenError(f"unexpected alg {header.get('alg')!r}; only {_ALG} is signed")
    if header.get("typ") not in (None, "JWT"):
        raise TokenError(f"unexpected typ {header.get('typ')!r}")


def verify(token: str, *, now: datetime | None = None) -> dict:
    """Return the claims, or raise TokenError. Accepts the previous secret too.

    `exp` is REQUIRED. A token without one is refused rather than treated as
    non-expiring — "absent means forever" is how a leaked link outlives the
    subscription that paid for it.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed token")
    head_b64, body_b64, sig = parts
    _check_header(head_b64)

    signing_input = f"{head_b64}.{body_b64}".encode()
    # Current secret first, then the rotation overlap. compare_digest on every
    # candidate: a short-circuiting `==` here leaks signature bytes by timing.
    candidates = [s for s in (settings.brief_token_secret,
                              settings.brief_token_secret_prev) if s]
    if not candidates:
        raise TokenError("no signing secret configured")
    if not any(hmac.compare_digest(_sign(signing_input, s), sig) for s in candidates):
        raise TokenError("bad signature")

    try:
        claims = json.loads(_b64u_decode(body_b64))
    except Exception as exc:
        raise TokenError(f"unparseable claims: {exc}") from exc

    exp = claims.get("exp")
    if not isinstance(exp, int):
        raise TokenError("missing or non-integer exp")
    now = now or datetime.now(timezone.utc)
    if int(now.timestamp()) >= exp:
        raise TokenError("expired")
    return claims


def claims_if_valid(token: str | None, *, now: datetime | None = None) -> dict | None:
    """The verified claims, or None for absent/invalid/expired. Never raises.

    Added for the accounts build (2026-08-04): the brief route needs `sub` (the
    subscription id) to resolve the link to an ACCOUNT, not just `team_id`.

    This reads claims the token already carried — it does not add any. The blast
    radius stated at the top of this module is unchanged precisely because the
    claim set is unchanged: `sub` was always there, and it names a row that is
    re-read and re-checked on every request rather than being believed.
    """
    if not token:
        return None
    try:
        return verify(token, now=now)
    except TokenError as exc:
        log.info("brief token rejected: %s", exc)
        return None


def team_id_if_valid(token: str | None, *, now: datetime | None = None) -> int | None:
    """`token` -> team_id, or None for absent/invalid/expired. Never raises.

    The route contract (§1.2) is "no or invalid token => free view", so a bad token
    must degrade rather than error. The reason is logged, never returned: telling a
    caller *why* their forgery failed is free help.
    """
    if not token:
        return None
    try:
        claims = verify(token, now=now)
    except TokenError as exc:
        log.info("brief token rejected: %s", exc)
        return None
    tid = claims.get("team_id")
    return int(tid) if isinstance(tid, int) else None


# --- unsubscribe tokens (spec §1.8) ----------------------------------------
#
# A DIFFERENT scheme on purpose: a plain keyed HMAC over the address, with no
# expiry. Three reasons it is not a JWT:
#   1. An unsubscribe link must work forever. Mail sits in inboxes for years, and a
#      link that has expired is, for deliverability purposes, no link at all.
#   2. It is derived, not stored — nothing to migrate, nothing to leak.
#   3. Its blast radius is "unsubscribe this one address", which is not worth the
#      complexity of a claims format.

def unsubscribe_token(email: str) -> str:
    """Stable per-address token. Same address, same token, forever."""
    assert_secret_usable()
    mac = hmac.new(settings.brief_token_secret.encode(),
                   f"unsubscribe:{email.strip().lower()}".encode(), hashlib.sha256)
    return _b64u(mac.digest())


def unsubscribe_token_valid(email: str, token: str) -> bool:
    candidates = [s for s in (settings.brief_token_secret,
                              settings.brief_token_secret_prev) if s]
    for secret in candidates:
        mac = hmac.new(secret.encode(),
                       f"unsubscribe:{email.strip().lower()}".encode(), hashlib.sha256)
        if hmac.compare_digest(_b64u(mac.digest()), token):
            return True
    return False
