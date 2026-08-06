"""Orchestration between the HTTP routes and the graph/data contracts.

The routes stay thin; everything that touches the DB or the pipeline lives here.
We call the graph via `graph.cache.get_or_generate` (its contract), never the
engine formula — the deterministic/LLM boundary holds at the delivery layer too.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from fpl.db import SessionLocal
from fpl.models import (
    Account, Brief, DraftSquad, EmailSignup, EpScore, EuropeanWeek, Player,
    SendLog, Subscription, TeamNote,
)
from . import accounts, billing, tokens
from .config import settings
from fpl.sync import derive_mode, next_gameweek, sync_fpl
from graph.cache import get_or_generate
from graph.config import MAX_PER_CLUB, POSITION_QUOTA, SQUAD_BUDGET, SQUAD_SIZE
from graph.data import load_teams
from graph.deps import Deps
from graph.nodes.squad_analyst import resolve_squad
from graph.nodes.writer import STUB_MODEL
from graph.sections import PREVIEW_HEADERS, gate_sections, split_sections
from graph.state import BriefState, RequestCtx
from writer.generic import render_generic_stub

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset — never a naive string.

    Every timestamp column is written with `models.utcnow()`, i.e. aware UTC, but
    SQLite drops the tzinfo on the way back out (Postgres does not). Serializing
    the naive value as-is produces "2026-07-30T14:19:55", which `new Date()` in a
    browser parses as LOCAL time — silently shifting every rendered timestamp by
    the viewer's UTC offset. That is how the admin dashboard reported an 8-hour-old
    sync that was two hours old, from IST (2026-07-30).

    A naive value reaching here is UTC by construction, so stamping UTC on it is a
    statement of what it already is, not a conversion.
    """
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()

# THE PAYWALL FLIP (Stage B, 2026-08-01). Was a `False` constant through Stage A;
# it is now the `PAYWALL_ENABLED` env flag, which is the whole change the spec §6
# flip asked for — "no code change" from here on.
#
# Gating itself did NOT move and must never move: it is `graph.sections.gate_sections`
# and nothing else. Do NOT reintroduce per-route gating logic; the allow-list is
# only auditable if there is exactly one of it.
#
# Read through `paywall_on()` rather than at import time, so a test (and the
# operator) can flip the setting without reimporting the module.
# The Stage A `PAYWALL` constant is deliberately GONE rather than aliased: a module
# constant cannot track an env flag, and a name that looks like the old one while
# silently reading a stale value is the worst of both.
def paywall_on() -> bool:
    return settings.paywall_enabled


# --- Who may generate a free brief (2026-08-06) ----------------------------
#
# Same shape as paywall_on() above and for the same reason: read through a
# function, never bind a module constant to an env flag.
#
# This is the ONLY definition of the mode vocabulary. The gate itself is
# `_enforce_brief_access` in api/routes.py and lives nowhere else — the paywall
# taught this lesson already (three copies of the section vocabulary that
# disagreed), so there is one list and one enforcement point.
FREE_BRIEF_ACCESS_MODES = frozenset({"open", "second_gate", "login_first"})


def access_mode() -> str:
    return settings.free_brief_access


# --- FastAPI dependency ----------------------------------------------------

def get_deps() -> Deps:
    """Default Deps (prod). Tests override this to inject an in-memory factory.

    client=None: Stage A is pre-season (draft path), which never needs the live
    FPL client. The IN_SEASON team path builds a client on demand (Stage B).
    """
    return Deps(session_factory=SessionLocal, client=None)


# --- draft validation ------------------------------------------------------

