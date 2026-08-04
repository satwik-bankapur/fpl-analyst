"""Stage B: billing, tokens, paywall, send batch (spec §1.6–1.8, §3, §4).

Structured to mirror docs/AUDIT.md §D, because that section is what this code was
built to satisfy — a reviewer should be able to read a requirement there and find
the test that pins it here by name.

Everything is offline. No Paddle account, no Resend key, no network: signatures are
computed with a test secret using Paddle's real scheme, and the mail provider is
injected. That is the point — a test that needed a sandbox account would not run in
CI, and these are the assertions that must run on every commit.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.models import (
    Base, Brief, EmailSignup, SendLog, Subscription, WebhookEvent,
)
from fpl.sync import sync_fpl
from graph.deps import Deps

from api import billing, mail, send, service, tokens
from api.config import settings
from api.main import app
from api.ratelimit import (
    DbRateLimiter, admin_limiter, db_limiter, global_limiter, limiter,
    request_limiter,
)
from api.service import get_deps

ASSETS = Path(__file__).parent / "assets"

# 43 chars, the shape `secrets.token_urlsafe(32)` produces.
TEST_SECRET = "test-secret-with-enough-entropy-for-hs256-xx"
WEBHOOK_SECRET = "pdl_ntfset_test_0123456789abcdef"
PRICE_MONTHLY = "pri_monthly_01"
PRICE_SEASON = "pri_season_01"


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def sf():
    """Migrated-but-empty DB. Enough for everything that is not brief-shaped."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture
def golden_sf():
    """The frozen 2025-26 season through the real pipeline — for brief/preview tests."""
    class _Client:
        def get_bootstrap(self):
            return json.loads((ASSETS / "bootstrap_2025-26.json").read_text("utf-8"))

        def get_fixtures(self):
            return json.loads((ASSETS / "fixtures_2025-26.json").read_text("utf-8"))

        def close(self):
            pass

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sync_fpl(client=_Client(), session_factory=SF)
    yield SF
    engine.dispose()


@pytest.fixture(autouse=True)
def stage_b_settings(monkeypatch):
    """Configured Stage B, paywall OFF. Individual tests turn on what they test."""
    monkeypatch.setattr(settings, "brief_token_secret", TEST_SECRET)
    monkeypatch.setattr(settings, "brief_token_secret_prev", "")
    monkeypatch.setattr(settings, "paddle_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "paddle_price_monthly", PRICE_MONTHLY)
    monkeypatch.setattr(settings, "paddle_price_season", PRICE_SEASON)
    monkeypatch.setattr(settings, "paywall_enabled", False)
    monkeypatch.setattr(settings, "send_enabled", True)
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "public_web_url", "https://example.test")
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


# --- Paddle payload + signature helpers -------------------------------------

def sign(body: bytes, *, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    """Paddle's real scheme: HMAC-SHA256 over f"{ts}:{body}", header `ts=..;h1=..`."""
    ts = ts if ts is not None else int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256)
    return f"ts={ts};h1={mac.hexdigest()}"


def sub_event(event_type="subscription.created", *, event_id="evt_001",
              sub_id="sub_001", status=None, price_id=PRICE_MONTHLY,
              email="fan@example.com", team_id=1234, period_end=None) -> dict:
    if period_end is None:
        period_end = (datetime.now(timezone.utc) + timedelta(days=30))
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "id": sub_id,
            "status": status,
            "customer_id": "ctm_001",
            "items": [{"price": {"id": price_id}}],
            "custom_data": {"fpl_team_id": team_id, "email": email},
            "current_billing_period": {
                "ends_at": period_end.isoformat().replace("+00:00", "Z")},
        },
    }


def txn_event(*, event_id="evt_txn", txn_id="txn_001", price_id=PRICE_SEASON,
              email="pass@example.com", team_id=99, subscription_id=None) -> dict:
    return {
        "event_id": event_id,
        "event_type": "transaction.completed",
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "id": txn_id,
            "subscription_id": subscription_id,
            "customer_id": "ctm_002",
            "items": [{"price": {"id": price_id}}],
            "custom_data": {"fpl_team_id": team_id, "email": email},
        },
    }


def post_webhook(client, payload: dict, *, secret=WEBHOOK_SECRET, ts=None,
                 signature=None):
    body = json.dumps(payload).encode()
    header = signature if signature is not None else sign(body, secret=secret, ts=ts)
    return client.post("/api/webhooks/paddle", content=body,
                       headers={"Paddle-Signature": header,
                                "Content-Type": "application/json"})


# ===========================================================================
# D1 — webhook: verify before parse, timing-safe, replay, idempotency
# ===========================================================================

def test_a_valid_signature_is_accepted(client, sf):
    r = post_webhook(client, sub_event())
    assert r.status_code == 200, r.text
    with sf() as s:
        row = s.scalars(select(Subscription)).one()
    assert row.provider_subscription_id == "sub_001"
    assert row.plan == "monthly" and row.status == "active"


@pytest.mark.parametrize("signature,why", [
    (None, "no header at all"),
    ("", "empty header"),
    ("ts=1;h1=deadbeef", "wrong hmac"),
    ("h1=deadbeef", "no timestamp"),
    ("ts=notanumber;h1=deadbeef", "non-integer timestamp"),
    ("garbage", "unparseable"),
])
def test_an_unverified_webhook_is_401_with_no_detail(client, sf, signature, why):
    body = json.dumps(sub_event()).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Paddle-Signature"] = signature
    r = client.post("/api/webhooks/paddle", content=body, headers=headers)
    assert r.status_code == 401, why
    # No detail: the reason goes to our log, never to the caller. Anything that
    # distinguishes "bad hmac" from "stale ts" is free help for the next attempt.
    assert r.json() == {"detail": {"error": "unauthorized"}}
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0
        assert s.scalar(select(func.count()).select_from(WebhookEvent)) == 0


