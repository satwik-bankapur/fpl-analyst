"""Reasoning inspector for /admin/inspect — READ-ONLY diagnostics, zero LLM spend.

Three things the serving path deliberately throws away, reassembled for one operator:

1. **The trace.** Runs the graph with `RequestCtx.trace` set and returns every
   node's working. See `graph/trace.py`.
2. **The LLM payload.** The EXACT text `writer/serializer.py` would hand the model,
   with the token count and a costed estimate. It never calls the model — nothing in
   this module imports `writer.llm`, and the only writer import is the serializer.
3. **Free vs paid.** The same brief through `gate_sections` twice, once with the
   paywall forced on. That is the real allow-list, not a mock, so this page is also
   a standing check that the Stage B flip gates the right sections.

## Why this runs the graph instead of reading the cache

`graph.cache.get_or_generate` persists a `briefs` row on a miss and returns only
`brief_md` on a hit — no trace either way. So the inspector calls `run_brief`
directly: nothing is written, nothing is cached, and the run is deterministic, so
the brief it traces is the brief the user gets. The `cache` block reports whether
the persisted row still agrees, and which writer version wrote it — see
`_cache_state` for why the version is what makes a mismatch readable.

## Stage A and the team path

Season briefs need live picks, and Stage A ships no FPL client (`get_deps` passes
`client=None`), so a team_id CANNOT be re-traced. Rather than fake it, the team
path serves the persisted brief for the free/paid comparison and says plainly that
trace and payload are unavailable, with the reason.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from fpl.models import Brief, DraftSquad, EpScore, Player
from graph.data import load_ep_map, load_players, load_teams, resolve_ep_anchor
from graph.deps import Deps
from graph.graph import run_brief
from graph.nodes.writer import STUB_MODEL
from graph.sections import PAID_SECTIONS, gate_sections, split_sections
from graph.state import BriefState, RequestCtx
from writer.config import MODEL_PRICING, OUTPUT_MAX_TOKENS, PAYLOAD_MAX_TOKENS, model_name
from writer.serializer import serialize_payload

from .service import iso_utc

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

_NO_LIVE_CLIENT = (
    "Stage A runs the draft path only — no FPL client is configured, so a season "
    "squad cannot be re-resolved and the pipeline cannot be re-run for it. The "
    "persisted brief is shown; trace and payload need a live squad."
)


# --- brief picker ----------------------------------------------------------

def recent_briefs(deps: Deps, n: int = 25) -> list[dict]:
    """Newest briefs, for one-click inspection. The generic preview row is skipped.

    A generic preview (`fpl_team_id` and `draft_id` both NULL — see the relaxed
    CHECK in migration b2c3d4e5f6a7) has no squad behind it, so there is nothing to
    trace. Listing it would offer the operator a button that cannot work.
    """
    with deps.session_factory() as session:
        rows = session.scalars(
            select(Brief).order_by(Brief.created_at.desc(), Brief.id.desc()).limit(n)
        ).all()
        return [
            {
                "brief_id": b.id,
                "draft_id": str(b.draft_id) if b.draft_id else None,
                "fpl_team_id": b.fpl_team_id,
                "gameweek": b.gameweek,
                "model": b.model,
                "created_at": iso_utc(b.created_at),
                "input_hash": b.input_hash[:12],
            }
            for b in rows
            if b.draft_id is not None or b.fpl_team_id is not None
        ]


# --- the components blob ---------------------------------------------------

def player_components(deps: Deps, player_id: int) -> dict | None:
    """One player's full `ep_scores.components` blob, verbatim, plus who they are.

    Verbatim on purpose: this blob IS the engine's reasoning (Layer 2 §3), and a
    prettified subset would quietly decide for the operator which parts matter.
    """
    with deps.session_factory() as session:
        player = session.get(Player, player_id)
        if player is None:
            return None
        teams = load_teams(session)
        anchor = resolve_ep_anchor(session)
        row = (
            session.get(EpScore, (player_id, anchor)) if anchor is not None else None
        )
    return {
        "player_id": player_id,
        "web_name": player.web_name,
        "full_name": player.full_name,
        "team_short": teams.get(player.team_id, {}).get("short_name", "?"),
        "position": _POS.get(player.position, "?"),
        "price": float(player.price),
        "status": player.status,
        "news": player.news or "",
        "chance_of_playing": player.chance_of_playing,
        "ownership_pct": float(player.ownership_pct or 0),
        "gameweek": anchor,
        "ep": float(row.ep) if row else None,
        "ep_next4": float(row.ep_next4) if row else None,
        "components": (row.components if row else None),
    }


# --- the inspection bundle -------------------------------------------------

def inspect_draft(deps: Deps, draft_id: uuid.UUID) -> dict | None:
    """Full bundle for a draft squad. None if the draft row does not exist."""
    with deps.session_factory() as session:
        if session.get(DraftSquad, draft_id) is None:
            return None

    state = BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=draft_id, gameweek_anchor=0,
        requested_at=datetime.now(timezone.utc), trace=True))
    # Direct graph call: no cache read, no `briefs` row written. See module docstring.
    final = run_brief(state, deps)

    if final.error is not None:
        return {
            "target": {"kind": "draft", "draft_id": str(draft_id), "gameweek": 0},
            "error": {"code": final.error.code, "message": final.error.message},
            "available": {"trace": False, "payload": False,
                          "reason": f"The pipeline failed: {final.error.message}"},
            "trace": [st.model_dump() for st in final.trace],
            "warnings": list(final.warnings),
        }

    return {
        "target": {"kind": "draft", "draft_id": str(draft_id), "gameweek": 0},
        "error": None,
        "available": {"trace": True, "payload": True, "reason": None},
        "trace": [st.model_dump() for st in final.trace],
        "warnings": list(dict.fromkeys(final.warnings)),
        "brief_md": final.brief_md,
        "squad": _squad_table(deps, final),
        "cache": _cache_state(deps, draft_id, final.brief_md),
        **_tiers(final.brief_md),
        "payload": _payload(deps, final),
    }


def inspect_team(deps: Deps, team_id: int, gameweek: int) -> dict | None:
    """Free/paid comparison for a persisted season brief. None if there isn't one."""
    with deps.session_factory() as session:
        row = session.scalars(
            select(Brief).where(Brief.fpl_team_id == team_id, Brief.gameweek == gameweek)
        ).first()
        if row is None:
            return None
    return {
        "target": {"kind": "team", "team_id": team_id, "gameweek": gameweek},
        "error": None,
        "available": {"trace": False, "payload": False, "reason": _NO_LIVE_CLIENT},
        "trace": [],
        "warnings": [],
        "brief_md": row.brief_md,
        "squad": [],
        "cache": None,
        **_tiers(row.brief_md),
        "payload": None,
    }