class InvalidDraft(Exception):
    """A submitted draft breaks an FPL squad rule. Carries a specific code."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


def validate_draft(deps: Deps, player_ids: list[int], captain_id: int | None) -> None:
    """Reject an illegal draft squad. Raises InvalidDraft; returns None if legal.

    POST /api/brief is PUBLIC and unauthenticated. The picker enforces the same
    rules client-side, but that is a courtesy to the user, not a control — a
    hand-rolled request bypasses it completely. Everything the picker prevents is
    re-checked here against the real `players` rows, because the client's idea of
    a player's price, position and club is not evidence.

    Checks run cheapest-and-most-fundamental first so the error a caller gets
    names the thing actually wrong with their squad, not a downstream symptom.
    """
    if len(player_ids) != SQUAD_SIZE:
        raise InvalidDraft("squad_size",
                           f"A squad is exactly {SQUAD_SIZE} players, got {len(player_ids)}.")
    if len(set(player_ids)) != len(player_ids):
        raise InvalidDraft("duplicate_players", "The same player appears more than once.")

    with deps.session_factory() as session:
        rows = session.scalars(select(Player).where(Player.id.in_(player_ids))).all()

    found = {p.id: p for p in rows}
    missing = [pid for pid in player_ids if pid not in found]
    if missing:
        raise InvalidDraft("unknown_players",
                           f"Unknown player ids: {sorted(missing)}.")

    counts: dict[int, int] = {}
    clubs: dict[int, int] = {}
    total = 0.0
    for pid in player_ids:
        p = found[pid]
        counts[p.position] = counts.get(p.position, 0) + 1
        clubs[p.team_id] = clubs.get(p.team_id, 0) + 1
        total += float(p.price)

    if counts != POSITION_QUOTA:
        want = "/".join(str(POSITION_QUOTA[k]) for k in sorted(POSITION_QUOTA))
        got = "/".join(str(counts.get(k, 0)) for k in sorted(POSITION_QUOTA))
        raise InvalidDraft("position_counts",
                           f"A squad is {want} GK/DEF/MID/FWD, got {got}.")

    # Compare on rounded £0.1m: prices are Numeric(4,1), so a squad that sums to
    # exactly 100.0 must not fail on binary float dust.
    if round(total, 1) > SQUAD_BUDGET:
        raise InvalidDraft("budget_exceeded",
                           f"Squad costs £{round(total, 1)}m, budget is £{SQUAD_BUDGET}m.")

    over = [tid for tid, n in clubs.items() if n > MAX_PER_CLUB]
    if over:
        with deps.session_factory() as session:
            teams = load_teams(session)
        named = ", ".join(
            f"{teams.get(tid, {}).get('short_name', tid)} ({clubs[tid]})" for tid in over
        )
        raise InvalidDraft("club_over_cap",
                           f"Max {MAX_PER_CLUB} players from one club: {named}.")

    if captain_id is not None and captain_id not in found:
        raise InvalidDraft("captain_not_in_squad",
                           "The captain must be one of the 15 selected players.")


# --- draft persistence -----------------------------------------------------

def create_draft(deps: Deps, player_ids: list[int], captain_id: int | None,
                 email: str | None) -> uuid.UUID:
    did = uuid.uuid4()
    with deps.session_factory() as session, session.begin():
        session.add(DraftSquad(id=did, player_ids=player_ids,
                               captain_id=captain_id, email=email))
    return did


def draft_exists(deps: Deps, draft_id: uuid.UUID) -> bool:
    with deps.session_factory() as session:
        return session.get(DraftSquad, draft_id) is not None


# --- brief generation ------------------------------------------------------

def _preseason_state(draft_id: uuid.UUID) -> BriefState:
    return BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=draft_id, gameweek_anchor=0,
        requested_at=datetime.now(timezone.utc)))


def _brief_id(session, req: RequestCtx) -> int | None:
    if req.draft_id is not None:
        return session.scalar(select(Brief.id).where(Brief.draft_id == req.draft_id))
    return session.scalar(
        select(Brief.id).where(Brief.fpl_team_id == req.fpl_team_id,
                               Brief.gameweek == req.gameweek_anchor))


def _shape(deps: Deps, state: BriefState, final: BriefState, cached: bool) -> dict:
    # squad ctx supplies the header card; populated on a miss, re-resolved on a hit.
    squad = final.squad
    if squad is None:
        squad, _ = resolve_squad(state, deps)
    with deps.session_factory() as session:
        brief_id = _brief_id(session, state.request)
    # Spec §1.1: this route returns the FREE view. Full sections are never served
    # here — the full view is §1.2 with a minted token.
    sections, locked = gate_sections(split_sections(final.brief_md),
                                     paywall=paywall_on())
    return {
        "brief_id": brief_id,
        "draft_id": str(state.request.draft_id) if state.request.draft_id else None,
        "gameweek": state.request.gameweek_anchor,
        "team_name": squad.team_name if squad else None,
        "overall_rank": squad.overall_rank if squad else None,
        "bank": squad.bank if squad else None,
        "sections": sections,
        "locked_sections": locked,
        "cached": cached,
    }


class BriefError(Exception):
    """Carries a graph PipelineError code up to the route for HTTP mapping."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(code)


