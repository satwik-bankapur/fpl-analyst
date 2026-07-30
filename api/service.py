"""Orchestration between the HTTP routes and the graph/data contracts.

The routes stay thin; everything that touches the DB or the pipeline lives here.
We call the graph via `graph.cache.get_or_generate` (its contract), never the
engine formula — the deterministic/LLM boundary holds at the delivery layer too.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import func, select

from fpl.db import SessionLocal
from fpl.models import (
    Brief, DraftSquad, EmailSignup, EpScore, EuropeanWeek, Player, TeamNote,
)
from fpl.sync import derive_mode, next_gameweek, sync_fpl
from graph.cache import get_or_generate
from graph.config import MAX_PER_CLUB, POSITION_QUOTA, SQUAD_BUDGET, SQUAD_SIZE
from graph.data import load_teams
from graph.deps import Deps
from graph.nodes.squad_analyst import resolve_squad
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

# Stage A ships no paywall: every brief returns in full with `locked_sections: []`
# (layer5 §6). The GATING MECHANISM is built and proven fail-closed
# (`graph.sections.gate_sections` + tests/test_sections.py) so the Stage B flip is
# a one-line change here — replace this constant with the PAYWALL_ENABLED env
# setting. Do NOT reintroduce per-route gating logic; it lives in one function on
# purpose, because the allow-list is only auditable in one place.
PAYWALL = False


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
    sections, locked = gate_sections(split_sections(final.brief_md), paywall=PAYWALL)
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


def get_team_brief(deps: Deps, team_id: int, gw: int) -> dict | None:
    """Read an already-persisted season brief (§1.2 free view). None if absent.

    Stage A never generates season briefs, so this is a pure read — no live client,
    no header enrichment (rank/bank come from the send job's squad ctx in Stage B).
    """
    with deps.session_factory() as session:
        row = session.scalars(
            select(Brief).where(Brief.fpl_team_id == team_id, Brief.gameweek == gw)
        ).first()
        if row is None:
            return None
        sections, locked = gate_sections(split_sections(row.brief_md), paywall=PAYWALL)
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
    with deps.session_factory() as session:
        teams = load_teams(session)
        rows = session.scalars(select(Player)).all()
        return [
            {
                "id": p.id,
                "web_name": p.web_name,
                "team_short": teams.get(p.team_id, {}).get("short_name", "?"),
                "position": _POS.get(p.position, "?"),
                "price": float(p.price),
            }
            for p in rows
        ]


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
    with deps.session_factory() as session, session.begin():
        session.merge(EmailSignup(email=email, fpl_team_id=team_id,
                                  tier="free", source=source))


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
