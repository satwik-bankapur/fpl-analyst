"""Entry point + cache integration (§4).

input_hash = hash(sorted picks + captain + bank + ep config_version +
gameweek_anchor + curated_notes_version + euro_weeks_version).

The curated-notes and euro-weeks versions are REAL sources as of 2026-07-10:
max(updated_at) over `team_notes` / `european_weeks`, resolved from the DB in
`graph.data`. (They were placeholder constants when this module was written; that
is no longer true.) The first time either curated table is populated or edited,
the version string changes and every cached brief invalidates exactly once — that
mass miss is by design.

Cache purpose (§5): the deterministic chain is cheap to rerun; the only guarded
cost is the Writer (an LLM call in Layer 4). We resolve the squad cheaply, hash,
and only run the graph on a miss.
"""
from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select

from fpl.models import Brief

from .data import (
    curated_notes_version, ep_config_version, euro_weeks_version, load_ep_map,
    resolve_ep_anchor,
)
from .deps import Deps
from .nodes.squad_analyst import resolve_squad
from .nodes.writer import STUB_MODEL
from .state import BriefState
from .graph import run_brief

log = logging.getLogger("graph.cache")


def compute_input_hash(squad, gameweek_anchor: int, ep_cfg_version: str,
                       notes_version: str, euro_version: str) -> str:
    payload = {
        "picks": sorted((pk.player_id, pk.multiplier) for pk in squad.picks),
        "captain": next((pk.player_id for pk in squad.picks if pk.is_captain), None),
        "bank": round(squad.bank, 1),
        "ep_config_version": ep_cfg_version,
        "gameweek_anchor": gameweek_anchor,
        "curated_notes_version": notes_version,
        "euro_weeks_version": euro_version,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def get_or_generate(state: BriefState, deps: Deps, *, run_fn=run_brief,
                    before_generate=None):
    """Return (BriefState, cache_hit: bool).

    On a fatal squad-resolution error we still run the graph so the error
    responder produces a user-facing message — errors are not cached.
    `run_fn` is injectable so tests can spy on how many times the graph ran.
    `before_generate` (Layer 5) is a no-arg callback invoked ONLY on a genuine
    cache miss, right before the graph runs — never on cache hits or the error
    path. It may raise to abort generation (e.g. the per-IP rate limiter).
    """
    squad, err = resolve_squad(state, deps)
    if err is not None or squad is None:
        return run_fn(state, deps), False

    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        ep_cfg = ep_config_version(load_ep_map(session, anchor)) if anchor else "unknown"
        input_hash = compute_input_hash(
            squad, state.request.gameweek_anchor, ep_cfg,
            curated_notes_version(session), euro_weeks_version(session))

        cached = session.scalars(
            select(Brief).where(Brief.input_hash == input_hash)
        ).first()
        if cached is not None:
            log.info("brief cache HIT hash=%s", input_hash[:12])
            state.brief_md = cached.brief_md
            return state, True

    if before_generate is not None:
        before_generate()
    final = run_fn(state, deps)
    if final.error is None and final.brief_md:
        _persist(deps, state, final, input_hash)
    return final, False


def _persist(deps: Deps, state: BriefState, final: BriefState, input_hash: str) -> None:
    req = state.request
    with deps.session_factory() as session:
        with session.begin():
            session.add(Brief(
                fpl_team_id=req.fpl_team_id,
                draft_id=req.draft_id,
                gameweek=req.gameweek_anchor,
                brief_md=final.brief_md,
                input_hash=input_hash,
                model=STUB_MODEL,
                tokens_in=0, tokens_out=0,
            ))
    log.info("brief persisted hash=%s", input_hash[:12])
