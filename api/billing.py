"""Paddle Billing: signature verification, event handling, entitlement (spec §4).

Hardened to docs/AUDIT.md §D1 and §D6. The three rules that must survive every
future edit to this file:

1. **Verify before parsing.** `verify_signature` takes RAW BYTES. Nothing in this
   module calls `json.loads` on a body that has not already been proven to come
   from Paddle. A FastAPI route that declares a pydantic body model has already
   deserialized attacker input before the handler runs, which is why the route
   takes `Request` and reads `await request.body()` itself.

2. **Plan and price come from the verified payload, never from client input.** The
   checkout route sends a plan *name* and the server resolves the price id; the
   webhook re-derives the plan from the price id **in the signed payload**. A
   client can lie about what it selected; it cannot forge Paddle's signature over
   what it was actually charged.

3. **Entitlement is an allow-list.** `ENTITLED_STATUSES` is the whole rule. A
   status string this code has never seen — a new Paddle state, a typo, a future
   product — is NOT entitled. This is the same allow-list-not-deny-list property
   that `graph.sections.gate_sections` enforces for section keys, for the same
   reason: the failure mode must be "a paying reader is briefly under-served",
   never "everyone reads the paid product".

## Paddle's signature scheme

`Paddle-Signature: ts=1671552777;h1=<hex hmac>`, where the HMAC-SHA256 is computed
over `f"{ts}:{raw_body}"` with the notification destination's secret key. The
timestamp is INSIDE the signed material, so a replayed request cannot have its
timestamp edited — which is what makes the freshness window real protection
rather than a clock-skew allowance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fpl.models import EmailSignup, Subscription, WebhookEvent

from .config import settings

log = logging.getLogger("api.billing")


class SignatureError(Exception):
    """The request did not come from Paddle. The route answers 401 with no detail."""


# --- THE allow-list ---------------------------------------------------------
#
# Every other Paddle status — past_due, paused, canceled, and anything invented
# after this line was written — is not entitled by construction. `canceled` gets a
# grace path below (delivery to current_period_end); `past_due` deliberately does
# NOT, because Paddle is still retrying and we should not serve paid content on a
# payment that may never land.
ENTITLED_STATUSES = frozenset({"active", "trialing"})

# Statuses that keep delivery alive until the period they were paid for ends.
# `canceled` only: a cancellation is "do not renew", not "refund the current
# period". A refund or chargeback also arrives as a cancellation (spec §4, "no
# refund handling logic — MoR owns refunds"), and those must NOT keep delivering,
# which is why the grace path additionally requires the period end to be in the
# future — a refund cancels with a period end that is already past or is moved to
# now by Paddle.
GRACE_STATUSES = frozenset({"canceled"})


def plan_for_price_id(price_id: str | None) -> str | None:
    """Map a Paddle price id to our plan name. Unknown id -> None (not entitled).

    The map is env-configured and lives only on the server. This is the function
    that makes "a client cannot select what it pays" true on the webhook side.
    """
    if not price_id:
        return None
    if price_id == settings.paddle_price_monthly and price_id:
        return "monthly"
    if price_id == settings.paddle_price_season and price_id:
        return "season"
    return None


# --- 1. verification --------------------------------------------------------

def _parse_signature_header(header: str) -> tuple[int, list[str]]:
    """`ts=...;h1=...` -> (ts, [h1, ...]). Raises SignatureError on anything else."""
    ts: int | None = None
    macs: list[str] = []
    for part in header.split(";"):
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key == "ts":
            try:
                ts = int(value)
            except ValueError as exc:
                raise SignatureError("non-integer ts") from exc
        elif key == "h1":
            macs.append(value)
    if ts is None or not macs:
        raise SignatureError("signature header missing ts or h1")
    return ts, macs


def verify_signature(raw_body: bytes, signature_header: str | None,
                     *, now: datetime | None = None) -> None:
    """Prove `raw_body` came from Paddle, unmodified and recently. Else raise.

    Takes bytes, NOT a parsed object — see rule 1 in the module docstring.
    """
    if not settings.paddle_webhook_secret:
        # Empty secret closes the webhook, exactly as an empty ADMIN_TOKEN closes
        # admin. Never open-by-misconfiguration.
        raise SignatureError("PADDLE_WEBHOOK_SECRET is unset; webhook is closed")
    if not signature_header:
        raise SignatureError("missing Paddle-Signature header")

    ts, macs = _parse_signature_header(signature_header)

    signed_payload = str(ts).encode() + b":" + raw_body
    expected = hmac.new(settings.paddle_webhook_secret.encode(),
                        signed_payload, hashlib.sha256).hexdigest()
    # compare_digest against every h1 Paddle sent (it sends more than one during a
    # secret rotation). Timing-safe, per audit §D1.2.
    if not any(hmac.compare_digest(expected, mac) for mac in macs):
        raise SignatureError("signature mismatch")

    # Replay protection. The timestamp is part of the signed material above, so an
    # attacker replaying a captured-but-valid request cannot slide it forward.
    now = now or datetime.now(timezone.utc)
    age = int(now.timestamp()) - ts
    if age > settings.paddle_replay_window_s:
        raise SignatureError(f"stale signature: {age}s old")
    if age < -settings.paddle_replay_window_s:
        raise SignatureError(f"signature from the future: {-age}s ahead")


def parse_verified(raw_body: bytes) -> dict:
    """json.loads, but only ever called AFTER verify_signature has returned."""
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SignatureError(f"verified body was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SignatureError("verified body was not a JSON object")
    return payload


# --- 2. payload readers -----------------------------------------------------

def _dt(value: str | None) -> datetime | None:
    """Paddle sends RFC3339 with a `Z`. Always return aware UTC or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first_price_id(data: dict) -> str | None:
    """The price id, from either a subscription or a transaction payload."""
    for item in data.get("items") or []:
        price = item.get("price")
        if isinstance(price, dict) and price.get("id"):
            return price["id"]
        if item.get("price_id"):
            return item["price_id"]
    return None


