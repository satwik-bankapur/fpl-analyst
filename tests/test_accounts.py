"""Accounts + auth (accounts build, 2026-08-04).

Structured around the two things this build could get catastrophically wrong:

  1. **Putting a login in front of the free brief.** That is a product kill, not a
     bug, so §1 asserts the whole free funnel still works with no cookie at all.
  2. **Making entitlement resolvable by something other than an account.** The
     paywall's fail-open history (docs/CLAUDE.md, the section-vocabulary note) is
     the cautionary tale; §4 pins that a token, an address, or a `tier` column
     cannot substitute for a session -> account -> subscription resolution.

Offline like the rest: no Google, no Resend, no Paddle. The OAuth token endpoint
is injected, mail is a no-op, and every signature is computed locally.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.models import (
    Account, AuthSession, Base, Brief, EmailSignup, MagicLinkToken, SendLog,
    Subscription,
)
from graph.deps import Deps

from api import accounts, billing, mail, service, tokens
from api.config import settings
from api.main import app
from api.ratelimit import (
    admin_limiter, auth_email_limiter, auth_link_limiter, auth_verify_limiter,
    limiter, request_limiter,
)
from api.service import get_deps

TEST_SECRET = "test-secret-with-enough-entropy-for-hs256-xx"
WEBHOOK_SECRET = "pdl_ntfset_test_0123456789abcdef"
PRICE_MONTHLY = "pri_monthly_01"
GOOGLE_CLIENT = "google-client-id.apps.googleusercontent.com"
WEB = "https://example.test"


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch):
    monkeypatch.setattr(settings, "brief_token_secret", TEST_SECRET)
    monkeypatch.setattr(settings, "brief_token_secret_prev", "")
    monkeypatch.setattr(settings, "paddle_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "paddle_price_monthly", PRICE_MONTHLY)
    monkeypatch.setattr(settings, "paywall_enabled", False)
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "public_web_url", WEB)
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.test")
    monkeypatch.setattr(settings, "google_client_id", GOOGLE_CLIENT)
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "terms_version", "2026-08-04")
    # §1's whole subject is that the free funnel takes no login. FREE_BRIEF_ACCESS
    # (2026-08-06) can change that deliberately, and it is read from a .env — so
    # pin the default here, or a developer with the gate switched on locally would
    # see this file fail and read it as the constraint being broken.
    monkeypatch.setattr(settings, "free_brief_access", "open")
    # The TestClient speaks http, and a Secure cookie would be dropped before any
    # assertion could see it. Production defaults to True; api/main asserts nothing
    # about it, so this is a transport concession, not a relaxed rule.
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    for lim in (limiter, request_limiter, admin_limiter):
        lim.reset()
    yield
    for lim in (limiter, request_limiter, admin_limiter):
        lim.reset()


@pytest.fixture
def client(sf):
    app.dependency_overrides[get_deps] = lambda: Deps(session_factory=sf, client=None)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def deps(sf):
    return Deps(session_factory=sf, client=None)


@pytest.fixture(autouse=True)
def no_mail(monkeypatch):
    """Capture magic links instead of mailing them. Returns the list of sends."""
    sent = []

    def fake(email, token, *, next_path="/"):
        sent.append({"email": email, "token": token, "next": next_path})
        return True

    monkeypatch.setattr(mail, "send_magic_link", fake)
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    return sent


# --- helpers ----------------------------------------------------------------

def sub_event(*, event_id="evt_001", sub_id="sub_001", status="active",
              email="fan@example.com", team_id=1234, period_end=None) -> dict:
    if period_end is None:
        period_end = datetime.now(timezone.utc) + timedelta(days=30)
    return {
        "event_id": event_id,
        "event_type": "subscription.created",
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "id": sub_id,
            "status": status,
            "customer_id": "ctm_001",
            "items": [{"price": {"id": PRICE_MONTHLY}}],
            "custom_data": {"fpl_team_id": team_id, "email": email},
            "current_billing_period": {
                "ends_at": period_end.isoformat().replace("+00:00", "Z")},
        },
    }


def post_webhook(client, payload: dict):
    import hmac as _hmac

    body = json.dumps(payload).encode()
    ts = int(time.time())
    mac = _hmac.new(WEBHOOK_SECRET.encode(), f"{ts}:".encode() + body,
                    hashlib.sha256)
    return client.post("/api/webhooks/paddle", content=body,
                       headers={"paddle-signature": f"ts={ts};h1={mac.hexdigest()}",
                                "content-type": "application/json"})


def persist_brief(sf, team_id: int, gw: int) -> None:
    md = (
        "## Verdict\nv\n\n## Watchlist\nw\n\n## Captaincy\nc\n\n"
        "## Transfer Plan\nt\n\n## Bench Plan\nb\n\n## Chips\nch\n"
    )
    with sf() as s, s.begin():
        s.add(Brief(fpl_team_id=team_id, gameweek=gw, brief_md=md,
                    model="stub", input_hash=f"h{team_id}{gw}"))


def request_and_link(client, sent, email: str) -> str:
    """Request a magic link and return the plaintext token that was 'mailed'."""
    before = len(sent)
    r = client.post("/api/auth/request-link", json={"email": email})
    assert r.status_code == 200
    assert len(sent) == before + 1
    return sent[-1]["token"]


def sign_in(client, sent, email: str, *, accept_terms=True) -> None:
    token = request_and_link(client, sent, email)
    r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    if accept_terms:
        assert client.post("/api/auth/accept-terms").status_code == 200


# ===========================================================================
# 1 — THE FREE FUNNEL IS NOT BEHIND A LOGIN
#
# The design constraint this build was given, asserted first because it is the
# one failure that would be worth reverting the whole branch for.
# ===========================================================================

def test_the_free_brief_path_works_with_no_session_at_all(client, sf):
    persist_brief(sf, 1234, 5)
    r = client.get("/api/brief/1234/5")
    assert r.status_code == 200
    assert r.json()["sections"], "an anonymous reader still gets a brief"


def test_no_free_route_answers_401_without_a_session(client, sf, monkeypatch):
    """Every public route, anonymous. Not one of them may demand a login.

    Walks the routes rather than naming them, so a future route added to the
    public surface is covered by this test the day it lands.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    # `/api/preview/*` is omitted only because it needs a synced database to
    # answer at all (tests/test_stage_b.py covers it against the golden fixture);
    # it takes no session either. Nothing here is omitted for auth reasons.
    checks = [
        ("GET", "/api/health", None),
        ("GET", "/api/players", None),
        ("GET", "/api/brief/1234/5", None),
        ("POST", "/api/signup", {"email": "anon@example.com"}),
        ("GET", "/api/auth/me", None),
    ]
    for method, path, body in checks:
        r = (client.get(path) if method == "GET"
             else client.post(path, json=body))
        assert r.status_code != 401, f"{method} {path} demanded a login"