def test_signature_is_verified_before_the_body_is_parsed(client, monkeypatch):
    """The §D1.1 requirement, asserted on the call ORDER rather than the outcome.

    An unsigned request must never reach json.loads. Testing only the 401 would
    pass even if the handler parsed first and checked afterwards — which is the
    exact bug the requirement exists to prevent.
    """
    parsed: list[bytes] = []
    real = billing.parse_verified
    monkeypatch.setattr(billing, "parse_verified",
                        lambda raw: parsed.append(raw) or real(raw))

    r = client.post("/api/webhooks/paddle",
                    content=json.dumps(sub_event()).encode(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    assert parsed == [], "the body was parsed before the signature was verified"


def test_signature_over_a_tampered_body_fails(client, sf):
    """Sign one payload, send another — the HMAC covers the body, so this dies."""
    honest = json.dumps(sub_event()).encode()
    header = sign(honest)
    tampered = json.dumps(sub_event(price_id=PRICE_SEASON)).encode()
    r = client.post("/api/webhooks/paddle", content=tampered,
                    headers={"Paddle-Signature": header,
                             "Content-Type": "application/json"})
    assert r.status_code == 401
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0


def test_a_replayed_signature_expires(client, sf):
    """The timestamp is INSIDE the signed material, so it cannot be slid forward."""
    stale = int(time.time()) - settings.paddle_replay_window_s - 60
    assert post_webhook(client, sub_event(), ts=stale).status_code == 401
    future = int(time.time()) + settings.paddle_replay_window_s + 60
    assert post_webhook(client, sub_event(), ts=future).status_code == 401
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0


def test_the_webhook_is_closed_when_the_secret_is_unset(client, sf, monkeypatch):
    """Empty PADDLE_WEBHOOK_SECRET closes it — never open-by-misconfiguration."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", "")
    body = json.dumps(sub_event()).encode()
    # Even a signature that is "correct" for the empty secret must be refused.
    r = client.post("/api/webhooks/paddle", content=body,
                    headers={"Paddle-Signature": sign(body, secret=""),
                             "Content-Type": "application/json"})
    assert r.status_code == 401


def test_a_redelivered_event_is_applied_exactly_once(client, sf):
    """Paddle redelivers on any non-2xx. Twice must not grant twice."""
    payload = sub_event(event_id="evt_dup", sub_id="sub_dup")
    first = post_webhook(client, payload)
    second = post_webhook(client, payload)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["duplicate"] is True
    # And a duplicate is a 200, not a 4xx — a 4xx makes Paddle retry forever.
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1
        assert s.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_idempotency_survives_the_pre_check_race(sf):
    """The PK is the mechanism; `already_processed` is only an optimisation.

    Called twice with no pre-check, the second must fail on the webhook_events PK
    rather than applying a second time.
    """
    from sqlalchemy.exc import IntegrityError

    payload = sub_event(event_id="evt_race", sub_id="sub_race")
    with sf() as s, s.begin():
        billing.handle_event(s, payload)
    with pytest.raises(IntegrityError):
        with sf() as s, s.begin():
            billing.handle_event(s, payload)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1


def test_an_unhandled_event_type_is_recorded_and_200(client, sf):
    payload = {"event_id": "evt_x", "event_type": "customer.updated", "data": {}}
    r = post_webhook(client, payload)
    assert r.status_code == 200 and r.json()["action"] == "ignored"
    with sf() as s:
        assert s.get(WebhookEvent, "evt_x") is not None
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0


# ===========================================================================
# D1.5 / D3 — plan and price come from the verified payload only
# ===========================================================================

def test_plan_is_derived_from_the_signed_price_id(client, sf):
    post_webhook(client, sub_event(event_id="e1", sub_id="s1", price_id=PRICE_SEASON))
    with sf() as s:
        assert s.scalars(select(Subscription)).one().plan == "season"


def test_an_unknown_price_id_does_not_become_a_plan(client, sf):
    """A price we never configured maps to no plan — recorded, not guessed."""
    post_webhook(client, sub_event(event_id="e2", sub_id="s2",
                                   price_id="pri_someone_elses_product"))
    with sf() as s:
        assert s.scalars(select(Subscription)).one().plan == "unknown"


def test_the_checkout_request_schema_has_no_price_field(client):
    """§D3 as a schema property: there is nothing for a client to tamper with.

    Asserted on the model rather than by sending a tampered request, because the
    guarantee is "the field does not exist", not "the field is validated".
    """
    from api.routes import CheckoutRequest

    fields = set(CheckoutRequest.model_fields)
    # `accept_terms` joined the model in the accounts build (2026-08-04): checkout
    # is an account-creation path and the box is ticked on the card. It carries no
    # pricing authority — it decides what is recorded about CONSENT, not about money.
    assert fields == {"email", "team_id", "plan", "accept_terms"}
    for forbidden in ("price", "price_id", "amount", "currency", "discount"):
        assert forbidden not in fields


def test_extra_price_fields_in_a_checkout_body_are_ignored(client, monkeypatch, sf):
    """Belt and braces: a client that invents a price field changes nothing."""
    seen = {}

    def fake_create(plan, email, team_id, post=None):
        seen.update(plan=plan, email=email, team_id=team_id)
        return "https://sandbox-pay.example/checkout/abc"

    monkeypatch.setattr(billing, "create_checkout", fake_create)
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    r = client.post("/api/checkout", json={
        "email": "buyer@example.com", "team_id": 77, "plan": "monthly",
        "price_id": "pri_free_please", "amount": 0, "currency": "XXX",
    })
    assert r.status_code == 200
    assert seen == {"plan": "monthly", "email": "buyer@example.com", "team_id": 77}


def test_price_id_lookup_is_server_side(monkeypatch):
    assert billing.price_id_for_plan("monthly") == PRICE_MONTHLY
    assert billing.price_id_for_plan("season") == PRICE_SEASON
    with pytest.raises(billing.CheckoutError):
        billing.price_id_for_plan("free")
    monkeypatch.setattr(settings, "paddle_price_monthly", "")
    with pytest.raises(billing.CheckoutError):
        billing.price_id_for_plan("monthly")


def test_checkout_failure_is_503_and_leaks_no_provider_detail(client, monkeypatch):
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    monkeypatch.setattr(settings, "paddle_api_key", "")     # not configured
    r = client.post("/api/checkout",
                    json={"email": "a@b.com", "team_id": 5, "plan": "monthly"})
    assert r.status_code == 503
    assert r.json()["detail"] == {"error": "checkout_unavailable"}


# ===========================================================================
# D6 — entitlement is an ALLOW-LIST
# ===========================================================================

def _sub(status: str, *, days: int | None = 30) -> Subscription:
    end = (datetime.now(timezone.utc) + timedelta(days=days)) if days is not None else None
    return Subscription(email="x@y.com", fpl_team_id=1, provider="paddle",
                        provider_subscription_id=f"sub_{status}_{days}",
                        plan="monthly", status=status, current_period_end=end)


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_known_good_statuses_are_entitled(status):
    assert billing.is_entitled(_sub(status)) is True


@pytest.mark.parametrize("status", [
    "past_due",           # Paddle is still retrying; do not serve on an unpaid invoice
    "paused",
    "unknown",
    "",
    # THE load-bearing case: a status Paddle invents after this code was written.
    "subscription.some_future_state",
    "ACTIVE",             # case matters — near-misses must not pass either
    "active ",
])
def test_every_other_status_degrades_to_free(status):
    """The allow-list property. If this test ever needs relaxing, the paywall is
    now a deny-list and the failure mode has flipped to 'everyone reads it free'."""
    assert billing.is_entitled(_sub(status)) is False


def test_the_allow_list_is_the_only_entitled_set():
    """Structural: nothing may be entitled that is not in ENTITLED_STATUSES."""
    assert billing.ENTITLED_STATUSES == frozenset({"active", "trialing"})
    for status in billing.ENTITLED_STATUSES:
        assert billing.is_entitled(_sub(status, days=None)) is True


def test_no_subscription_is_not_entitled():
    assert billing.is_entitled(None) is False


# --- cancellation, refund, chargeback ---------------------------------------

def test_cancellation_keeps_delivery_until_the_period_ends(client, sf):
    """Never a mid-period cutoff — they paid for the period (spec §4)."""
    post_webhook(client, sub_event(event_id="c1", sub_id="sub_c"))
    post_webhook(client, sub_event("subscription.canceled", event_id="c2",
                                   sub_id="sub_c", status="canceled"))
    with sf() as s:
        row = s.scalars(select(Subscription)).one()
    assert row.status == "canceled"
    assert billing.is_entitled(row) is True          # period end still in the future
    # ...and stops the moment it passes.
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert billing.is_entitled(row, now=later) is False


def test_a_refund_arriving_as_cancellation_with_no_period_stops_immediately(sf):
    """A refund/chargeback cancels with the period already gone. No grace."""
    refunded = _sub("canceled", days=None)           # no current_period_end
    assert billing.is_entitled(refunded) is False
    backdated = _sub("canceled", days=-1)            # period ended yesterday
    assert billing.is_entitled(backdated) is False


def test_past_due_does_not_deliver_but_does_not_destroy_the_row(client, sf):
    post_webhook(client, sub_event(event_id="p1", sub_id="sub_p"))
    post_webhook(client, sub_event("subscription.past_due", event_id="p2",
                                   sub_id="sub_p", status="past_due"))
    with sf() as s:
        row = s.scalars(select(Subscription)).one()
    assert row.status == "past_due"
    assert billing.is_entitled(row) is False
    assert row.current_period_end is not None        # recoverable when it clears


def test_a_renewal_extends_the_period_and_revives_delivery(client, sf):
    post_webhook(client, sub_event(event_id="r1", sub_id="sub_r"))
    far = datetime.now(timezone.utc) + timedelta(days=365)
    post_webhook(client, sub_event("subscription.updated", event_id="r2",
                                   sub_id="sub_r", status="active", period_end=far))
    with sf() as s:
        row = s.scalars(select(Subscription)).one()
    assert row.current_period_end.replace(tzinfo=timezone.utc) > \
        datetime.now(timezone.utc) + timedelta(days=300)


def test_an_update_without_a_period_never_shortens_the_existing_one(client, sf):
    post_webhook(client, sub_event(event_id="u1", sub_id="sub_u"))
    with sf() as s:
        before = s.scalars(select(Subscription)).one().current_period_end
    payload = sub_event("subscription.updated", event_id="u2", sub_id="sub_u",
                        status="active")
    payload["data"].pop("current_billing_period")
    post_webhook(client, payload)
    with sf() as s:
        assert s.scalars(select(Subscription)).one().current_period_end == before


# --- the one-time season pass -----------------------------------------------

def test_a_season_pass_transaction_grants_access_to_june(client, sf, monkeypatch):
    monkeypatch.setattr(settings, "season_pass_end_year", 2027)
    r = post_webhook(client, txn_event())
    assert r.status_code == 200 and r.json()["action"] == "season_pass_granted"
    with sf() as s:
        row = s.scalars(select(Subscription)).one()
    assert row.plan == "season" and row.status == "active"
    assert row.current_period_end.year == 2027 and row.current_period_end.month == 6
    assert row.provider_subscription_id.startswith("txn:")
    assert billing.is_entitled(row) is True


def test_an_ordinary_subscription_transaction_does_not_double_grant(client, sf):
    """transaction.completed also fires for subscription payments. Acting on those
    would create a second entitlement row alongside the subscription's own."""
    post_webhook(client, sub_event(event_id="d1", sub_id="sub_d"))
    r = post_webhook(client, txn_event(event_id="d2", price_id=PRICE_MONTHLY,
                                       subscription_id="sub_d"))
    assert r.json()["action"] == "ignored"
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1


def test_a_transaction_for_an_unknown_price_is_ignored(client, sf):
    r = post_webhook(client, txn_event(event_id="d3", price_id="pri_nope"))
    assert r.json()["action"] == "ignored"
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0


# --- entitlement is never taken from email_signups.tier ---------------------

def test_the_public_signup_route_cannot_grant_entitlement(client, sf, monkeypatch):
    """`tier` is reachable from an unauthenticated route; entitlement is not.

    The audit's H4 was this table being clobbered from the public surface. If
    entitlement ever reads `tier`, POST /api/signup becomes a free upgrade button.
    """
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "sneaky@example.com", "team_id": 4242})
    with sf() as s, s.begin():
        s.get(EmailSignup, "sneaky@example.com").tier = "paid"
    with sf() as s:
        assert billing.entitled_for_team(s, 4242) is False


