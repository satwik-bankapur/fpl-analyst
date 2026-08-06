"""The weekly send batch (spec §3) — post-deadline, idempotent, isolated.

Runs strictly after brief persistence. Two passes over two audiences:

  1. every ENTITLED subscription  -> `paid_brief`, with a minted full-view token
  2. every free, still-subscribed signup -> `free_preview`

## The four properties that make this safe to re-run

**Idempotent.** `send_log` has a unique index on `(email, gameweek, template)` and
the row is inserted BEFORE the provider call, in its own committed transaction. A
crash between call and record would otherwise re-send on the next run, and a
duplicate email to a paying customer is worse than a missing one we can retry on
purpose. The unique index is the mechanism — not a `SELECT ... if not exists`,
which races two overlapping runs against each other.

**Per-recipient isolation.** One bad address must not end the batch at recipient 3
of 400. Every send is wrapped individually; a failure marks that row `failed`,
records the reason, and the loop continues. The whole-batch `try` that would be
the lazy version of this is precisely the bug (docs/AUDIT.md §D4.2).

**Retry once, in-run.** `retry_failed` re-attempts rows marked `failed` — spec §3
says "+1h", which the caller schedules; this module just makes the retry
idempotent and bounded to one extra attempt. No dead-letter machinery in v1.

**No addresses in logs.** Every log line goes through `mail.mask`.

## Why entitlement is re-checked here and not passed in

`billing.active_subscriptions` applies the allow-list at query time, so a
subscription that went `past_due` between brief generation and the send does not
get mailed. The send job is the last gate before money-backed content leaves the
building; it re-reads state rather than trusting a caller's list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fpl.models import Brief, EmailSignup, SendLog
from graph.deps import Deps
from graph.sections import PREVIEW_HEADERS, split_sections
from writer import emails

from . import billing, mail, tokens
from .config import settings

log = logging.getLogger("api.send")

PAID_TEMPLATE = "paid_brief"
FREE_TEMPLATE = "free_preview"


@dataclass
class SendResult:
    gameweek: int
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    capped: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"gameweek": self.gameweek, "sent": self.sent, "skipped": self.skipped,
                "failed": self.failed, "capped": self.capped,
                "errors": self.errors[:20]}


# --- links ------------------------------------------------------------------

def _web(path: str) -> str:
    return f"{settings.public_web_url.rstrip('/')}{path}"


def _unsub_query(email: str) -> str:
    from urllib.parse import quote

    return f"e={quote(email)}&t={quote(tokens.unsubscribe_token(email))}"


def unsubscribe_url(email: str) -> str:
    """The `List-Unsubscribe` header target — the API, not the website.

    RFC 8058 one-click means a mail client POSTs here with no human present, so it
    has to be a route that exists: PUBLIC_WEB_URL is the Next.js app and serves no
    /api/* path. Split from `unsubscribe_page_url` for exactly that reason.
    """
    return f"{settings.public_api_url.rstrip('/')}/api/email/unsubscribe?{_unsub_query(email)}"


def unsubscribe_page_url(email: str) -> str:
    """The link a human clicks in the footer: a page that confirms, then calls the API."""
    return _web(f"/unsubscribe?{_unsub_query(email)}")


def brief_url(team_id: int, gameweek: int, token: str | None) -> str:
    base = _web(f"/brief/{team_id}")
    return f"{base}?gw={gameweek}&token={token}" if token else f"{base}?gw={gameweek}"


# --- the send_log gate ------------------------------------------------------

def _claim(deps: Deps, email: str, gameweek: int, template: str) -> bool:
    """Reserve (email, gw, template). False if it was already claimed.

    Committed on its own so the claim outlives a crash in the provider call. That
    is the deliberate trade: a crash mid-send loses one email rather than risking
    a duplicate to everyone on the retry.
    """
    try:
        with deps.session_factory() as session, session.begin():
            session.add(SendLog(email=email, gameweek=gameweek, template=template,
                                status="pending", attempts=1))
        return True
    except IntegrityError:
        return False


def _finish(deps: Deps, email: str, gameweek: int, template: str,
            status: str, error: str | None = None) -> None:
    with deps.session_factory() as session, session.begin():
        row = session.scalars(
            select(SendLog).where(SendLog.email == email,
                                  SendLog.gameweek == gameweek,
                                  SendLog.template == template)).first()
        if row is not None:
            row.status = status
            row.error = (error or None) and error[:500]


# --- recipients -------------------------------------------------------------

def _mailable_free_signups(session) -> list[EmailSignup]:
    """Free, CONFIRMED, not unsubscribed. FILTERED IN THE QUERY, not in Python.

    docs/AUDIT.md §D5: loading the list and filtering in a loop is how someone who
    unsubscribed gets mailed by the one code path that forgot the `if`.

    `confirmed_at IS NOT NULL` (double opt-in, 2026-08-06) is the whole of the
    "never mail an unconfirmed address" rule, and it is one clause here rather
    than a check at each call site for exactly the reason above. This is the ONLY
    place free recipients are enumerated.

    Note the direction: it tests for a confirmation that IS there, not for the
    absence of a pending flag. An address that somehow acquired a state nobody
    anticipated is not mailed, which is the safe way round — the same
    allow-list-not-deny-list rule the section vocabulary runs on.

    Paid recipients do not come through here at all: `_send_paid` reads
    `subscriptions`, and someone who has paid us money has proved the address as
    thoroughly as a click ever could.
    """
    return list(session.scalars(
        select(EmailSignup).where(
            EmailSignup.unsubscribed_at.is_(None),
            EmailSignup.confirmed_at.is_not(None),
            EmailSignup.tier != "paid",
        ).order_by(EmailSignup.email)))


def _mailable_free(session, email: str) -> bool:
    """Is this ONE address still eligible for the free send?

    The single-address form of `_mailable_free_signups`' WHERE clause, for the
    retry path — which reads `send_log` and so cannot re-run the list query.
    Deliberately expressed as the same three conditions rather than "not
    pending": a state nobody anticipated is not mailed.
    """
    row = session.get(EmailSignup, email)
    return bool(row
                and row.unsubscribed_at is None
                and row.confirmed_at is not None
                and row.tier != "paid")


def _is_unsubscribed(session, email: str) -> bool:
    row = session.get(EmailSignup, email)
    return row is not None and row.unsubscribed_at is not None


# --- the batch --------------------------------------------------------------

def run_batch(deps: Deps, gameweek: int, *, now: datetime | None = None,
              send_fn=None) -> SendResult:
    """Send both templates for `gameweek`. Safe to re-run; safe to interrupt."""
    now = now or datetime.now(timezone.utc)
    send_fn = send_fn or mail.send
    result = SendResult(gameweek=gameweek)

    if not settings.send_enabled:
        log.info("send: SEND_ENABLED is false — batch for gw=%s not run", gameweek)
        result.errors.append("send_disabled")
        return result

    # Signing must work before anything is claimed: a batch that mints half its
    # tokens and then dies on a weak secret has already burned those send_log rows.
    tokens.assert_secret_usable()

    budget = settings.send_max_recipients
    budget = _send_paid(deps, gameweek, now, send_fn, result, budget)
    budget = _send_free(deps, gameweek, now, send_fn, result, budget)
    if budget <= 0:
        result.capped = True
        log.warning("send: hit SEND_MAX_RECIPIENTS=%s for gw=%s",
                    settings.send_max_recipients, gameweek)
    log.info("send: gw=%s sent=%s skipped=%s failed=%s",
             gameweek, result.sent, result.skipped, result.failed)
    return result


def _send_paid(deps: Deps, gameweek: int, now: datetime, send_fn,
               result: SendResult, budget: int) -> int:
    with deps.session_factory() as session:
        subs = billing.active_subscriptions(session, now=now)

    for sub in subs:
        if budget <= 0:
            return budget
        email = (sub.email or "").strip().lower()
        if not email or sub.fpl_team_id is None:
            result.skipped += 1
            continue
        with deps.session_factory() as session:
            if _is_unsubscribed(session, email):
                # Mail consent is separate from billing (§1.8). They keep access to
                # the web view; they just stop receiving the email.
                result.skipped += 1
                continue
            brief = session.scalars(
                select(Brief).where(Brief.fpl_team_id == sub.fpl_team_id,
                                    Brief.gameweek == gameweek)).first()
            sections = split_sections(brief.brief_md) if brief else None
        if not sections:
            result.skipped += 1
            log.info("send: no persisted brief for team=%s gw=%s — skipping %s",
                     sub.fpl_team_id, gameweek, mail.mask(email))
            continue

        if not _claim(deps, email, gameweek, PAID_TEMPLATE):
            result.skipped += 1
            continue
        budget -= 1

        # exp = current_period_end, so the link dies exactly when access does.
        token = tokens.mint(subscription_id=sub.id, team_id=sub.fpl_team_id,
                            expires_at=sub.current_period_end or now)
        subject, html, text = emails.paid_brief(
            gameweek=gameweek, sections=sections,
            brief_url=brief_url(sub.fpl_team_id, gameweek, token),
            unsubscribe_url=unsubscribe_page_url(email))
        _deliver(deps, send_fn, email, gameweek, PAID_TEMPLATE,
                 subject, html, text, result)
    return budget


def _send_free(deps: Deps, gameweek: int, now: datetime, send_fn,
               result: SendResult, budget: int) -> int:
    with deps.session_factory() as session:
        preview = session.scalars(
            select(Brief).where(Brief.fpl_team_id.is_(None),
                                Brief.draft_id.is_(None),
                                Brief.gameweek == gameweek)).first()
        sections = split_sections(preview.brief_md, PREVIEW_HEADERS) if preview else None
        recipients = [s.email for s in _mailable_free_signups(session)]

    if not sections:
        # The persisted generic preview is the free template's only content
        # (service.persist_generic_preview). No preview => send nothing rather
        # than an empty email.
        log.warning("send: no persisted generic preview for gw=%s — free pass skipped",
                    gameweek)
        result.errors.append("no_generic_preview")
        return budget

    for email in recipients:
        if budget <= 0:
            return budget
        email = email.strip().lower()
        if not _claim(deps, email, gameweek, FREE_TEMPLATE):
            result.skipped += 1
            continue
        budget -= 1
        subject, html, text = emails.free_preview(
            gameweek=gameweek, sections=sections, landing_url=_web("/"),
            unsubscribe_url=unsubscribe_page_url(email))
        _deliver(deps, send_fn, email, gameweek, FREE_TEMPLATE,
                 subject, html, text, result)
    return budget


def _deliver(deps: Deps, send_fn, email: str, gameweek: int, template: str,
             subject: str, html: str, text: str, result: SendResult) -> None:
    """One recipient, fully isolated. Never raises into the batch loop."""
    try:
        send_fn(to=email, subject=subject, html=html, text=text,
                unsubscribe_url=unsubscribe_url(email))
    except Exception as exc:                      # provider error, bad address, DNS
        # Broad on purpose: this IS the isolation boundary (§D4.2). A narrower
        # except here would let an unexpected exception type end the batch.
        reason = f"{type(exc).__name__}: {exc}"[:300]
        _finish(deps, email, gameweek, template, "failed", reason)
        result.failed += 1
        result.errors.append(f"{mail.mask(email)}: {reason[:120]}")
        log.warning("send: %s failed for %s: %s", template, mail.mask(email), reason)
        return
    _finish(deps, email, gameweek, template, "sent")
    result.sent += 1


def retry_failed(deps: Deps, gameweek: int, *, send_fn=None) -> SendResult:
    """Re-attempt rows left `failed`. One extra attempt, then they stay failed.

    Spec §3 schedules this at +1h. Bounded by `attempts >= 2` so a permanently bad
    address cannot be retried forever by a caller that reruns the job.
    """
    send_fn = send_fn or mail.send
    result = SendResult(gameweek=gameweek)
    if not settings.send_enabled:
        result.errors.append("send_disabled")
        return result

    with deps.session_factory() as session:
        rows = list(session.scalars(
            select(SendLog).where(SendLog.gameweek == gameweek,
                                  SendLog.status == "failed",
                                  SendLog.attempts < 2)))
        targets = [(r.email, r.template) for r in rows]

    for email, template in targets:
        with deps.session_factory() as session, session.begin():
            row = session.scalars(
                select(SendLog).where(SendLog.email == email,
                                      SendLog.gameweek == gameweek,
                                      SendLog.template == template)).first()
            if row is None or row.attempts >= 2:
                continue
            row.attempts += 1
            row.status = "pending"

        payload = _rebuild(deps, email, gameweek, template)
        if payload is None:
            _finish(deps, email, gameweek, template, "failed", "content unavailable")
            result.failed += 1
            continue
        subject, html, text = payload
        _deliver(deps, send_fn, email, gameweek, template, subject, html, text, result)
    return result


def _rebuild(deps: Deps, email: str, gameweek: int,
             template: str) -> tuple[str, str, str] | None:
    """Re-render one email for a retry. None if it must not be sent after all.

    CONSENT IS RE-CHECKED HERE, not only at claim time (2026-08-06). A retry runs
    an hour after the batch and reads `send_log`, which is a record of what we
    TRIED to send — it is not a permission. Between the two, someone can hit
    unsubscribe, and before this guard the one-click unsubscribe they were
    legally required to be offered would have been overridden by a retry an hour
    later. Double opt-in made the same hole wider: a row claimed while an address
    was confirmed must not be redelivered if the confirmation is gone.

    This sits in `_rebuild` rather than in `retry_failed`'s loop because both
    templates funnel through here, so one guard covers both paths — and a future
    third template gets it for free instead of needing to remember.
    """
    with deps.session_factory() as session:
        if _is_unsubscribed(session, email):
            return None
        if template == FREE_TEMPLATE and not _mailable_free(session, email):
            return None

    if template == FREE_TEMPLATE:
        with deps.session_factory() as session:
            preview = session.scalars(
                select(Brief).where(Brief.fpl_team_id.is_(None),
                                    Brief.draft_id.is_(None),
                                    Brief.gameweek == gameweek)).first()
        if preview is None:
            return None
        return emails.free_preview(
            gameweek=gameweek,
            sections=split_sections(preview.brief_md, PREVIEW_HEADERS),
            landing_url=_web("/"), unsubscribe_url=unsubscribe_page_url(email))

    now = datetime.now(timezone.utc)
    with deps.session_factory() as session:
        sub = next((s for s in billing.active_subscriptions(session, now=now)
                    if (s.email or "").strip().lower() == email), None)
        if sub is None or sub.fpl_team_id is None:
            return None
        brief = session.scalars(
            select(Brief).where(Brief.fpl_team_id == sub.fpl_team_id,
                                Brief.gameweek == gameweek)).first()
        if brief is None:
            return None
        sections = split_sections(brief.brief_md)
        token = tokens.mint(subscription_id=sub.id, team_id=sub.fpl_team_id,
                            expires_at=sub.current_period_end or now)
        team_id = sub.fpl_team_id
    return emails.paid_brief(gameweek=gameweek, sections=sections,
                             brief_url=brief_url(team_id, gameweek, token),
                             unsubscribe_url=unsubscribe_page_url(email))