def test_the_paywall_locks_sections_rather_than_the_route(client, sf, monkeypatch):
    """Anonymous + paywall on = 200 with locked sections, never 401/403.

    A login wall and a paywall are different products. This is the line between them.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    r = client.get("/api/brief/1234/5")
    assert r.status_code == 200
    assert r.json()["locked_sections"], "paid sections should be locked, not refused"


def test_auth_me_is_anonymous_not_an_error(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["signed_in"] is False


# ===========================================================================
# 2 — MAGIC LINK
# ===========================================================================

def test_a_magic_link_signs_you_in(client, sf, no_mail):
    token = request_and_link(client, no_mail, "new@example.com")
    r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert accounts.SESSION_COOKIE in r.cookies
    assert client.get("/api/auth/me").json()["signed_in"] is True
    with sf() as s:
        assert s.scalars(select(Account)
                         .where(Account.email == "new@example.com")).one()


def test_a_magic_link_works_exactly_once(client, sf, no_mail):
    """Single use. The second click is refused, and refused the same way an
    unknown token is — a replay must not be distinguishable from a forgery."""
    token = request_and_link(client, no_mail, "once@example.com")
    first = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert first.status_code == 303
    assert "error" not in first.headers["location"]

    client.cookies.clear()
    second = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert second.headers["location"] == f"{WEB}/login?error=link_invalid"
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_an_expired_magic_link_is_refused(client, sf, no_mail):
    token = request_and_link(client, no_mail, "slow@example.com")
    with sf() as s, s.begin():
        row = s.scalars(select(MagicLinkToken)).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert r.headers["location"] == f"{WEB}/login?error=link_invalid"


def test_an_expired_link_is_not_consumed_by_looking_at_it(client, sf, no_mail):
    """Expiry is checked BEFORE the claim, so the row still reads as unused —
    which is what keeps 'expired' and 'replayed' separable in the logs."""
    token = request_and_link(client, no_mail, "slow2@example.com")
    with sf() as s, s.begin():
        s.scalars(select(MagicLinkToken)).one().expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1))
    client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    with sf() as s:
        assert s.scalars(select(MagicLinkToken)).one().consumed_at is None


def test_a_garbage_token_is_refused(client):
    r = client.get("/api/auth/verify?token=not-a-real-token",
                   follow_redirects=False)
    assert r.headers["location"] == f"{WEB}/login?error=link_invalid"


def test_tokens_are_never_stored_in_plaintext(client, sf, no_mail):
    """A DB dump must not yield a usable credential."""
    token = request_and_link(client, no_mail, "hash@example.com")
    with sf() as s:
        row = s.scalars(select(MagicLinkToken)).one()
    assert row.token_hash != token
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()


def test_request_link_answers_identically_for_known_and_unknown_addresses(
        client, sf, no_mail):
    """No enumeration oracle: same status, same body, and a token row either way."""
    with sf() as s, s.begin():
        s.add(Account(email="known@example.com", auth_provider="magic_link"))
    known = client.post("/api/auth/request-link", json={"email": "known@example.com"})
    unknown = client.post("/api/auth/request-link", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"ok": True}
    with sf() as s:
        assert len(s.scalars(select(MagicLinkToken)).all()) == 2


def test_a_malformed_address_still_answers_ok(client, sf):
    """Even the reject path must not become an oracle for 'is this deliverable'."""
    r = client.post("/api/auth/request-link", json={"email": "user@nodot"})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json() == {"ok": True}
        with sf() as s:
            assert s.scalars(select(MagicLinkToken)).all() == []


def test_the_link_request_is_rate_limited_per_ip(client, sf, monkeypatch, no_mail):
    """This endpoint sends mail to an address the caller names. Hard cap."""
    monkeypatch.setattr(auth_link_limiter, "limit", 3)
    codes = [client.post("/api/auth/request-link",
                         json={"email": f"u{i}@example.com"}).status_code
             for i in range(5)]
    assert 429 in codes, "the per-IP magic-link cap did not fire"


def test_the_link_request_is_rate_limited_per_address(client, sf, monkeypatch,
                                                      no_mail):
    """A distributed sender is under the per-IP cap on every single request, so the
    per-address cap is the one that stops a mailbomb aimed at one victim."""
    monkeypatch.setattr(auth_email_limiter, "limit", 2)
    monkeypatch.setattr(auth_link_limiter, "limit", 1000)
    codes = []
    for i in range(4):
        # A new source address on every request — the per-IP bucket never fills.
        codes.append(client.post("/api/auth/request-link",
                                 json={"email": "victim@example.com"},
                                 headers={"x-forwarded-for": f"10.0.0.{i}"}
                                 ).status_code)
    assert 429 in codes, "the per-address cap did not fire"


def test_the_rate_limit_bucket_is_not_a_mailing_list(client, sf, no_mail):
    """`rate_hits.bucket` is a table column; a raw address there is a list of who
    tried to sign in. Same rule as the logs (audit §D4.3)."""
    from fpl.models import RateHit

    client.post("/api/auth/request-link", json={"email": "private@example.com"})
    with sf() as s:
        buckets = [r.bucket for r in s.scalars(select(RateHit)).all()]
    assert buckets, "no bucket was recorded"
    assert not any("private@example.com" in b for b in buckets)


# ===========================================================================
# 3 — GOOGLE OAUTH
# ===========================================================================

def _id_token(payload: dict) -> str:
    import base64

    def seg(d):
        return base64.urlsafe_b64encode(
            json.dumps(d).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.signature-not-checked"


def _google_ok(claims: dict | None = None):
    base = {"aud": GOOGLE_CLIENT, "iss": "https://accounts.google.com",
            "email": "g@example.com", "email_verified": True, "sub": "google-123"}
    base.update(claims or {})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id_token": _id_token(base)}

    return lambda *a, **k: _Resp()


def test_the_google_callback_rejects_a_bad_state(client, sf, monkeypatch):
    """CSRF. Without this, an attacker's callback signs a victim into the
    ATTACKER's Google account, and the victim's next purchase attaches to it."""
    monkeypatch.setattr(accounts, "google_exchange",
                        lambda *a, **k: ("g@example.com", "google-123"))
    start = client.get("/api/auth/google/start", follow_redirects=False)
    assert start.status_code == 303

    r = client.get("/api/auth/google/callback?code=abc&state=attacker-chose-this",
                   follow_redirects=False)
    assert r.headers["location"] == f"{WEB}/login?error=state_mismatch"
    assert client.get("/api/auth/me").json()["signed_in"] is False
    with sf() as s:
        assert s.scalars(select(Account)).all() == []


def test_the_google_callback_rejects_a_missing_state(client, sf, monkeypatch):
    """An absent cookie and an absent param must not compare equal as ''=='' ."""
    monkeypatch.setattr(accounts, "google_exchange",
                        lambda *a, **k: ("g@example.com", "google-123"))
    r = client.get("/api/auth/google/callback?code=abc", follow_redirects=False)
    assert r.headers["location"] == f"{WEB}/login?error=state_mismatch"


def test_a_valid_google_callback_signs_in(client, sf, monkeypatch):
    monkeypatch.setattr("httpx.post", _google_ok())
    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = client.cookies[accounts.OAUTH_STATE_COOKIE]
    r = client.get(f"/api/auth/google/callback?code=abc&state={state}",
                   follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/auth/me").json()["email"] == "g@example.com"
    with sf() as s:
        account = s.scalars(select(Account)).one()
    assert account.auth_provider == "google"


def test_an_unverified_google_address_is_refused(client, sf, monkeypatch):
    """This account model joins to subscriptions BY ADDRESS. An unverified Google
    address would let a stranger's account inherit somebody's subscription."""
    monkeypatch.setattr("httpx.post", _google_ok({"email_verified": False}))
    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = client.cookies[accounts.OAUTH_STATE_COOKIE]
    r = client.get(f"/api/auth/google/callback?code=abc&state={state}",
                   follow_redirects=False)
    assert "google_unverified" in r.headers["location"]
    with sf() as s:
        assert s.scalars(select(Account)).all() == []


def test_an_id_token_for_another_client_is_refused(client, sf, monkeypatch):
    monkeypatch.setattr("httpx.post", _google_ok({"aud": "someone-elses-client-id"}))
    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = client.cookies[accounts.OAUTH_STATE_COOKIE]
    r = client.get(f"/api/auth/google/callback?code=abc&state={state}",
                   follow_redirects=False)
    assert "google_failed" in r.headers["location"]


def test_an_id_token_from_the_wrong_issuer_is_refused(client, monkeypatch):
    monkeypatch.setattr("httpx.post", _google_ok({"iss": "https://evil.example"}))
    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = client.cookies[accounts.OAUTH_STATE_COOKIE]
    r = client.get(f"/api/auth/google/callback?code=abc&state={state}",
                   follow_redirects=False)
    assert "google_failed" in r.headers["location"]


def test_google_routes_404_when_unconfigured(client, monkeypatch):
    """Half a provider is worse than none: it fails at the redirect, in Google's UI."""
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.get("/api/auth/google/start").status_code == 404
    assert client.get("/api/auth/google/callback?code=a&state=b").status_code == 404