# ===========================================================================
# D2 — JWT: pinned algorithm, enforced exp, secret entropy, rotation
# ===========================================================================

def _future():
    return datetime.now(timezone.utc) + timedelta(days=14)


def test_mint_and_verify_roundtrip():
    token = tokens.mint(subscription_id=7, team_id=1234, expires_at=_future())
    claims = tokens.verify(token)
    assert claims["team_id"] == 1234 and claims["sub"] == "7"
    assert "exp" in claims and "iat" in claims
    # Blast radius: exactly these four claims, nothing that generalises.
    assert set(claims) == {"sub", "team_id", "exp", "iat"}


def _retamper(token: str, *, header: dict | None = None, claims: dict | None = None,
              signature: str | None = None) -> str:
    import base64

    def enc(obj):
        raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def dec(part):
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    h, c, s = token.split(".")
    h = enc(header) if header is not None else h
    c = enc({**dec(c), **claims}) if claims is not None else c
    s = signature if signature is not None else s
    return f"{h}.{c}.{s}"


def test_alg_none_is_rejected():
    """The classic. Not merely 'rejected' — unrepresentable, since nothing dispatches
    on `alg`. The header check is what makes the refusal legible."""
    token = tokens.mint(subscription_id=1, team_id=1, expires_at=_future())
    forged = _retamper(token, header={"alg": "none", "typ": "JWT"}, signature="")
    with pytest.raises(tokens.TokenError, match="alg"):
        tokens.verify(forged)