def _custom(data: dict) -> dict:
    got = data.get("custom_data")
    return got if isinstance(got, dict) else {}


def _email_of(data: dict) -> str | None:
    """Prefer our own custom_data (we put it there at checkout), fall back to Paddle."""
    email = _custom(data).get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    customer = data.get("customer")
    if isinstance(customer, dict) and isinstance(customer.get("email"), str):
        return customer["email"].strip().lower()
    return None


def _team_id_of(data: dict) -> int | None:
    raw = _custom(data).get("fpl_team_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _period_end(data: dict) -> datetime | None:
    period = data.get("current_billing_period")
    if isinstance(period, dict):
        return _dt(period.get("ends_at"))
    return _dt(data.get("next_billed_at"))


def _season_pass_end() -> datetime:
    """30 June of the configured season-end year, 23:59:59 UTC.

    A one-time purchase carries no billing period from Paddle, so the PRODUCT
    defines the term. Env-configured rather than computed from `now` because a
    purchase in July belongs to the season starting in August, and a rule that
    guesses gets that wrong every summer.
    """
    return datetime(settings.season_pass_end_year, 6, 30, 23, 59, 59, tzinfo=timezone.utc)


# --- 3. the state machine ---------------------------------------------------

def is_entitled(sub: Subscription | None, *, now: datetime | None = None) -> bool:
    """The ONE entitlement decision. Everything paid asks this function.

    | state | entitled |
    |---|---|
    | active / trialing | yes |
    | canceled, period end still future | yes — they paid for the period |
    | canceled, period end passed or absent | no |
    | past_due | no — Paddle is still retrying; do not serve on an unpaid invoice |
    | paused | no |
    | anything unrecognised | **no** |
    """
    if sub is None:
        return False
    now = now or datetime.now(timezone.utc)
    if sub.status in ENTITLED_STATUSES:
        return True
    if sub.status in GRACE_STATUSES:
        end = sub.current_period_end
        if end is None:
            # No period to run out => nothing to honour. A refund lands here.
            return False
        if end.tzinfo is None:                    # SQLite drops tzinfo on read
            end = end.replace(tzinfo=timezone.utc)
        return now < end
    return False


def entitled_for_team(session, team_id: int, *, now: datetime | None = None) -> bool:
    """Is any subscription for `team_id` currently entitled?"""
    rows = session.scalars(
        select(Subscription).where(Subscription.fpl_team_id == team_id)).all()
    return any(is_entitled(s, now=now) for s in rows)


def active_subscriptions(session, *, now: datetime | None = None) -> list[Subscription]:
    """Every subscription the send job should mail. Filtered through `is_entitled`."""
    rows = session.scalars(select(Subscription)).all()
    return [s for s in rows if is_entitled(s, now=now)]


# --- 4. event handling ------------------------------------------------------

# Subscription lifecycle events we act on. Everything else is recorded and ignored
# (spec §4: "Everything else: log and 200").
_SUBSCRIPTION_EVENTS = {
    "subscription.created", "subscription.activated", "subscription.updated",
    "subscription.past_due", "subscription.canceled", "subscription.paused",
    "subscription.resumed", "subscription.trialing",
}

# The status a subscription event implies when the payload omits one. Paddle
# normally sends `status` in `data`; these are the fallbacks so an event type we
# were told about still lands in the right state.
_EVENT_STATUS = {
    "subscription.created": "active",
    "subscription.activated": "active",
    "subscription.past_due": "past_due",
    "subscription.canceled": "canceled",
    "subscription.paused": "paused",
    "subscription.resumed": "active",
    "subscription.trialing": "trialing",
}


class Handled:
    """What `handle_event` did, for the route's response and the tests."""

    def __init__(self, action: str, subscription_id: int | None = None,
                 duplicate: bool = False, detail: str = ""):
        self.action = action
        self.subscription_id = subscription_id
        self.duplicate = duplicate
        self.detail = detail

    def as_dict(self) -> dict:
        return {"ok": True, "action": self.action, "duplicate": self.duplicate}


def handle_event(session, payload: dict, *, now: datetime | None = None) -> Handled:
    """Apply one VERIFIED event. Caller owns the transaction.

    Idempotency: the `webhook_events` INSERT happens in the same transaction as the
    state change, and its PK is the guard. A redelivery raises IntegrityError on
    flush, the caller rolls back, and nothing is applied twice.
    """
    now = now or datetime.now(timezone.utc)
    event_id = payload.get("event_id") or payload.get("notification_id")
    event_type = payload.get("event_type") or ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if not event_id:
        raise SignatureError("verified payload has no event_id")

    session.add(WebhookEvent(
        event_id=str(event_id),
        event_type=str(event_type),
        occurred_at=_dt(payload.get("occurred_at")),
        processed_at=now,
        payload=payload,
    ))
    # Force the PK collision NOW, before any state change, so a duplicate costs one
    # failed insert rather than a half-applied transaction.
    session.flush()

    if event_type in _SUBSCRIPTION_EVENTS:
        return _apply_subscription(session, event_type, data, now)
    if event_type == "transaction.completed":
        return _apply_season_pass(session, data, now)

    log.info("billing: recorded unhandled event_type=%s", event_type)
    return Handled("ignored", detail=f"unhandled event_type {event_type}")


def _upsert_signup_tier(session, email: str | None, team_id: int | None) -> None:
    """Mark the mailing-list row paid. NEVER the source of entitlement (see models).

    Fill-blanks only, matching `service.signup` — the audit's H4 finding was this
    exact table being clobbered by a well-meaning `merge`.
    """
    if not email:
        return
    row = session.get(EmailSignup, email)
    if row is None:
        session.add(EmailSignup(email=email, fpl_team_id=team_id, tier="paid",
                                source="checkout"))
        return
    row.tier = "paid"
    if row.fpl_team_id is None and team_id is not None:
        row.fpl_team_id = team_id
    # An unsubscribe is a MAIL preference and survives a purchase. Buying does not
    # re-consent someone to email; the checkout success page is where they opt back in.


def _apply_subscription(session, event_type: str, data: dict,
                        now: datetime) -> Handled:
    provider_id = data.get("id")
    if not provider_id:
        return Handled("ignored", detail="subscription event without an id")

    # Status from the PAYLOAD first, event type only as a fallback. Free text: an
    # unrecognised value is stored and simply fails the entitlement allow-list.
    status = data.get("status") or _EVENT_STATUS.get(event_type) or "unknown"
    price_id = _first_price_id(data)
    plan = plan_for_price_id(price_id)
    email = _email_of(data)
    team_id = _team_id_of(data)
    period_end = _period_end(data)

    row = session.scalars(
        select(Subscription).where(
            Subscription.provider_subscription_id == str(provider_id))).first()

    if row is None:
        row = Subscription(
            email=email or "",
            fpl_team_id=team_id,
            provider="paddle",
            provider_subscription_id=str(provider_id),
            provider_customer_id=data.get("customer_id"),
            # An unknown price id yields plan=None; store it as 'unknown' rather
            # than guessing. It is recorded, visible, and not entitled-by-plan.
            plan=plan or "unknown",
            status=str(status),
            current_period_end=period_end,
        )
        session.add(row)
    else:
        row.status = str(status)
        # Renewals extend the period; this is what keeps already-emailed tokens
        # valid (spec §4, subscription.updated). Never shorten on a missing value.
        if period_end is not None:
            row.current_period_end = period_end
        if plan is not None:
            row.plan = plan
        if email:
            row.email = email
        if team_id is not None:
            row.fpl_team_id = team_id
        if data.get("customer_id"):
            row.provider_customer_id = data["customer_id"]
        row.updated_at = now

    if status in ENTITLED_STATUSES:
        _upsert_signup_tier(session, email, team_id)

    session.flush()
    log.info("billing: %s -> subscription id=%s status=%s plan=%s",
             event_type, row.id, row.status, row.plan)
    return Handled("subscription_upserted", subscription_id=row.id)


def _apply_season_pass(session, data: dict, now: datetime) -> Handled:
    """`transaction.completed` carrying the one-time season-pass price id (spec §4).

    Ignored unless the price id in the VERIFIED payload maps to the season plan —
    ordinary subscription transactions also emit this event, and acting on them
    would create a duplicate entitlement alongside the subscription row.
    """
    price_id = _first_price_id(data)
    if plan_for_price_id(price_id) != "season":
        return Handled("ignored", detail="transaction.completed without a season price")
    # A transaction that belongs to a subscription is that subscription's business.
    if data.get("subscription_id"):
        return Handled("ignored", detail="transaction belongs to a subscription")

    txn_id = data.get("id")
    if not txn_id:
        return Handled("ignored", detail="transaction without an id")

    # Namespaced so a transaction id can never collide with a subscription id in the
    # unique column they share.
    key = f"txn:{txn_id}"
    row = session.scalars(
        select(Subscription).where(Subscription.provider_subscription_id == key)).first()
    email = _email_of(data)
    team_id = _team_id_of(data)

    if row is None:
        row = Subscription(
            email=email or "",
            fpl_team_id=team_id,
            provider="paddle",
            provider_subscription_id=key,
            provider_customer_id=data.get("customer_id"),
            plan="season",
            status="active",
            current_period_end=_season_pass_end(),
        )
        session.add(row)
    else:
        row.status = "active"
        row.current_period_end = _season_pass_end()
        row.updated_at = now

    _upsert_signup_tier(session, email, team_id)
    session.flush()
    log.info("billing: season pass -> subscription id=%s ends=%s",
             row.id, row.current_period_end)
    return Handled("season_pass_granted", subscription_id=row.id)


# --- 5. checkout (spec §1.6) ------------------------------------------------

class CheckoutError(Exception):
    """Checkout could not be created. Carries a code for the route, never a body."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(code)


def price_id_for_plan(plan: str) -> str:
    """Resolve a plan NAME to a price id, server-side. The whole point of §D3.

    The client sends `"monthly"`. It never sends, sees, or influences a price id,
    an amount or a currency, so there is nothing for it to tamper with. An
    unconfigured plan is a 503 (our misconfiguration), not a 400 (their fault).
    """
    mapping = {
        "monthly": settings.paddle_price_monthly,
        "season": settings.paddle_price_season,
    }
    if plan not in mapping:
        raise CheckoutError("unknown_plan", f"No such plan: {plan}")
    price_id = mapping[plan]
    if not price_id:
        raise CheckoutError("plan_unavailable",
                            f"PADDLE_PRICE_{plan.upper()} is not configured")
    return price_id


def create_checkout(plan: str, email: str, team_id: int | None,
                    *, post=None) -> str:
    """Create a Paddle transaction and return its hosted checkout URL.

    `custom_data` carries `{fpl_team_id, email}` so the webhook can attribute the
    subscription without trusting anything the browser sends back. `post` is
    injectable so tests never touch the network.
    """
    price_id = price_id_for_plan(plan)
    if not settings.paddle_api_key:
        raise CheckoutError("checkout_unavailable", "PADDLE_API_KEY is not configured")

    import httpx

    post = post or httpx.post
    body = {
        "items": [{"price_id": price_id, "quantity": 1}],
        # Read back on the webhook from the SIGNED payload — see rule 2 above.
        "custom_data": {"fpl_team_id": team_id, "email": email},
        "customer": {"email": email},
    }
    try:
        resp = post(
            f"{settings.paddle_api_base.rstrip('/')}/transactions",
            json=body,
            headers={"Authorization": f"Bearer {settings.paddle_api_key}",
                     "Content-Type": "application/json"},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
    except Exception as exc:                     # network, 4xx/5xx, malformed JSON
        # The provider's error text can quote our API key back at us; it goes to the
        # log, never to the caller.
        log.warning("billing: checkout creation failed: %s", exc)
        raise CheckoutError("checkout_failed", "provider rejected the checkout") from exc

    url = (data.get("checkout") or {}).get("url")
    if not url:
        log.warning("billing: checkout response had no checkout.url")
        raise CheckoutError("checkout_failed", "provider returned no checkout url")
    return url


def already_processed(session, event_id: str) -> bool:
    """Cheap pre-check so a redelivery does not have to fail an insert to be a no-op.

    NOT the idempotency mechanism — the PK is (see `handle_event`). This only saves
    a wasted transaction on the common case; the race it cannot cover is exactly
    what the PK covers.
    """
    return session.get(WebhookEvent, event_id) is not None


def is_duplicate(exc: IntegrityError) -> bool:
    """Did this IntegrityError come from the webhook_events PK (i.e. a redelivery)?"""
    text = str(getattr(exc, "orig", exc)).lower()
    return "webhook_events" in text or "unique" in text or "duplicate" in text