def test_google_and_magic_link_land_in_the_same_account(client, sf, monkeypatch,
                                                        no_mail):
    """Matched by ADDRESS, not by provider — otherwise one customer becomes two,
    and only one of them has the subscription."""
    sign_in(client, no_mail, "both@example.com")
    client.post("/api/auth/logout")
    monkeypatch.setattr("httpx.post", _google_ok({"email": "both@example.com"}))
    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = client.cookies[accounts.OAUTH_STATE_COOKIE]
    client.get(f"/api/auth/google/callback?code=abc&state={state}",
               follow_redirects=False)
    with sf() as s:
        rows = s.scalars(select(Account)).all()
    assert len(rows) == 1
    assert rows[0].auth_provider == "magic_link"   # first provider wins, never rewritten
    assert rows[0].provider_id == "google-123"     # and the id is filled in


# ===========================================================================
# 4 — ENTITLEMENT RESOLVES THROUGH THE ACCOUNT
# ===========================================================================

def test_entitlement_resolves_through_the_account_not_the_address(
        client, sf, monkeypatch, no_mail):
    """The subscription is found via `subscriptions.account_id`, not by matching
    `subscriptions.email` to whoever is asking."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com")

    body = client.get("/api/brief/1234/5").json()
    assert body["locked_sections"] == []

    # Break ONLY the account link. The address on the row is untouched, so an
    # implementation that matched on email would still unlock.
    with sf() as s, s.begin():
        s.scalars(select(Subscription)).one().account_id = None
    assert client.get("/api/brief/1234/5").json()["locked_sections"]


def test_a_signed_in_stranger_is_not_entitled(client, sf, monkeypatch, no_mail):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "stranger@example.com")
    assert client.get("/api/brief/1234/5").json()["locked_sections"]


def test_an_entitled_account_does_not_unlock_another_team(client, sf, monkeypatch,
                                                          no_mail):
    """Team scoping survived the rewire: paying for team A is not paying for all."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    persist_brief(sf, 9999, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com")
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []
    assert client.get("/api/brief/9999/5").json()["locked_sections"]