def test_algorithm_confusion_is_rejected():
    """An RS256 header must not persuade the verifier to change algorithms."""
    token = tokens.mint(subscription_id=1, team_id=1, expires_at=_future())
    forged = _retamper(token, header={"alg": "RS256", "typ": "JWT"})
    with pytest.raises(tokens.TokenError, match="alg"):
        tokens.verify(forged)


def test_tampered_claims_are_rejected():
    """Rewriting team_id must invalidate the signature — the whole point."""
    token = tokens.mint(subscription_id=1, team_id=1, expires_at=_future())
    with pytest.raises(tokens.TokenError, match="signature"):
        tokens.verify(_retamper(token, claims={"team_id": 999}))


def test_an_expired_token_is_rejected():
    token = tokens.mint(subscription_id=1, team_id=1,
                        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.verify(token)


def test_a_token_without_exp_is_rejected():
    """Absent must not mean forever — that is how a leaked link outlives the sub."""
    token = tokens.mint(subscription_id=1, team_id=1, expires_at=_future())
    import base64

    h, c, _ = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)))
    claims.pop("exp")
    raw = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    c2 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = tokens._sign(f"{h}.{c2}".encode(), TEST_SECRET)
    with pytest.raises(tokens.TokenError, match="exp"):
        tokens.verify(f"{h}.{c2}.{sig}")


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    token = tokens.mint(subscription_id=1, team_id=1, expires_at=_future())
    monkeypatch.setattr(settings, "brief_token_secret",
                        "a-completely-different-secret-of-good-length")
    with pytest.raises(tokens.TokenError, match="signature"):
        tokens.verify(token)


@pytest.mark.parametrize("bad", ["", "abc", "a.b", "a.b.c.d", "...", "x" * 40])
def test_malformed_tokens_never_raise_out_of_the_route_helper(bad):
    assert tokens.team_id_if_valid(bad) is None


def test_rotation_accepts_the_previous_secret_but_signs_with_the_new(monkeypatch):
    """Without this overlap, rotating the secret dead-links every email already sent."""
    old = tokens.mint(subscription_id=1, team_id=42, expires_at=_future())
    monkeypatch.setattr(settings, "brief_token_secret_prev", TEST_SECRET)
    monkeypatch.setattr(settings, "brief_token_secret",
                        "the-new-secret-with-plenty-of-entropy-here")
    assert tokens.verify(old)["team_id"] == 42            # old link still opens
    fresh = tokens.mint(subscription_id=1, team_id=42, expires_at=_future())
    monkeypatch.setattr(settings, "brief_token_secret_prev", "")
    assert tokens.verify(fresh)["team_id"] == 42          # new one needs no fallback


@pytest.mark.parametrize("secret,why", [
    ("", "empty"),
    ("short", "5 chars"),
    ("x" * 31, "one char under the floor"),
])
def test_a_weak_signing_secret_fails_closed(monkeypatch, secret, why):
    monkeypatch.setattr(settings, "brief_token_secret", secret)
    with pytest.raises(RuntimeError, match="BRIEF_TOKEN_SECRET"):
        tokens.assert_secret_usable()
    with pytest.raises(RuntimeError):
        tokens.mint(subscription_id=1, team_id=1, expires_at=_future())


def test_startup_refuses_to_boot_stage_b_on_a_weak_secret(monkeypatch):
    from api.main import _check_stage_b_config

    monkeypatch.setattr(settings, "brief_token_secret", "")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    with pytest.raises(RuntimeError, match="BRIEF_TOKEN_SECRET"):
        _check_stage_b_config()
    # ...and Stage A, with everything off, still boots.
    monkeypatch.setattr(settings, "paywall_enabled", False)
    monkeypatch.setattr(settings, "send_enabled", False)
    _check_stage_b_config()


# ===========================================================================
# 7 — the paywall flip
# ===========================================================================

PAID_KEYS = {"bench_plan", "chips", "watchlist"}
FREE_KEYS = {"verdict", "transfer", "captaincy"}


def _persist_team_brief(SF, team_id: int, gw: int) -> None:
    """A season brief with every canonical section present."""
    md = "\n".join([
        f"# Gameweek {gw}", "",
        "## The verdict", "Solid enough.", "",
        "## This week's transfer", "Sell nobody.", "",
        "## Captaincy", "Armband stays.", "",
        "## Bench order & the plan", "GK, then the cheap defender.", "",
        "## Chips", "Hold the wildcard.", "",
        "## Watchlist", "Two names for next week.",
    ])
    with SF() as s, s.begin():
        s.add(Brief(fpl_team_id=team_id, draft_id=None, gameweek=gw, brief_md=md,
                    input_hash=f"test-{team_id}-{gw}", model="stub", tokens_in=0,
                    tokens_out=0))


