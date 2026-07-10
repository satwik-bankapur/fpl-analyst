"""MarketScout (§2.2) — scan the global ep_scores table for actionable moves.

Hard filters applied everywhere: exclude unavailable status (i/s/u/n); enforce the
3-per-club rule against the current squad (minus the outgoing player, for
replacements); exclude the squad's own players from replacement + template lists.
"""
from __future__ import annotations

from ..config import (
    DIFFERENTIAL_MAX_OWNERSHIP, DIFFERENTIAL_TOP_N, MAX_PER_CLUB,
    REPLACEMENT_TOP_N, SALE_PRICE_EDGE, TEMPLATE_MIN_OWNERSHIP, TEMPLATE_TOP_N,
)
from ..data import load_ep_map, load_players, resolve_ep_anchor
from ..deps import Deps
from ..state import BriefState, Candidate, MarketScan, Replacement, ReplacementSet

_REPLACEABLE = {"unavailable", "injury_doubt", "fixture_cliff", "cold_form"}
_OK_STATUS = {"a", "d"}  # exclude i/s/u/n everywhere


def market_scout(state: BriefState, deps: Deps) -> dict:
    squad, diag = state.squad, state.diagnosis
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        ep_map = load_ep_map(session, anchor)
        players = load_players(session)

    squad_ids = {pk.player_id for pk in squad.picks}
    club_count: dict[int, int] = {}
    for pk in squad.picks:
        tid = players.get(pk.player_id, {}).get("team_id")
        club_count[tid] = club_count.get(tid, 0) + 1

    def ep4(pid):
        return ep_map.get(pid, {}).get("ep_next4", 0.0)

    def key_flags(pid):
        return list((ep_map.get(pid, {}).get("components", {}) or {}).get("flags", []))[:5]

    def club_ok(pid, counts):
        if pid in squad_ids:                      # already counted; not an addition
            return True
        return counts.get(players[pid]["team_id"], 0) < MAX_PER_CLUB

    universe = [pid for pid in ep_map
                if pid in players and players[pid]["status"] in _OK_STATUS]

    # --- replacements per diagnosed problem --------------------------------
    replacements: list[ReplacementSet] = []
    warnings: list[str] = []
    for prob in diag.problems:
        if prob.type not in _REPLACEABLE or prob.player_id is None:
            continue
        out = players.get(prob.player_id)
        if out is None:
            continue
        out_ep4 = ep4(prob.player_id)
        budget = out["price"] + squad.bank
        counts = dict(club_count)
        counts[out["team_id"]] = counts.get(out["team_id"], 0) - 1  # free the outgoing slot

        cands = [
            pid for pid in universe
            if pid not in squad_ids
            and players[pid]["position"] == out["position"]
            and players[pid]["price"] <= budget + 1e-9
            and club_ok(pid, counts)
        ]
        cands.sort(key=ep4, reverse=True)

        cand_models = []
        for pid in cands[:REPLACEMENT_TOP_N]:
            pl = players[pid]
            if budget - pl["price"] <= SALE_PRICE_EDGE:
                warnings.append("sale_price_approximated")
            cand_models.append(Replacement(
                player_id=pid, ep_next4=round(ep4(pid), 2),
                ep_next4_delta=round(ep4(pid) - out_ep4, 2),
                price_delta=round(pl["price"] - out["price"], 1),
                ownership_pct=pl["ownership_pct"], key_flags=key_flags(pid)))
        replacements.append(ReplacementSet(
            problem_player_id=prob.player_id, problem_type=prob.type, candidates=cand_models))

    # --- differentials: top-N global ep_next4, ownership < 10% --------------
    diff_pool = [pid for pid in universe
                 if players[pid]["ownership_pct"] < DIFFERENTIAL_MAX_OWNERSHIP
                 and club_ok(pid, club_count)]
    diff_pool.sort(key=ep4, reverse=True)
    differentials = [
        Candidate(player_id=pid, ep_next4=round(ep4(pid), 2),
                  ownership_pct=players[pid]["ownership_pct"], key_flags=key_flags(pid))
        for pid in diff_pool[:DIFFERENTIAL_TOP_N]
    ]

    # --- template_missing: top-N by ownership > 30%, not in squad -----------
    tmpl_pool = [pid for pid in universe
                 if pid not in squad_ids
                 and players[pid]["ownership_pct"] > TEMPLATE_MIN_OWNERSHIP
                 and club_ok(pid, club_count)]
    tmpl_pool.sort(key=lambda pid: players[pid]["ownership_pct"], reverse=True)
    template_missing = [
        Candidate(player_id=pid, ep_next4=round(ep4(pid), 2),
                  ownership_pct=players[pid]["ownership_pct"], key_flags=key_flags(pid))
        for pid in tmpl_pool[:TEMPLATE_TOP_N]
    ]

    market = MarketScan(replacements=replacements, differentials=differentials,
                        template_missing=template_missing)
    # de-dupe the sale-price warning (emit at most once)
    out_warnings = ["sale_price_approximated"] if "sale_price_approximated" in warnings else []
    return {"market": market, "warnings": out_warnings}