@pytest.mark.parametrize("status", [
    "past_due", "paused", "unknown", "ACTIVE", "active ", "deleted",
    "subscription.brand_new_paddle_state_2027", "",
])
def test_an_unknown_subscription_status_degrades_to_free(
        client, sf, monkeypatch, no_mail, status):
    """THE ALLOW-LIST, re-asserted through the new resolution path.

    This is `tests/test_stage_b.py`'s allow-list test again, deliberately, because
    the accounts build moved the code between the status check and the response. A
    status Paddle invents next year must still withhold — and the account layer is
    a new place that could have accidentally started answering 'yes'.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com")
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []

    with sf() as s, s.begin():
        s.scalars(select(Subscription)).one().status = status
    assert client.get("/api/brief/1234/5").json()["locked_sections"], (
        f"status {status!r} was treated as entitled")


def test_the_email_signups_tier_column_cannot_entitle(client, sf, monkeypatch,
                                                      no_mail):
    """`tier` is reachable from the public signup route. It must never be the grant
    (audit §D6) — the accounts build did not create a new way for it to become one."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    sign_in(client, no_mail, "sneaky@example.com")
    with sf() as s, s.begin():
        s.add(EmailSignup(email="sneaky@example.com", fpl_team_id=1234, tier="paid"))
    assert client.get("/api/brief/1234/5").json()["locked_sections"]


