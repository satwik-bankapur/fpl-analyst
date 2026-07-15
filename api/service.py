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
from graph.data import load_teams
from graph.deps import Deps
from graph.nodes.squad_analyst import resolve_squad
from graph.state import BriefState, RequestCtx
from writer.generic import render_generic_stub

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# brief_md `## ` headers -> canonical section keys. Forward-compatible with the
# Stage B locked_sections vocabulary; unknown headers fall back to a slug.
_SECTION_KEYS = {
    "Verdict": "verdict",
    "Transfer": "transfer",
    "Captaincy": "captaincy",
    "Bench order": "bench_plan",
    "Watchlist": "watchlist",
    "Fixture notes": "fixtures",
}


# --- FastAPI dependency ----------------------------------------------------

def get_deps() -> Deps:
    """Default Deps (prod). Tests override this to inject an in-memory factory.

    client=None: Stage A is pre-season (draft path), which never needs the live
    FPL client. The IN_SEASON team path builds a client on demand (Stage B).
    """
    return Deps(session_factory=SessionLocal, client=None)


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


def split_sections(brief_md: str) -> dict[str, str]:
    """brief_md -> {section_key: markdown}. Header card lines (before the first
    `## `) are dropped — the response carries team/rank/bank as structured fields."""
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in brief_md.splitlines():
        if line.startswith("## "):
            if key is not None:
                out[key] = "\n".join(buf).strip()
            head = line[3:].strip()
            key = _SECTION_KEYS.get(head, head.lower().replace(" ", "_"))
            buf = []
        elif key is not None:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out


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
    return {
        "brief_id": brief_id,
        "draft_id": str(state.request.draft_id) if state.request.draft_id else None,
        "gameweek": state.request.gameweek_anchor,
        "team_name": squad.team_name if squad else None,
        "overall_rank": squad.overall_rank if squad else None,
        "bank": squad.bank if squad else None,
        "sections": split_sections(final.brief_md),
        "locked_sections": [],  # Stage A: no paywall (Stage B populates this)
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
        return {
            "brief_id": row.id,
            "gameweek": row.gameweek,
            "team_name": None,
            "overall_rank": None,
            "bank": None,
            "sections": split_sections(row.brief_md),
            "locked_sections": [],
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
        "sections": split_sections(md),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def health(deps: Deps) -> dict:
    with deps.session_factory() as session:
        mode = derive_mode(session)
        ng = next_gameweek(session)
        last = session.scalar(select(func.max(EpScore.computed_at)))
    return {
        "last_sync": last.isoformat() if last else None,
        "mode": mode,
        "next_gw": ng.id if ng else None,
        "next_deadline": ng.deadline.isoformat() if ng else None,
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


def upsert_note(deps: Deps, team_id: int, note: str) -> None:
    with deps.session_factory() as session, session.begin():
        session.merge(TeamNote(team_id=team_id, note=note, is_sample=False))


def upsert_euro_week(deps: Deps, team_id: int, gameweek: int,
                     competition: str, tie_importance: int) -> None:
    with deps.session_factory() as session, session.begin():
        session.merge(EuropeanWeek(
            team_id=team_id, gameweek=gameweek,
            competition=competition, tie_importance=tie_importance))