# --- pieces ----------------------------------------------------------------

def _tiers(brief_md: str) -> dict:
    """The same brief gated twice — the real allow-list, run for both tiers.

    `gate_sections(paywall=True)` is exactly what Stage B will call. Rendering both
    outputs side by side means the paywall's behaviour is inspectable BEFORE the
    flip, rather than discovered by a customer afterwards.
    """
    sections = split_sections(brief_md)
    free_sections, locked = gate_sections(sections, paywall=True)
    paid_sections, paid_locked = gate_sections(sections, paywall=False)
    return {
        "free": {"sections": free_sections, "locked_sections": locked},
        "paid": {"sections": paid_sections, "locked_sections": paid_locked},
        # What a subscriber gets that a free reader does not. Derived from the two
        # gated outputs, never from the PAID_SECTIONS constant — a key the writer
        # never produced must not be advertised as part of the paid product.
        "paid_delta": [k for k in paid_sections if k not in free_sections],
        "paid_vocabulary": list(PAID_SECTIONS),
    }


def _squad_table(deps: Deps, final: BriefState) -> list[dict]:
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        players = load_players(session)
        teams = load_teams(session)
        ep_map = load_ep_map(session, anchor) if anchor is not None else {}

    out = []
    for pk in sorted(final.squad.picks, key=lambda p: p.slot):
        pl = players.get(pk.player_id, {})
        ep = ep_map.get(pk.player_id, {})
        out.append({
            "player_id": pk.player_id,
            "name": pl.get("web_name", f"#{pk.player_id}"),
            "club": teams.get(pl.get("team_id"), {}).get("short_name", "?"),
            "position": _POS.get(pl.get("position"), "?"),
            "price": pl.get("price", 0.0),
            "slot": pk.slot,
            "role": "XI" if pk.slot <= 11 else "BENCH",
            "is_captain": pk.is_captain,
            "ep": round(ep.get("ep", 0.0), 2),
            "ep_next4": round(ep.get("ep_next4", 0.0), 2),
            "flags": list((ep.get("components") or {}).get("flags", [])),
        })
    return out


