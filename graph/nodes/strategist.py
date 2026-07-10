"""Strategist (§2.4) — RULES implementation (the designated LLM insertion point).

Pure function of the three upstream contracts (diagnosis, market, fixture_outlook)
plus EP lookups. A future LLM Strategist consumes the same serialized inputs and
must return a valid `Decisions` — enforced by a shared contract test against both
implementations. v1 never recommends a points hit (single assumed free transfer).
"""
from __future__ import annotations

from ..config import (
    CAPTAIN_DIFF_EP_GAP, CAPTAIN_DIFF_MAX_OWNERSHIP, DOUBLE_GW_CHIP_STARTERS,
    TRANSFER_MIN_DELTA, WATCHLIST_N,
)
from ..data import load_ep_map, load_players, resolve_ep_anchor
from ..deps import Deps
from ..state import BriefState, CaptainDecision, Decisions, TransferDecision


def strategist(state: BriefState, deps: Deps) -> dict:
    diag, market, outlook, squad = (
        state.diagnosis, state.market, state.fixture_outlook, state.squad,
    )
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        ep_map = load_ep_map(session, anchor)
        players = load_players(session)

    def ep(pid):  return ep_map.get(pid, {}).get("ep", 0.0)
    def ep4(pid): return ep_map.get(pid, {}).get("ep_next4", 0.0)
    def flags(pid): return (ep_map.get(pid, {}).get("components", {}) or {}).get("flags", [])
    def own(pid): return players.get(pid, {}).get("ownership_pct", 100.0)

    squad_ids = {pk.player_id for pk in squad.picks}
    reasoning: list[str] = []

    # --- transfer: highest-severity problem with a >=+2.0 ep_next4 replacement
    rs_by_prob = {rs.problem_player_id: rs for rs in market.replacements}
    transfer = TransferDecision(action="roll", reasoning_codes=["no_qualifying_delta"])
    for prob in sorted(diag.problems, key=lambda p: p.severity, reverse=True):
        rs = rs_by_prob.get(prob.player_id)
        if not rs or not rs.candidates:
            continue
        best = rs.candidates[0]                     # candidates are ep_next4-desc
        if best.ep_next4_delta >= TRANSFER_MIN_DELTA:
            codes = [f"delta:+{best.ep_next4_delta}", f"problem:{prob.type}",
                     f"severity:{prob.severity}"] + [f"flag:{f}" for f in best.key_flags]
            transfer = TransferDecision(action="make", out_id=prob.player_id,
                                        in_id=best.player_id, reasoning_codes=codes)
            reasoning.extend(codes)
            break
    if transfer.action == "roll":
        reasoning.append("transfer:roll")

    # --- captain: max anchor-GW ep among the XI; vice = second -------------
    starters = [pk.player_id for pk in squad.picks if pk.slot <= 11]
    ranked = sorted(starters, key=lambda pid: (-ep(pid), pid))
    pick_id = ranked[0]
    vice_id = ranked[1] if len(ranked) > 1 else pick_id
    diff_note = None
    gap = round(ep(pick_id) - ep(vice_id), 2)
    if len(ranked) > 1 and gap <= CAPTAIN_DIFF_EP_GAP and own(vice_id) < CAPTAIN_DIFF_MAX_OWNERSHIP:
        diff_note = (f"vice ({vice_id}) is within {gap} ep of the captain and only "
                     f"{own(vice_id)}% owned — a differential captaincy option")
    captain = CaptainDecision(pick_id=pick_id, vice_id=vice_id, differential_note=diff_note)
    reasoning.append(f"captain_ep:{round(ep(pick_id), 2)}")

    # --- bench order: outfield bench by ep desc; bench GK pinned first ------
    bench = [pk for pk in squad.picks if pk.slot >= 12]
    bench_gk = [pk.player_id for pk in bench if players.get(pk.player_id, {}).get("position") == 1]
    bench_out = sorted(
        [pk.player_id for pk in bench if players.get(pk.player_id, {}).get("position") != 1],
        key=ep, reverse=True,
    )
    bench_order = bench_gk + bench_out

    # --- chip stance: double_gw for >=2 squad players -> consider ----------
    dbl = sum(1 for pk in squad.picks
              if any(f.startswith("double_gw") for f in flags(pk.player_id)))
    chip_stance = "consider_bench_boost_or_tc" if dbl >= DOUBLE_GW_CHIP_STARTERS else "hold"
    if chip_stance != "hold":
        reasoning.append(f"double_gw_players:{dbl}")

    # --- watchlist: differentials ∪ entering-good-run teams' top EP players -
    pool = [c.player_id for c in market.differentials]
    for tid in outlook.entering_good_run:
        team_players = [pid for pid in ep_map if players.get(pid, {}).get("team_id") == tid]
        if team_players:
            pool.append(max(team_players, key=ep4))
            reasoning.append(f"swing_team:{tid}")
    watch, seen = [], set()
    for pid in sorted(pool, key=ep4, reverse=True):
        if pid in squad_ids or pid in seen:
            continue
        seen.add(pid)
        watch.append(pid)
        if len(watch) >= WATCHLIST_N:
            break

    decisions = Decisions(
        transfer=transfer, captain=captain, bench_order=bench_order,
        chip_stance=chip_stance, watchlist=watch, reasoning_codes=reasoning)
    return {"decisions": decisions}