def test_paywall_off_serves_everything(client, sf, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", False)
    _persist_team_brief(sf, 1234, 5)
    body = client.get("/api/brief/1234/5").json()
    assert FREE_KEYS | PAID_KEYS <= set(body["sections"])
    assert body["locked_sections"] == []


def test_paywall_on_withholds_paid_sections_from_an_anonymous_reader(
        client, sf, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    _persist_team_brief(sf, 1234, 5)
    body = client.get("/api/brief/1234/5").json()
    assert set(body["sections"]) == FREE_KEYS
    assert set(body["locked_sections"]) == PAID_KEYS


def test_the_flip_uses_the_existing_allow_list_and_not_a_second_implementation():
    """Structural. A second gating path is the thing docs/CLAUDE.md forbids."""
    import inspect as _inspect

    from api import service as _service

    src = _inspect.getsource(_service)
    # Exactly two gate calls — _shape and get_team_brief — and no local filtering.
    assert src.count("gate_sections(") == 2
    assert "FREE_SECTIONS" not in src, "service must not re-derive the allow-list"
    assert "PAID_SECTIONS" not in src


def _accept_terms(sf, email: str) -> None:
    """Record terms acceptance for the account the webhook provisioned.

    UPDATED BY THE ACCOUNTS BUILD (2026-08-04). Entitlement no longer resolves
    from the token to the subscription; it resolves token -> account ->
    subscription, and an account that has not accepted the current terms is
    served the FREE view (`routes._paid_account_id`). In the real flow the box is
    ticked on the checkout card and `POST /api/checkout` records it, so this
    helper stands in for the step these token tests skip by posting a webhook
    directly. It is not a workaround — without it, these tests would assert that
    the terms gate does not work.
    """
    from api import accounts as acc

    with sf() as s, s.begin():
        account = acc.get_account_by_email(s, email)
        assert account is not None, "the webhook should have provisioned an account"
        acc.accept_terms(account)


def test_a_valid_token_for_an_entitled_team_unlocks_everything(client, sf, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    _persist_team_brief(sf, 1234, 5)
    post_webhook(client, sub_event(team_id=1234))
    _accept_terms(sf, "fan@example.com")
    with sf() as s:
        sub = s.scalars(select(Subscription)).one()
        token = tokens.mint(subscription_id=sub.id, team_id=1234,
                            expires_at=sub.current_period_end.replace(
                                tzinfo=timezone.utc))
    body = client.get(f"/api/brief/1234/5?token={token}").json()
    assert FREE_KEYS | PAID_KEYS <= set(body["sections"])
    assert body["locked_sections"] == []


def test_a_token_for_another_team_does_not_unlock_this_one(client, sf, monkeypatch):
    """The claim is COMPARED, not merely present."""
    monkeypatch.setattr(settings, "paywall_enabled", True)
    _persist_team_brief(sf, 1234, 5)
    post_webhook(client, sub_event(team_id=1234))
    other = tokens.mint(subscription_id=1, team_id=9999, expires_at=_future())
    body = client.get(f"/api/brief/1234/5?token={other}").json()
    assert set(body["sections"]) == FREE_KEYS


def test_a_valid_token_stops_working_when_entitlement_ends(client, sf, monkeypatch):
    """Live DB re-check, not just the token — this is what makes a refund immediate.

    The token is still cryptographically valid and unexpired; the subscription is
    not entitled. Free view.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    _persist_team_brief(sf, 1234, 5)
    post_webhook(client, sub_event(team_id=1234))
    _accept_terms(sf, "fan@example.com")
    with sf() as s:
        sub = s.scalars(select(Subscription)).one()
        token = tokens.mint(subscription_id=sub.id, team_id=1234,
                            expires_at=_future())
    assert FREE_KEYS | PAID_KEYS <= set(
        client.get(f"/api/brief/1234/5?token={token}").json()["sections"])

    # The refund arrives as a cancellation with no remaining period.
    with sf() as s, s.begin():
        row = s.scalars(select(Subscription)).one()
        row.status, row.current_period_end = "canceled", None
    body = client.get(f"/api/brief/1234/5?token={token}").json()
    assert set(body["sections"]) == FREE_KEYS


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_an_invalid_token_degrades_to_free_rather_than_erroring(
        client, sf, monkeypatch, token):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    _persist_team_brief(sf, 1234, 5)
    r = client.get(f"/api/brief/1234/5?token={token}")
    assert r.status_code == 200
    assert set(r.json()["sections"]) == FREE_KEYS


# ===========================================================================
# 8 — the persisted generic preview singleton
# ===========================================================================

def test_the_generic_preview_persists_exactly_one_row_per_gameweek(golden_sf):
    deps = Deps(session_factory=golden_sf, client=None)
    first = service.persist_generic_preview(deps, 5)
    second = service.persist_generic_preview(deps, 5)
    assert first["created"] is True and second["created"] is False
    assert first["brief_id"] == second["brief_id"]
    with golden_sf() as s:
        rows = s.scalars(select(Brief).where(Brief.fpl_team_id.is_(None),
                                             Brief.draft_id.is_(None),
                                             Brief.gameweek == 5)).all()
    assert len(rows) == 1
    assert set(first["sections"]) & {"the_week_ahead", "trap_of_the_week"}


def test_the_preview_never_collides_with_a_real_cache_key(golden_sf):
    deps = Deps(session_factory=golden_sf, client=None)
    service.persist_generic_preview(deps, 6)
    with golden_sf() as s:
        row = s.scalars(select(Brief).where(Brief.gameweek == 6,
                                            Brief.fpl_team_id.is_(None))).one()
    assert row.input_hash == "generic:6"       # namespaced, never a sha256


# ===========================================================================
# 5 — the send batch
# ===========================================================================

class FakeMail:
    """Records sends; can be told to fail for specific addresses."""

    def __init__(self, fail_for: set[str] | None = None):
        self.sent: list[dict] = []
        self.fail_for = fail_for or set()

    def __call__(self, *, to, subject, html, text, unsubscribe_url=None):
        if to in self.fail_for:
            raise mail.MailError("provider said no")
        self.sent.append({"to": to, "subject": subject, "html": html,
                          "text": text, "unsubscribe_url": unsubscribe_url})
        return True


def _seed_send_world(SF, *, gw=5, free=("f1@example.com", "f2@example.com"),
                     paid_team=1234, paid_email="paid@example.com"):
    _persist_team_brief(SF, paid_team, gw)
    deps = Deps(session_factory=SF, client=None)
    with SF() as s, s.begin():
        s.add(Brief(fpl_team_id=None, draft_id=None, gameweek=gw,
                    brief_md="# Gameweek 5\n\n## The week ahead\nBusy.\n\n"
                             "## Trap of the week\nAvoid the popular striker.",
                    input_hash=f"generic:{gw}", model="stub",
                    tokens_in=0, tokens_out=0))
        s.add(Subscription(email=paid_email, fpl_team_id=paid_team, provider="paddle",
                           provider_subscription_id="sub_send", plan="monthly",
                           status="active",
                           current_period_end=datetime.now(timezone.utc)
                           + timedelta(days=30)))
        for addr in free:
            s.add(EmailSignup(email=addr, tier="free"))
    return deps


def test_the_batch_sends_both_templates(sf):
    deps = _seed_send_world(sf)
    fake = FakeMail()
    result = send.run_batch(deps, 5, send_fn=fake)
    assert result.sent == 3 and result.failed == 0
    to = {m["to"] for m in fake.sent}
    assert to == {"paid@example.com", "f1@example.com", "f2@example.com"}

    # Two distinct templates, distinguishable from the subject line alone (§3):
    # the paid brief leads with the verdict hook, the free one says "preview".
    by_addr = {m["to"]: m["subject"] for m in fake.sent}
    assert by_addr["paid@example.com"].startswith("GW5: ")
    assert by_addr["f1@example.com"].startswith("GW5 preview: ")
    # And the paid one carries paid content while the free one does not.
    paid_html = next(m["html"] for m in fake.sent if m["to"] == "paid@example.com")
    free_html = next(m["html"] for m in fake.sent if m["to"] == "f1@example.com")
    assert "Bench order" in paid_html and "Bench order" not in free_html
    with sf() as s:
        rows = {(r.template, r.status) for r in s.scalars(select(SendLog))}
    assert rows == {("paid_brief", "sent"), ("free_preview", "sent")}


def test_every_send_carries_a_one_click_unsubscribe(sf):
    deps = _seed_send_world(sf)
    fake = FakeMail()
    send.run_batch(deps, 5, send_fn=fake)
    for message in fake.sent:
        assert message["unsubscribe_url"], "no List-Unsubscribe target"
        assert "/api/email/unsubscribe" in message["unsubscribe_url"]
        assert "Unsubscribe" in message["text"]         # visible link too


def test_every_send_has_a_real_plaintext_alternative(sf):
    deps = _seed_send_world(sf)
    fake = FakeMail()
    send.run_batch(deps, 5, send_fn=fake)
    for message in fake.sent:
        assert message["text"].strip()
        assert "<" not in message["text"].split("---")[0], "html leaked into plaintext"
        assert len(message["text"]) > 80


def test_the_batch_is_idempotent(sf):
    """Rerunning must mail nobody twice — the cron can fire twice, and does."""
    deps = _seed_send_world(sf)
    first = FakeMail()
    send.run_batch(deps, 5, send_fn=first)
    second = FakeMail()
    result = send.run_batch(deps, 5, send_fn=second)
    assert second.sent == []
    assert result.sent == 0 and result.skipped == 3
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(SendLog)) == 3


def test_one_bad_recipient_does_not_end_the_batch(sf):
    """Per-recipient isolation (§D4.2). The whole-batch try/except is the bug."""
    deps = _seed_send_world(sf, free=("good1@example.com", "bad@example.com",
                                      "good2@example.com"))
    fake = FakeMail(fail_for={"bad@example.com"})
    result = send.run_batch(deps, 5, send_fn=fake)
    assert result.failed == 1
    assert result.sent == 3                      # paid + two good free addresses
    assert "good2@example.com" in {m["to"] for m in fake.sent}, \
        "the batch stopped at the failure"
    with sf() as s:
        row = s.scalars(select(SendLog).where(
            SendLog.email == "bad@example.com")).one()
    assert row.status == "failed" and "provider said no" in row.error


def test_a_failure_is_retried_exactly_once(sf):
    deps = _seed_send_world(sf, free=("bad@example.com",))
    send.run_batch(deps, 5, send_fn=FakeMail(fail_for={"bad@example.com"}))

    recovered = FakeMail()
    result = send.retry_failed(deps, 5, send_fn=recovered)
    assert result.sent == 1 and {m["to"] for m in recovered.sent} == {"bad@example.com"}

    # A second retry pass does nothing — bounded to one extra attempt.
    again = FakeMail()
    send.retry_failed(deps, 5, send_fn=again)
    assert again.sent == []


def test_an_unsubscribed_free_reader_is_not_mailed(sf):
    deps = _seed_send_world(sf, free=("stay@example.com", "gone@example.com"))
    service.unsubscribe(deps, "gone@example.com")
    fake = FakeMail()
    send.run_batch(deps, 5, send_fn=fake)
    assert "gone@example.com" not in {m["to"] for m in fake.sent}
    assert "stay@example.com" in {m["to"] for m in fake.sent}


def test_an_unsubscribed_paid_subscriber_stops_mail_but_keeps_access(sf, monkeypatch):
    """§1.8: mail consent and billing are different things.

    Unsubscribing must not cancel a service someone is paying for.
    """
    monkeypatch.setattr(settings, "paywall_enabled", True)
    deps = _seed_send_world(sf, free=())
    with sf() as s, s.begin():
        s.add(EmailSignup(email="paid@example.com", tier="paid", fpl_team_id=1234))
    service.unsubscribe(deps, "paid@example.com")

    fake = FakeMail()
    send.run_batch(deps, 5, send_fn=fake)
    assert fake.sent == [], "an unsubscribed subscriber was still mailed"

    with sf() as s:                              # ...and still entitled
        assert billing.entitled_for_team(s, 1234) is True


def test_the_batch_does_not_run_when_send_is_disabled(sf, monkeypatch):
    monkeypatch.setattr(settings, "send_enabled", False)
    deps = _seed_send_world(sf)
    fake = FakeMail()
    result = send.run_batch(deps, 5, send_fn=fake)
    assert fake.sent == [] and result.errors == ["send_disabled"]
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(SendLog)) == 0


def test_the_batch_respects_the_whole_run_ceiling(sf, monkeypatch):
    monkeypatch.setattr(settings, "send_max_recipients", 2)
    deps = _seed_send_world(sf, free=("a@example.com", "b@example.com",
                                      "c@example.com"))
    fake = FakeMail()
    result = send.run_batch(deps, 5, send_fn=fake)
    assert result.sent == 2 and result.capped is True


def test_a_subscription_without_a_persisted_brief_is_skipped_not_failed(sf):
    """No brief means nothing to send — that is a skip, not an error to alert on."""
    deps = _seed_send_world(sf, free=())
    fake = FakeMail()
    result = send.run_batch(deps, 9, send_fn=fake)   # gw 9 has no brief, no preview
    assert result.sent == 0 and result.failed == 0
    assert "no_generic_preview" in result.errors


# --- PII in logs -------------------------------------------------------------

def test_addresses_are_masked_for_logging():
    masked = mail.mask("Someone.Real@example.com")
    assert "someone.real" not in masked.lower()
    assert masked.endswith("@example.com")           # domain kept: it is the diagnosis
    assert mail.mask("Someone.Real@example.com") == masked      # stable


def test_the_send_path_never_logs_a_full_address(sf, caplog):
    import logging as _logging

    deps = _seed_send_world(sf, free=("visible@example.com",))
    with caplog.at_level(_logging.INFO):
        send.run_batch(deps, 5, send_fn=FakeMail(fail_for={"visible@example.com"}))
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "visible@example.com" not in blob
    assert "paid@example.com" not in blob


def test_the_admin_billing_view_masks_addresses(sf):
    deps = _seed_send_world(sf, free=())
    from api import admin as _admin

    view = _admin.subscriptions(deps)
    assert view["subscriptions"], "no rows to check"
    for row in view["subscriptions"]:
        assert "paid@example.com" not in row["email"]
    assert view["entitled_count"] == 1


# ===========================================================================
# 6 — unsubscribe + delete
# ===========================================================================

def test_one_click_unsubscribe_works_from_a_mailed_link(client, sf, monkeypatch):
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "bye@example.com"})
    token = tokens.unsubscribe_token("bye@example.com")

    r = client.get(f"/api/email/unsubscribe?e=bye@example.com&t={token}")
    assert r.status_code == 200 and r.json()["status"] == "unsubscribed"
    with sf() as s:
        assert s.get(EmailSignup, "bye@example.com").unsubscribed_at is not None


def test_the_rfc8058_one_click_post_target_works(client, sf, monkeypatch):
    """Gmail and Yahoo POST here with no human and no session."""
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "bye2@example.com"})
    token = tokens.unsubscribe_token("bye2@example.com")
    r = client.post(f"/api/email/unsubscribe?e=bye2@example.com&t={token}")
    assert r.status_code == 200
    with sf() as s:
        assert s.get(EmailSignup, "bye2@example.com").unsubscribed_at is not None


@pytest.mark.parametrize("query", [
    "e=bye@example.com&t=wrong",
    "e=bye@example.com",
    "t=sometoken",
    "",
])
def test_unsubscribe_needs_a_valid_token(client, query):
    r = client.get(f"/api/email/unsubscribe?{query}")
    assert r.status_code == 400


def test_an_unsubscribe_token_for_one_address_does_not_work_for_another(client):
    token = tokens.unsubscribe_token("a@example.com")
    r = client.get(f"/api/email/unsubscribe?e=b@example.com&t={token}")
    assert r.status_code == 400


def test_unsubscribe_is_not_an_oracle_for_who_is_a_customer(client, sf, monkeypatch):
    """Identical response for a known and an unknown address."""
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "known@example.com"})
    known = client.get("/api/email/unsubscribe?e=known@example.com"
                       f"&t={tokens.unsubscribe_token('known@example.com')}")
    unknown = client.get("/api/email/unsubscribe?e=ghost@example.com"
                         f"&t={tokens.unsubscribe_token('ghost@example.com')}")
    assert known.status_code == unknown.status_code == 200
    assert known.json()["status"] == unknown.json()["status"]


def test_resubscribe_undoes_it(client, sf, monkeypatch):
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "back@example.com"})
    token = tokens.unsubscribe_token("back@example.com")
    client.get(f"/api/email/unsubscribe?e=back@example.com&t={token}")
    r = client.post(f"/api/email/resubscribe?e=back@example.com&t={token}")
    assert r.status_code == 200
    with sf() as s:
        assert s.get(EmailSignup, "back@example.com").unsubscribed_at is None


def test_delete_removes_the_row_and_anonymises_billing(client, sf, monkeypatch):
    """Delete is a DIFFERENT action from unsubscribe and actually removes the row."""
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "erase@example.com", "team_id": 55})
    post_webhook(client, sub_event(email="erase@example.com", team_id=55))
    with sf() as s, s.begin():
        s.add(SendLog(email="erase@example.com", gameweek=1, template="free_preview",
                      status="sent", attempts=1))

    token = tokens.unsubscribe_token("erase@example.com")
    r = client.post(f"/api/email/delete?e=erase@example.com&t={token}")
    assert r.status_code == 200
    assert r.json()["signup_removed"] is True
    assert r.json()["subscriptions_anonymised"] == 1
    assert r.json()["send_log_rows_removed"] == 1

    with sf() as s:
        assert s.get(EmailSignup, "erase@example.com") is None
        # The billing record survives, anonymised: it is a financial record.
        sub = s.scalars(select(Subscription)).one()
        assert sub.email == "" and sub.plan == "monthly"
        assert s.scalar(select(func.count()).select_from(SendLog)) == 0


def test_a_tombstoned_address_is_not_resurrected_by_a_later_signup(
        client, sf, monkeypatch):
    """The reason unsubscribe leaves a row instead of deleting one."""
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **k: True)
    client.post("/api/signup", json={"email": "no@example.com"})
    token = tokens.unsubscribe_token("no@example.com")
    client.get(f"/api/email/unsubscribe?e=no@example.com&t={token}")
    client.post("/api/signup", json={"email": "no@example.com", "source": "again"})
    with sf() as s:
        assert s.get(EmailSignup, "no@example.com").unsubscribed_at is not None


# ===========================================================================
# The DB-backed rate limiter (audit §C2)
# ===========================================================================

def test_the_db_limiter_survives_a_process_restart(sf):
    """The whole point. The in-memory dict reset on every Render wake."""
    lim = DbRateLimiter(limit=3, window_s=3600)
    for _ in range(3):
        lim.enforce(sf, "ip:203.0.113.9")

    # A brand-new instance stands in for the process that woke up after a sleep.
    reborn = DbRateLimiter(limit=3, window_s=3600)
    from api.ratelimit import RateLimited

    with pytest.raises(RateLimited):
        reborn.enforce(sf, "ip:203.0.113.9")


def test_db_limiter_buckets_are_independent(sf):
    from api.ratelimit import RateLimited

    lim = DbRateLimiter(limit=1, window_s=3600)
    lim.enforce(sf, "ip:a")
    lim.enforce(sf, "ip:b")
    with pytest.raises(RateLimited):
        lim.enforce(sf, "ip:a")


def test_the_global_cap_stops_generation_regardless_of_caller(sf):
    """§C2 option A: the circuit breaker that caps the bill when per-IP has a bug."""
    from api.ratelimit import RateLimited

    lim = DbRateLimiter(limit=2, window_s=3600)
    lim.enforce(sf, "global")
    lim.enforce(sf, "global")
    with pytest.raises(RateLimited):
        lim.enforce(sf, "global")


def test_the_limiter_table_is_swept(sf):
    lim = DbRateLimiter(limit=100, window_s=60)
    old = datetime.now(timezone.utc) - timedelta(days=5)
    lim.enforce(sf, "ip:old", now=old)
    lim.enforce(sf, "ip:new")
    removed = lim.purge_before(sf, datetime.now(timezone.utc) - timedelta(days=2))
    assert removed == 1
    assert lim.peek(sf, "ip:new") == 1


def test_generation_budget_is_enforced_end_to_end(golden_sf, monkeypatch):
    """The route path: five generations, then the sixth is refused and DURABLY so."""
    from api.ratelimit import RateLimited

    app.dependency_overrides[get_deps] = lambda: Deps(session_factory=golden_sf,
                                                      client=None)
    try:
        c = TestClient(app)
        picks = json.loads((ASSETS / "entry_sample_event_38_picks_2025-26.json"
                            ).read_text("utf-8"))
        ids = [p["element"] for p in sorted(picks["picks"],
                                            key=lambda p: p["position"])]
        for cap in ids[:5]:
            assert c.post("/api/brief", json={
                "draft": {"player_ids": ids, "captain_id": cap}}).status_code == 200
        assert c.post("/api/brief", json={
            "draft": {"player_ids": ids, "captain_id": ids[5]}}).status_code == 429
        # The in-memory limiter is what fired; prove the DB counter recorded too, so
        # a restart would not hand the caller a fresh budget.
        assert db_limiter.peek(golden_sf, "ip:testclient") >= 5
        assert global_limiter.peek(golden_sf, "global") >= 5
    finally:
        app.dependency_overrides.clear()
        limiter.reset()


# ===========================================================================
# Templates
# ===========================================================================

def test_the_paid_template_renders_sections_in_canonical_order():
    from writer import emails

    sections = {"watchlist": "Later.", "verdict": "First.", "chips": "Hold."}
    _, html, text = emails.paid_brief(gameweek=7, sections=sections,
                                      brief_url="https://x.test/b",
                                      unsubscribe_url="https://x.test/u")
    assert html.index("First.") < html.index("Hold.") < html.index("Later.")
    assert text.index("First.") < text.index("Hold.") < text.index("Later.")


def test_a_section_key_outside_the_vocabulary_is_not_rendered():
    """Same allow-list property as the paywall, in the email renderer."""
    from writer import emails

    _, html, text = emails.paid_brief(
        gameweek=7, sections={"verdict": "Fine.", "x_smuggled": "SHOULD NOT APPEAR"},
        brief_url="https://x.test/b", unsubscribe_url="https://x.test/u")
    assert "SHOULD NOT APPEAR" not in html and "SHOULD NOT APPEAR" not in text


def test_hostile_text_in_a_brief_cannot_inject_html():
    """`players.news` is EXTERNAL text (audit §M6) and reaches brief prose."""
    from writer import emails

    nasty = '<script>alert(1)</script> and <img src=x onerror=y>'
    _, html, _ = emails.paid_brief(gameweek=7, sections={"verdict": nasty},
                                   brief_url="https://x.test/b",
                                   unsubscribe_url="https://x.test/u")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_subject_hook_is_the_first_sentence_of_the_verdict():
    from writer import emails

    subject, _, _ = emails.paid_brief(
        gameweek=12,
        sections={"verdict": "Your bench is costing you. Second sentence here."},
        brief_url="https://x.test/b", unsubscribe_url="https://x.test/u")
    assert subject == "GW12: Your bench is costing you"


def test_a_long_hook_is_truncated_not_wrapped():
    from writer import emails

    subject, _, _ = emails.paid_brief(
        gameweek=1, sections={"verdict": "x" * 200},
        brief_url="https://x.test/b", unsubscribe_url="https://x.test/u")
    assert len(subject) < 80 and subject.endswith("…")