def generate_brief(deps: Deps, state: BriefState, *, before_generate=None) -> dict:
    """Run cache-or-generate for `state`, return the §1.1 response dict.

    Raises BriefError(code) on a pipeline error so the route maps it to HTTP.
    `before_generate` fires only on a real cache miss (rate-limit hook).
    """
    final, cached = get_or_generate(state, deps, before_generate=before_generate)
    if final.error is not None:
        raise BriefError(final.error.code, final.error.message)
    return _shape(deps, state, final, cached)


def brief_from_draft(deps: Deps, draft_id: uuid.UUID, *, before_generate=None) -> dict:
    return generate_brief(deps, _preseason_state(draft_id), before_generate=before_generate)


def account_from_link_token(deps: Deps, token: str | None, team_id: int) -> int | None:
    """A full-view JWT -> the account id it belongs to, or None. Never raises.

    THE EMAILED LINK IS NO LONGER A GRANT; IT IS A POINTER AT AN ACCOUNT
    (accounts build, 2026-08-04). Three things must all hold:

    1. The token verifies and its `team_id` claim names THIS team — unchanged, and
       still what stops a token for team A opening team B.
    2. Its `sub` claim resolves to a real subscription.
    3. That subscription is LINKED TO AN ACCOUNT.

    (3) is the new one and it is the point: a token forwarded to someone with no
    account, or minted against a subscription the webhook never managed to link,
    resolves to nothing and opens nothing. The route exchanges the result for a
    session, so the token stops being a standing credential and becomes a one-time
    introduction — after which every subsequent request is gated by the session,
    and revoking the subscription takes effect on the next read either way.

    Entitlement is NOT decided here; `get_team_brief` re-reads it from the account.
    """
    claimed_team = tokens.team_id_if_valid(token)
    if claimed_team is None or claimed_team != team_id:
        return None
    claims = tokens.claims_if_valid(token)
    if claims is None:
        return None
    with deps.session_factory() as session:
        account = billing.account_for_subscription_id(session, claims.get("sub"))
        return account.id if account is not None else None


def get_team_brief(deps: Deps, team_id: int, gw: int,
                   account_id: int | None = None) -> dict | None:
    """Read an already-persisted season brief (§1.2). None if absent.

    Two independent conditions must BOTH hold for the paid view, and the
    redundancy is deliberate:

    1. **A session identifies an account.** `account_id` comes from the session
       cookie, or from a full-view link that was just exchanged for one
       (`account_from_link_token`). Anonymous is not an error — it is the free view.
    2. **That account has a currently-entitled subscription for this team.**
       Re-read from the DB on every request, never inferred from the cookie. This
       is what makes a refund or chargeback take effect *immediately* (audit §D6)
       rather than at session expiry.

    Either condition failing degrades to the free view. It never errors: §1.2's
    contract is "no/invalid credential -> free view", and telling a caller why
    theirs failed is free help for forging a better one.

    THE FREE VIEW IS NOT A LOGIN WALL. `account_id=None` returns the brief with
    paid sections locked — a 401 here would put a login in front of content the
    product gives away, which the accounts build was explicitly not allowed to do.
    """
    with deps.session_factory() as session:
        row = session.scalars(
            select(Brief).where(Brief.fpl_team_id == team_id, Brief.gameweek == gw)
        ).first()
        if row is None:
            return None
        full = billing.entitled_for_account(session, account_id, team_id)
        sections, locked = gate_sections(split_sections(row.brief_md),
                                         paywall=paywall_on() and not full)
        return {
            "brief_id": row.id,
            "gameweek": row.gameweek,
            "team_name": None,
            "overall_rank": None,
            "bank": None,
            "sections": sections,
            "locked_sections": locked,
            "cached": True,
        }


