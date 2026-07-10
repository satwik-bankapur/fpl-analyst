"""Entry point + cache integration (§4).

input_hash = hash(sorted picks + captain + bank + ep config_version +
gameweek_anchor + curated_notes_version + euro_weeks_version). The curated-notes
and euro-weeks tables don't exist yet — their versions are placeholder constants
(graph/config.py), already mixed into the hash so the key shape is final.

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

from .config import CURATED_NOTES_VERSION, EURO_WEEKS_VERSION
from .data import ep_config_version, load_ep_map, resolve_ep_anchor
from .deps import Deps
from .nodes.squad_analyst import resolve_squad
from .nodes.writer import STUB_MODEL
from .state import BriefState
from .graph import run_brief

log = logging.getLogger("graph.cache")


def compute_input_hash(squad, gameweek_anchor: int, ep_cfg_version: str) -> str:
    payload = {
        "picks": sorted((pk.player_id, pk.multiplier) for pk in squad.picks),
        "captain": next((pk.player_id for pk in squad.picks if pk.is_captain), None),
        "bank": round(squad.bank, 1),
        "ep_config_version": ep_cfg_version,
        "gameweek_anchor": gameweek_anchor,
        "curated_notes_version": CURATED_NOTES_VERSION,
        "euro_weeks_version": EURO_WEEKS_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def get_or_generate(state: BriefState, deps: Deps, *, run_fn=run_brief):
    """Return (BriefState, cache_hit: bool).

    On a fatal squad-resolution error we still run the graph so the error
    responder produces a user-facing message — errors are not cached.
    `run_fn` is injectable so tests can spy on how many times the graph ran.
    """
    squad, err = resolve_squad(state, deps)
    if err is not None or squad is None:
        return run_fn(state, deps), False

    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        ep_cfg = ep_config_version(load_ep_map(session, anchor)) if anchor else "unknown"
        input_hash = compute_input_hash(squad, state.request.gameweek_anchor, ep_cfg)

        cached = session.scalars(
            select(Brief).where(Brief.input_hash == input_hash)
        ).first()
        if cached is not None:
            log.info("brief cache HIT hash=%s", input_hash[:12])
            state.brief_md = cached.brief_md
            return state, True

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