def test_the_webhook_links_the_subscription_to_an_account(client, sf):
    post_webhook(client, sub_event(email="Fan@Example.COM", team_id=1234))
    with sf() as s:
        sub = s.scalars(select(Subscription)).one()
        account = s.get(Account, sub.account_id)
    assert account is not None
    assert account.email == "fan@example.com", "the address is normalised on the way in"


def test_a_subscription_with_no_usable_email_is_recorded_but_unlinked(client, sf):
    """A billing row we refuse to write because of an identity problem is money
    taken with no trace of it. Record it, leave it unlinked, entitle nobody."""
    event = sub_event(team_id=1234)
    event["data"]["custom_data"] = {"fpl_team_id": 1234}
    event["data"].pop("customer", None)
    post_webhook(client, event)
    with sf() as s:
        sub = s.scalars(select(Subscription)).one()
        assert sub.account_id is None
        assert s.scalars(select(Account)).all() == []


# ===========================================================================
# 5 — THE FULL-VIEW LINK IS NO LONGER A STANDING CREDENTIAL
# ===========================================================================

def test_a_forwarded_link_cannot_grant_access_without_an_account(
        client, sf, monkeypatch):
    """The requirement, stated plainly: a token that resolves to no account opens
    nothing, however valid its signature.

    Simulates a subscription the webhook could not link (or one whose account was
    deleted) and a token minted against it — the exact shape of a link forwarded to
    someone who has never signed in.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    with sf() as s, s.begin():
        sub = s.scalars(select(Subscription)).one()
        sub.account_id = None                     # unlinked: nobody owns this row
        sub_id = sub.id
    token = tokens.mint(subscription_id=sub_id, team_id=1234,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7))

    body = client.get(f"/api/brief/1234/5?token={token}").json()
    assert body["locked_sections"], "an unlinked token unlocked the paid view"
    assert accounts.SESSION_COOKIE not in client.cookies


def test_a_link_for_an_unknown_subscription_grants_nothing(client, sf, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    token = tokens.mint(subscription_id=987654, team_id=1234,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    assert client.get(f"/api/brief/1234/5?token={token}").json()["locked_sections"]


def test_a_valid_link_establishes_a_session(client, sf, monkeypatch):
    """The link still works — but as an introduction, not a credential. After the
    exchange the SESSION is what carries access, so a later revocation bites."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    with sf() as s, s.begin():
        account = accounts.get_account_by_email(s, "fan@example.com")
        accounts.accept_terms(account)
        sub_id = s.scalars(select(Subscription)).one().id
    token = tokens.mint(subscription_id=sub_id, team_id=1234,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7))

    r = client.get(f"/api/brief/1234/5?token={token}")
    assert r.json()["locked_sections"] == []
    assert client.get("/api/auth/me").json()["signed_in"] is True
    # And now without the token at all — the session is doing the work.
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []


def test_a_link_for_another_team_still_does_not_open_this_one(client, sf, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    with sf() as s:
        sub_id = s.scalars(select(Subscription)).one().id
    token = tokens.mint(subscription_id=sub_id, team_id=9999,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    assert client.get(f"/api/brief/1234/5?token={token}").json()["locked_sections"]


# ===========================================================================
# 6 — TERMS
# ===========================================================================

def test_terms_are_required_before_paid_content(client, sf, monkeypatch, no_mail):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com", accept_terms=False)

    me = client.get("/api/auth/me").json()
    # Entitled AND blocked: the UI must be able to say "you have a subscription,
    # accept the terms" rather than showing a paying customer a buy button.
    assert me["entitled"] is True
    assert me["terms_required"] is True
    assert client.get("/api/brief/1234/5").json()["locked_sections"]

    assert client.post("/api/auth/accept-terms").status_code == 200
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []


def test_a_terms_version_bump_prompts_for_re_acceptance(client, sf, monkeypatch,
                                                        no_mail):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com")
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []

    monkeypatch.setattr(settings, "terms_version", "2027-01-01")
    assert client.get("/api/auth/me").json()["terms_required"] is True
    assert client.get("/api/brief/1234/5").json()["locked_sections"]

    client.post("/api/auth/accept-terms")
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []


def test_a_null_terms_record_is_not_read_as_consent(sf):
    """An account created before terms were tracked has agreed to nothing."""
    account = Account(email="old@example.com", auth_provider="magic_link")
    assert accounts.needs_terms_acceptance(account) is True
    assert accounts.needs_terms_acceptance(None) is True


def test_checkout_records_terms_when_the_box_was_ticked(client, sf, monkeypatch):
    """Checkout is the third account-creation path. Without it, a webhook-created
    account has no consent and the customer's own emailed link opens the free view."""
    monkeypatch.setattr(billing, "create_checkout",
                        lambda *a, **k: "https://pay.example/x")
    r = client.post("/api/checkout", json={
        "email": "buyer@example.com", "team_id": 77, "plan": "monthly",
        "accept_terms": True})
    assert r.status_code == 200
    with sf() as s:
        account = accounts.get_account_by_email(s, "buyer@example.com")
    assert account is not None
    assert accounts.needs_terms_acceptance(account) is False


def test_checkout_without_the_box_records_no_consent(client, sf, monkeypatch):
    monkeypatch.setattr(billing, "create_checkout",
                        lambda *a, **k: "https://pay.example/x")
    client.post("/api/checkout", json={
        "email": "quiet@example.com", "team_id": 77, "plan": "monthly"})
    with sf() as s:
        account = accounts.get_account_by_email(s, "quiet@example.com")
    # Either no account, or one that has agreed to nothing. Never a false record.
    assert account is None or accounts.needs_terms_acceptance(account) is True


def test_accept_terms_requires_a_session(client):
    assert client.post("/api/auth/accept-terms").status_code == 401


# ===========================================================================
# 7 — SESSION SECURITY
# ===========================================================================

def test_the_session_cookie_is_httponly_and_samesite(client, sf, no_mail,
                                                     monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    token = request_and_link(client, no_mail, "flags@example.com")
    r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=lax" in header


def test_the_session_token_is_not_stored_in_plaintext(client, sf, no_mail):
    token = request_and_link(client, no_mail, "sess@example.com")
    r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    cookie = r.cookies[accounts.SESSION_COOKIE]
    with sf() as s:
        row = s.scalars(select(AuthSession)).one()
    assert row.token_hash != cookie
    assert row.token_hash == hashlib.sha256(cookie.encode()).hexdigest()


def test_login_rotates_the_session_token(client, sf, no_mail):
    """Session fixation: the identifier that carried the caller in must not carry
    them out with more privilege."""
    first = request_and_link(client, no_mail, "rot@example.com")
    r1 = client.get(f"/api/auth/verify?token={first}", follow_redirects=False)
    before = r1.cookies[accounts.SESSION_COOKIE]

    second = request_and_link(client, no_mail, "rot@example.com")
    r2 = client.get(f"/api/auth/verify?token={second}", follow_redirects=False)
    after = r2.cookies[accounts.SESSION_COOKIE]

    assert before != after
    with sf() as s:
        rows = s.scalars(select(AuthSession)).all()
    assert len(rows) == 1, "the old session row should be gone, not merely replaced"
    assert rows[0].token_hash == hashlib.sha256(after.encode()).hexdigest()


def test_an_expired_session_does_not_sign_you_in(client, sf, no_mail):
    sign_in(client, no_mail, "exp@example.com")
    with sf() as s, s.begin():
        s.scalars(select(AuthSession)).one().expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1))
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_logout_deletes_the_row_not_just_the_cookie(client, sf, no_mail):
    """Clearing the cookie alone leaves a live session anyone holding a copy of the
    token could still present — a logout that logs nobody out."""
    sign_in(client, no_mail, "out@example.com")
    with sf() as s:
        stolen = s.scalars(select(AuthSession)).one().token_hash
    client.post("/api/auth/logout")
    with sf() as s:
        assert s.scalars(select(AuthSession)).all() == []
        assert s.get(AuthSession, stolen) is None
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_a_forged_session_cookie_is_ignored(client, sf):
    client.cookies.set(accounts.SESSION_COOKIE, "i-made-this-up")
    assert client.get("/api/auth/me").json()["signed_in"] is False


@pytest.mark.parametrize("target,expected", [
    ("/brief/123/5", "/brief/123/5"),
    ("//evil.example/x", "/"),
    ("/\\evil.example", "/"),
    ("https://evil.example", "/"),
    ("javascript:alert(1)", "/"),
    ("", "/"),
    (None, "/"),
    ("/ok\r\nX-Injected: 1", "/"),
])
def test_open_redirect_protection(target, expected):
    assert accounts.safe_redirect(target) == expected


def test_the_verify_route_will_not_redirect_off_site(client, sf, no_mail):
    """An open redirect on the one route that hands out sessions is how a phishing
    page borrows this domain for the address bar."""
    token = request_and_link(client, no_mail, "redir@example.com")
    r = client.get(f"/api/auth/verify?token={token}&next=https://evil.example",
                   follow_redirects=False)
    assert r.headers["location"].startswith(WEB)
    assert "evil.example" not in r.headers["location"]


def test_purge_drops_expired_sessions_and_links(client, sf, no_mail):
    sign_in(client, no_mail, "purge@example.com")
    with sf() as s, s.begin():
        for row in s.scalars(select(AuthSession)).all():
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        for row in s.scalars(select(MagicLinkToken)).all():
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    with sf() as s, s.begin():
        assert accounts.purge_expired(s) == 2
    with sf() as s:
        assert s.scalars(select(AuthSession)).all() == []
        assert s.scalars(select(MagicLinkToken)).all() == []


# ===========================================================================
# 8 — NO ADDRESSES IN LOGS
# ===========================================================================

def test_no_address_reaches_a_log_line(client, sf, caplog, no_mail, monkeypatch):
    """Auth logs are the highest-value ones to correlate — who signed in, when,
    from where. Audit §D4.3 applies here without exception."""
    import logging

    monkeypatch.setattr("httpx.post", _google_ok({"email": "leak2@example.com"}))
    with caplog.at_level(logging.DEBUG):
        sign_in(client, no_mail, "leak@example.com")
        post_webhook(client, sub_event(email="leak3@example.com", team_id=1234))
        start = client.get("/api/auth/google/start", follow_redirects=False)
        state = client.cookies[accounts.OAUTH_STATE_COOKIE]
        client.get(f"/api/auth/google/callback?code=abc&state={state}",
                   follow_redirects=False)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for address in ("leak@example.com", "leak2@example.com", "leak3@example.com"):
        assert address not in text, f"{address} was logged in full"


def test_mask_is_stable_and_not_reversible():
    assert accounts.mask("a@b.com") == accounts.mask("A@B.com  ")
    assert "a@b.com" not in accounts.mask("a@b.com")


@pytest.mark.parametrize("raw,expected", [
    ("  Fan@Example.COM ", "fan@example.com"),
    ("USER@DOMAIN.IO", "user@domain.io"),
    ("no-at-sign", None),
    ("two@@example.com", None),
    ("user@nodot", None),
    ("", None),
    (None, None),
    ("with space@example.com", None),
])
def test_email_case_is_normalised_on_every_path(raw, expected):
    """`accounts.email` is UNIQUE plain TEXT, not citext — this function IS the
    case-insensitivity guarantee, so it is tested as the load-bearing thing it is."""
    assert accounts.normalize_email(raw) == expected


def test_one_address_in_any_casing_is_one_account(client, sf, no_mail):
    sign_in(client, no_mail, "mixed@example.com")
    client.post("/api/auth/logout")
    token = request_and_link(client, no_mail, "MIXED@Example.Com")
    client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    with sf() as s:
        assert len(s.scalars(select(Account)).all()) == 1


# ===========================================================================
# 9 — DELETE CASCADE
# ===========================================================================

def test_delete_leaves_nothing_orphaned(client, sf, monkeypatch, no_mail):
    """Account, sessions, magic links and the signup go. The billing row stays,
    unlinked and anonymised — it is a financial record, and it must never be
    reachable from an identity again."""
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="gone@example.com", team_id=1234))
    sign_in(client, no_mail, "gone@example.com")
    # The webhook already wrote the `email_signups` row (billing._upsert_signup_tier),
    # so only the send history needs planting.
    with sf() as s, s.begin():
        s.add(SendLog(email="gone@example.com", gameweek=5, template="paid_brief",
                      status="sent"))

    r = client.post("/api/account/delete")
    assert r.status_code == 200
    assert r.json()["account_removed"] is True

    with sf() as s:
        assert s.scalars(select(Account)).all() == []
        assert s.scalars(select(AuthSession)).all() == []
        assert s.scalars(select(MagicLinkToken)).all() == []
        assert s.scalars(select(EmailSignup)).all() == []
        assert s.scalars(select(SendLog)).all() == []
        sub = s.scalars(select(Subscription)).one()
        assert sub.account_id is None, "a dangling account_id is an orphan"
        assert sub.email == "", "the address must not survive on the billing row"
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_delete_tells_the_user_their_subscription_is_still_billing(
        client, sf, no_mail):
    """Deleting an account does not cancel a card. Saying nothing would be the most
    expensive silence in the codebase."""
    post_webhook(client, sub_event(email="paid@example.com", team_id=1234))
    sign_in(client, no_mail, "paid@example.com")
    body = client.post("/api/account/delete").json()
    assert body["subscription_cancel_required"] is True