# --- read endpoints --------------------------------------------------------

def mode_of(deps: Deps) -> str:
    with deps.session_factory() as session:
        return derive_mode(session)


def players_list(deps: Deps) -> list[dict]:
    """Every player in the table, whatever their status.

    NO WHERE CLAUSE AND NO LIMIT, deliberately (2026-08-06). This is the draft
    picker's entire universe: a player filtered out here cannot be picked at all,
    and an injured or suspended player is still a legal pick — people draft them
    on purpose, for the fixture after next. So availability is shipped as data
    and rendered as a visual flag, never as an omission. `scripts/player_coverage.py`
    boundary B fails if a filter appears here.

    `full_name` is shipped because the picker searches it: FPL's `web_name` is
    often a surname or an initialled form ("M.Salah"), so without the full name
    typing someone's actual first name matches nothing.
    """
    with deps.session_factory() as session:
        teams = load_teams(session)
        rows = session.scalars(select(Player)).all()
        return [
            {
                "id": p.id,
                "web_name": p.web_name,
                "full_name": p.full_name,
                "team_short": teams.get(p.team_id, {}).get("short_name", "?"),
                "position": _POS.get(p.position, "?"),
                "price": float(p.price),
                # 'a' available / 'd' doubtful / 'i' injured / 's' suspended /
                # 'u' unavailable / 'n' on loan. `news` is NOT shipped: it is
                # free text FPL writes, it is the widest untrusted string on this
                # payload, and the picker only needs the one-letter state.
                "status": p.status,
            }
            for p in rows
        ]


def persist_generic_preview(deps: Deps, gameweek: int | None = None) -> dict:
    """Generate and persist THE generic preview for a gameweek (Layer 4 §6).

    The send job's free template has no other content source (`send._send_free`),
    and the preview route regenerates on every call — so without a persisted row,
    every free recipient would either get nothing or get a separately-generated
    brief, and "the weekly preview" would not be one artifact.

    Singleton by construction: `uq_brief_generic` is a unique index on `gameweek`
    where both owner columns are NULL (migration b2c3d4e5f6a7). Re-running for the
    same gameweek UPDATES that row rather than inserting a second one, so a rerun
    of the batch refreshes the preview instead of failing on the constraint.
    """
    md = render_generic_stub(deps, gameweek)
    gw = gameweek
    if gw is None:
        first = md.splitlines()[0] if md else ""
        gw = int("".join(c for c in first if c.isdigit()) or 0)

    with deps.session_factory() as session, session.begin():
        row = session.scalars(
            select(Brief).where(Brief.fpl_team_id.is_(None),
                                Brief.draft_id.is_(None),
                                Brief.gameweek == gw)).first()
        if row is None:
            row = Brief(fpl_team_id=None, draft_id=None, gameweek=gw, brief_md=md,
                        # The generic preview is not squad-derived, so it has no
                        # input hash in the §4 sense. Namespaced so it can never
                        # collide with a real cache key and get served as one.
                        input_hash=f"generic:{gw}",
                        model=STUB_MODEL, tokens_in=0, tokens_out=0)
            session.add(row)
            created = True
        else:
            row.brief_md = md
            row.model = STUB_MODEL
            created = False
        session.flush()
        brief_id = row.id
    return {"brief_id": brief_id, "gameweek": gw, "created": created,
            "sections": list(split_sections(md, PREVIEW_HEADERS))}