def _cache_state(deps: Deps, draft_id: uuid.UUID, brief_md: str) -> dict:
    """Does the newest persisted brief still equal what the code produces now?

    `matches=False` alone is ambiguous, so the writer version that produced the row
    is reported next to it. Two very different situations:

    - `model` != the current `STUB_MODEL`: the row predates a writer bump. It is
      ORPHANED, not stale-and-serving — `graph.cache` mixes STUB_MODEL into
      input_hash, so a request now misses and regenerates. Expected; ignore.
    - `model` == the current `STUB_MODEL`: the same writer version produced
      different prose. That is a real problem — either the template changed without
      a bump, or the underlying data moved under a fixed hash.

    Newest, not first: a draft accumulates one row per distinct input_hash, so a
    bump leaves the superseded row behind. Reading the oldest reported a mismatch
    on every draft generated before the last bump.
    """
    with deps.session_factory() as session:
        row = session.scalars(
            select(Brief).where(Brief.draft_id == draft_id)
            .order_by(Brief.created_at.desc(), Brief.id.desc())
        ).first()
    if row is None:
        return {"matches": None, "model": None, "created_at": None,
                "current_writer": STUB_MODEL, "superseded": False}
    return {
        "matches": row.brief_md == brief_md,
        "model": row.model,
        "created_at": iso_utc(row.created_at),
        "current_writer": STUB_MODEL,
        "superseded": row.model != STUB_MODEL,
    }


def _payload(deps: Deps, final: BriefState) -> dict:
    """The serialized Layer 4 payload, costed. NO LLM CALL — see module docstring."""
    result = serialize_payload(final, deps)
    model = model_name()
    in_rate, out_rate = MODEL_PRICING.get(model, (0.0, 0.0))
    # Output is priced at the generation CAP, not a guess: the honest number for
    # "what could one call cost" is the ceiling, and OUTPUT_MAX_TOKENS is it.
    input_cost = result.token_estimate / 1_000_000 * in_rate
    output_cost = OUTPUT_MAX_TOKENS / 1_000_000 * out_rate
    return {
        "text": result.text,
        "token_estimate": result.token_estimate,
        "max_input_tokens": PAYLOAD_MAX_TOKENS,
        "over_budget": result.token_estimate > PAYLOAD_MAX_TOKENS,
        "dropped": result.dropped,
        "blocks": result.blocks,
        "model": model,
        "priced": model in MODEL_PRICING,
        "rate_per_mtok": {"input": in_rate, "output": out_rate},
        "output_max_tokens": OUTPUT_MAX_TOKENS,
        "cost_usd": {
            "input": round(input_cost, 6),
            "output_at_cap": round(output_cost, 6),
            "total_at_cap": round(input_cost + output_cost, 6),
        },
    }