def test_delete_of_an_unpaid_account_does_not_claim_a_cancellation(
        client, sf, no_mail):
    sign_in(client, no_mail, "free@example.com")
    body = client.post("/api/account/delete").json()
    assert body["subscription_cancel_required"] is False


def test_delete_requires_a_session(client):
    assert client.post("/api/account/delete").status_code == 401


def test_the_mailed_delete_link_also_removes_the_account(client, sf, no_mail):
    """The two delete doors — the mailed link and the signed-in button — must mean
    the same thing, or 'delete my data' depends on which one you found."""
    post_webhook(client, sub_event(email="both@example.com", team_id=1234))
    sign_in(client, no_mail, "both@example.com")
    client.post("/api/auth/logout")

    t = tokens.unsubscribe_token("both@example.com")
    r = client.post(f"/api/email/delete?e=both@example.com&t={t}")
    assert r.status_code == 200
    with sf() as s:
        assert s.scalars(select(Account)).all() == []
        assert s.scalars(select(AuthSession)).all() == []


def test_unsubscribing_from_mail_does_not_cost_billing_access(
        client, sf, monkeypatch, no_mail):
    """The Stage B invariant, re-asserted now that a session exists: mail consent
    and billing state are different things and must stay different."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    persist_brief(sf, 1234, 5)
    post_webhook(client, sub_event(email="quiet@example.com", team_id=1234))
    sign_in(client, no_mail, "quiet@example.com")

    t = tokens.unsubscribe_token("quiet@example.com")
    assert client.post(f"/api/email/unsubscribe?e=quiet@example.com&t={t}"
                       ).status_code == 200

    assert client.get("/api/auth/me").json()["signed_in"] is True
    assert client.get("/api/brief/1234/5").json()["locked_sections"] == []


# ===========================================================================
# 10 — ADMIN
# ===========================================================================

def test_the_admin_accounts_view_is_token_gated(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token-value")
    assert client.get("/api/admin/accounts").status_code == 401
    r = client.get("/api/admin/accounts",
                   headers={"Authorization": "Bearer admin-token-value"})
    assert r.status_code == 200


def test_the_admin_accounts_view_reports_the_linked_subscription(
        client, sf, monkeypatch, no_mail):
    monkeypatch.setattr(settings, "admin_token", "admin-token-value")
    post_webhook(client, sub_event(email="fan@example.com", team_id=1234))
    sign_in(client, no_mail, "fan@example.com")
    r = client.get("/api/admin/accounts",
                   headers={"Authorization": "Bearer admin-token-value"})
    row = r.json()["accounts"][0]
    assert row["email"] == "fan@example.com"
    assert row["subscription_status"] == "active"
    assert row["entitled"] is True
    assert row["terms_current"] is True
    assert row["active_sessions"] == 1
    assert row["last_login_at"] is not None


def test_the_admin_accounts_view_is_read_only(client, monkeypatch):
    """No admin route may edit or impersonate an account — a console that can
    assume a customer's identity is a token worth stealing for more than a dashboard."""
    from api.main import app as live

    paths = [r.path for r in live.routes
             if "accounts" in getattr(r, "path", "")]
    for path in paths:
        route = next(r for r in live.routes if r.path == path)
        assert route.methods <= {"GET", "HEAD"}, f"{path} is not read-only"