def preview(deps: Deps, gameweek: int | None = None) -> dict:
    md = render_generic_stub(deps, gameweek)
    # anchor line: "# Gameweek {n} — ..."; pull n back out for the response field.
    gw = gameweek
    if gw is None:
        first = md.splitlines()[0] if md else ""
        gw = int("".join(c for c in first if c.isdigit()) or 0)
    return {
        "gameweek": gw,
        # The preview has its OWN vocabulary (never lockable) — passing
        # PREVIEW_HEADERS keeps its keys explicit instead of accidentally slugged.
        "sections": split_sections(md, PREVIEW_HEADERS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def health(deps: Deps) -> dict:
    with deps.session_factory() as session:
        mode = derive_mode(session)
        ng = next_gameweek(session)
        last = session.scalar(select(func.max(EpScore.computed_at)))
    return {
        "last_sync": iso_utc(last),
        "mode": mode,
        "next_gw": ng.id if ng else None,
        "next_deadline": iso_utc(ng.deadline) if ng else None,
        # The access mode, so the landing page can say up front that a brief
        # needs a sign-in rather than letting someone pick fifteen players and
        # only then hit a 403. NOT the enforcement — `_enforce_brief_access`
        # decides, server-side, and a client that ignores this still gets gated.
        # Safe to publish: it describes the site's own policy, not the caller.
        "free_brief_access": access_mode(),
    }


def run_sync(deps: Deps) -> dict:
    """Run `sync_fpl()` synchronously and return its SyncResult as JSON.

    The free-tier deployment has no in-process scheduler (a sleeping Render free
    service can't hold APScheduler), so an external cron calls this behind
    ADMIN_TOKEN instead. `deps.client=None` in prod => sync_fpl builds and closes
    a real FPLClient; tests inject the frozen-asset fake. Sync stays
    abort-or-nothing: a failed fetch raises and leaves the DB untouched.
    """
    return asdict(sync_fpl(client=deps.client, session_factory=deps.session_factory))


def signup(deps: Deps, email: str, team_id: int | None, source: str | None) -> None:
    """Idempotent join. A repeat signup FILLS BLANKS ONLY — it never overwrites.

    This was `session.merge(EmailSignup(..., tier="free", ...))`, which writes every
    column it was handed. Two ways that loses data, both reachable today:

    - POST /api/brief with an email calls this with `team_id=None, source="draft"`
      (routes._safe_signup). A subscriber who had signed up from the homepage with
      their team id got that id NULLed and their acquisition source rewritten.
    - `tier="free"` is unconditional, so in Stage B any re-signup — including the
      one the draft modal fires automatically — DEMOTES A PAYING SUBSCRIBER. The
      gating would then read `tier` and correctly withhold what they paid for.

    So: insert on first sight, and afterwards only fill in what is still empty.
    Tier is never written here at all; billing owns it.
    """
    with deps.session_factory() as session, session.begin():
        row = session.get(EmailSignup, email)
        if row is None:
            session.add(EmailSignup(email=email, fpl_team_id=team_id,
                                    tier="free", source=source))
            return
        if row.fpl_team_id is None and team_id is not None:
            row.fpl_team_id = team_id
        if row.source is None and source is not None:
            row.source = source


# --- mail consent (§1.8) ---------------------------------------------------
#
# UNSUBSCRIBE AND DELETE ARE DIFFERENT ACTIONS AND MUST STAY DIFFERENT.
#
# Unsubscribe is a MAIL preference and leaves a tombstone; delete removes the row.
# Two reasons the tombstone is not an over-complication:
#
#   1. Without a row, the next signup-form submission or list re-import silently
#      re-adds someone who asked to leave. The tombstone is what makes "no" stick.
#   2. `email_signups.tier` is NOT entitlement (that is `subscriptions`, see
#      api/billing.py), so a paying subscriber who stops the email keeps every bit
#      of the product they paid for — the web view, the token links, the billing
#      relationship. Deleting the row on unsubscribe would conflate the two and
#      cancel a service they are still paying for.

def unsubscribe(deps: Deps, email: str) -> bool:
    """Stop mail for `email`. Idempotent. True if a row was found.

    Deliberately returns the same shape whether or not the address was on the list:
    the route answers identically either way, so the endpoint is not an oracle for
    "is this person a customer".
    """
    email = email.strip().lower()
    with deps.session_factory() as session, session.begin():
        row = session.get(EmailSignup, email)
        if row is None:
            return False
        if row.unsubscribed_at is None:
            row.unsubscribed_at = datetime.now(timezone.utc)
        return True


def resubscribe(deps: Deps, email: str) -> bool:
    """Clear the tombstone — the "actually, keep sending" button on the landing page."""
    email = email.strip().lower()
    with deps.session_factory() as session, session.begin():
        row = session.get(EmailSignup, email)
        if row is None:
            return False
        row.unsubscribed_at = None
        return True


def delete_signup(deps: Deps, email: str) -> dict:
    """Erase the mailing-list row outright ("delete my data").

    Returns what was removed and what deliberately was not. Subscriptions are NOT
    deleted: they are the record of a financial transaction, which we are required
    to keep and which the payment provider holds anyway. The row is anonymised
    instead — the email is cleared so it cannot be mailed or looked up, and the
    billing history survives for accounting.
    """
    email = email.strip().lower()
    with deps.session_factory() as session, session.begin():
        row = session.get(EmailSignup, email)
        removed = row is not None
        if row is not None:
            session.delete(row)
        subs = session.scalars(
            select(Subscription).where(Subscription.email == email)).all()
        for sub in subs:
            sub.email = ""            # anonymised, not deleted — see docstring
        # Send history keyed by the address goes too; it exists to stop duplicate
        # sends, and there will be no more sends.
        logs = session.scalars(select(SendLog).where(SendLog.email == email)).all()
        for entry in logs:
            session.delete(entry)
        result = {"signup_removed": removed, "subscriptions_anonymised": len(subs),
                  "send_log_rows_removed": len(logs)}
        # The account, if this address has one. Same transaction: a delete that
        # removes the mailing-list row and leaves a live login behind is not a
        # delete, and two transactions could fail between them.
        account = accounts.get_account_by_email(session, email)
        if account is not None:
            result.update(_purge_account(session, account))
        return result


def _purge_account(session, account) -> dict:
    """Remove an account and everything that hangs off it. Caller owns the transaction.

    THE CASCADE, WRITTEN OUT RATHER THAN LEFT TO THE DATABASE. `sessions` has
    `ON DELETE CASCADE` and `subscriptions.account_id` has `ON DELETE SET NULL`,
    but SQLite does not enforce either unless `PRAGMA foreign_keys=ON`, and the
    whole test suite runs on SQLite — so the guarantee would be asserted exactly
    where it is not enforced. Doing it explicitly makes the behaviour identical on
    both engines and, more importantly, makes it *readable*: what a delete destroys
    should not require reading a migration to find out.

    What survives, and why:

    - **Subscription ROWS survive, unlinked and anonymised.** They are the record
      of a financial transaction; we are required to keep them and the payment
      provider holds them anyway. `account_id` and `email` are cleared, so the row
      is no longer reachable from any identity and can never re-entitle anyone.
    - **Nothing about billing is CANCELLED here.** Deleting an account does not
      tell Paddle to stop charging a card, and pretending otherwise in a return
      value would be the single most expensive lie this codebase could tell. The
      route surfaces `subscription_cancel_required` so the UI says it out loud.
    """
    from fpl.models import MagicLinkToken

    sessions_removed = accounts.destroy_sessions_for_account(session, account.id)
    links_removed = session.execute(
        delete(MagicLinkToken).where(MagicLinkToken.email == account.email)
    ).rowcount or 0
    subs = session.scalars(
        select(Subscription).where(Subscription.account_id == account.id)).all()
    still_paying = False
    for sub in subs:
        still_paying = still_paying or billing.is_entitled(sub)
        sub.account_id = None
        sub.email = ""
    session.delete(account)
    return {
        "account_removed": True,
        "sessions_removed": sessions_removed,
        "magic_links_removed": links_removed,
        "subscriptions_unlinked": len(subs),
        # True => there is a live subscription that will KEEP BILLING until it is
        # cancelled in Paddle. Not an incidental field; the UI must say this.
        "subscription_cancel_required": still_paying,
    }


def delete_account(deps: Deps, account_id: int) -> dict:
    """Delete the signed-in account: the row, its sessions, its links, its signup.

    The session-authenticated sibling of `delete_signup`. Both end in
    `_purge_account`, so "delete me" means the same thing whichever door it came
    through — the mailed link deletes by address, this deletes by session.
    """
    with deps.session_factory() as session, session.begin():
        account = session.get(Account, account_id)
        if account is None:
            return {"account_removed": False}
        email = account.email
        signup = session.get(EmailSignup, email)
        if signup is not None:
            session.delete(signup)
        logs = session.scalars(select(SendLog).where(SendLog.email == email)).all()
        for entry in logs:
            session.delete(entry)
        result = _purge_account(session, account)
        return {"signup_removed": signup is not None,
                "send_log_rows_removed": len(logs), **result}


# --- curated-table writes --------------------------------------------------
#
# SKIP-IF-IDENTICAL IS LOAD-BEARING, NOT AN OPTIMISATION.
#
# `max(team_notes.updated_at)` and `max(european_weeks.updated_at)` ARE the
# `curated_notes_version` / `euro_weeks_version` halves of the brief cache key
# (graph/data.py, docs/CLAUDE.md "Configuration"). The admin UI re-sends whatever
# is in the textarea — including on a save where nothing was typed — so writing
# unconditionally would bump the version and invalidate EVERY cached brief on a
# no-op click. Compare first; write only on a real change.
#
# Both return whether they wrote, so the UI can say so and the tests can assert it.

def upsert_note(deps: Deps, team_id: int, note: str) -> bool:
    with deps.session_factory() as session, session.begin():
        row = session.get(TeamNote, team_id)
        if row is not None and row.note == note:
            return False
        session.merge(TeamNote(team_id=team_id, note=note, is_sample=False))
        return True


def upsert_euro_week(deps: Deps, team_id: int, gameweek: int,
                     competition: str, tie_importance: int) -> bool:
    with deps.session_factory() as session, session.begin():
        row = session.get(EuropeanWeek, (team_id, gameweek))
        if (row is not None and row.competition == competition
                and row.tie_importance == tie_importance):
            return False
        session.merge(EuropeanWeek(
            team_id=team_id, gameweek=gameweek,
            competition=competition, tie_importance=tie_importance))
        return True


def replace_euro_weeks(deps: Deps, rows: list[dict]) -> int:
    """Whole-table save from the admin editor. Returns the number of rows changed.

    The editor edits a grid, so add / edit / remove all arrive as one submission:
    upsert everything sent, delete everything absent. Per-row skip-if-identical for
    the reason above — this endpoint is the one most likely to be handed a table
    that has not actually changed.
    """
    keep = {(r["team_id"], r["gameweek"]) for r in rows}
    changed = 0
    with deps.session_factory() as session, session.begin():
        existing = {(r.team_id, r.gameweek): r
                    for r in session.scalars(select(EuropeanWeek))}
        for key, row in existing.items():
            if key not in keep:
                session.delete(row)
                changed += 1
        for r in rows:
            cur = existing.get((r["team_id"], r["gameweek"]))
            if (cur is not None and cur.competition == r["competition"]
                    and cur.tie_importance == r["tie_importance"]):
                continue
            session.merge(EuropeanWeek(**r))
            changed += 1
    return changed
