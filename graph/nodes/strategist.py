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
from ..data import load_ep_map, load_players, load_teams, resolve_ep_anchor
from ..deps import Deps
from ..state import BriefState, CaptainDecision, Decisions, TransferDecision
from ..trace import TraceStage, explain_codes


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
    # Why each problem was NOT the one transferred. Built as the loop runs rather
    # than reconstructed afterwards, so it records the real control flow —
    # including the problems never reached because an earlier one won.
    considered: list[dict] = []
    ranked_problems = sorted(diag.problems, key=lambda p: p.severity, reverse=True)
    decided = False
    for prob in ranked_problems:
        rs = rs_by_prob.get(prob.player_id)
        if decided:
            considered.append({"player_id": prob.player_id, "type": prob.type,
                               "severity": prob.severity, "outcome": "not_reached",
                               "best_delta": None})
            continue
        if not rs or not rs.candidates:
            considered.append({"player_id": prob.player_id, "type": prob.type,
                               "severity": prob.severity, "outcome": "no_candidates",
                               "best_delta": None})
            continue
        best = rs.candidates[0]                     # candidates are ep_next4-desc
        if best.ep_next4_delta >= TRANSFER_MIN_DELTA:
            codes = [f"delta:+{best.ep_next4_delta}", f"problem:{prob.type}",
                     f"severity:{prob.severity}"] + [f"flag:{f}" for f in best.key_flags]
            transfer = TransferDecision(action="make", out_id=prob.player_id,
                                        in_id=best.player_id, reasoning_codes=codes)
            reasoning.extend(codes)
            considered.append({"player_id": prob.player_id, "type": prob.type,
                               "severity": prob.severity, "outcome": "chosen",
                               "best_delta": best.ep_next4_delta,
                               "in_id": best.player_id})
            decided = True
            continue
        considered.append({"player_id": prob.player_id, "type": prob.type,
                           "severity": prob.severity, "outcome": "below_threshold",
                           "best_delta": best.ep_next4_delta,
                           "in_id": best.player_id})
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
        # A PREDICATE about the vice, not a whole sentence, and deliberately
        # id-free: the layer3 §2.4 spec said "with both ids", but pick_id and
        # vice_id are already fields on this object, so repeating the id here only
        # ever leaked "vice (413)" into reader-facing copy via the stub writer.
        # Whoever renders it owns the name. See the 2026-07-30 drift note.
        diff_note = (f"is within {gap} projected points of the captain and only "
                     f"{own(vice_id)}% owned — a real differential")
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
    out = {"decisions": decisions}
    if state.request.trace:
        with deps.session_factory() as session:
            teams = load_teams(session)
        out["trace"] = [_trace(decisions, considered, ranked, gap, dbl,
                               players, teams, ep, ep4, own)]
    return out


# --- trace (diagnostic only; see graph/trace.py) ---------------------------

def _trace(dec, considered, ranked_starters, captain_gap, dbl_count,
           players, teams, ep, ep4, own) -> TraceStage:
    """The decision plus the alternatives that lost, with the margins they lost by."""
    def name(pid):
        return players.get(pid, {}).get("web_name", f"#{pid}")

    def club(pid):
        return teams.get(players.get(pid, {}).get("team_id"), {}).get("short_name", "?")

    def who(pid):
        return {"player_id": pid, "name": name(pid), "club": club(pid)}

    t = dec.transfer
    alternatives = [
        {**a, "name": name(a["player_id"]),
         "in_name": name(a["in_id"]) if a.get("in_id") else None,
         "threshold": TRANSFER_MIN_DELTA,
         "why": _WHY_LOST[a["outcome"]](a, TRANSFER_MIN_DELTA)}
        for a in considered
    ]

    starters = [{**who(pid), "ep": round(ep(pid), 2), "ep_next4": round(ep4(pid), 2),
                 "ownership_pct": own(pid)} for pid in ranked_starters]

    summary = []
    if t.action == "make":
        summary.append(
            f"Transfer: {name(t.out_id)} out, {name(t.in_id)} in — the highest-severity "
            f"problem whose best replacement cleared the +{TRANSFER_MIN_DELTA} "
            f"projected-points threshold.")
    else:
        best = max((a["best_delta"] for a in considered
                    if a["best_delta"] is not None), default=None)
        summary.append(
            f"Transfer: rolled. Best available delta "
            f"{'+' + str(best) if best is not None else 'none'}, "
            f"threshold is {TRANSFER_MIN_DELTA}.")
    summary += [f"  {a['name']}: {a['why']}" for a in alternatives]

    if starters:
        cap, vice = starters[0], (starters[1] if len(starters) > 1 else starters[0])
        summary.append(
            f"Captain: {cap['name']} — highest anchor-gameweek projection among the "
            f"starting XI, {round(cap['ep'] - vice['ep'], 2)} clear of "
            f"{vice['name']} at vice.")
    if dec.captain.differential_note:
        summary.append(f"  Vice flagged as a differential: {dec.captain.differential_note}.")
    summary.append(
        f"Chips: {dec.chip_stance} — {dbl_count} squad player(s) carry a double-gameweek flag.")
    summary.append("Bench order: " + (" -> ".join(name(p) for p in dec.bench_order) or "none"))
    summary.append("Watchlist: " + (", ".join(name(p) for p in dec.watchlist) or "none"))

    return TraceStage(node="strategist", summary=summary, data={
        "transfer": {
            "action": t.action,
            "out": who(t.out_id) if t.out_id else None,
            "in": who(t.in_id) if t.in_id else None,
            "threshold": TRANSFER_MIN_DELTA,
        },
        "transfer_alternatives": alternatives,
        "captain": {
            "pick": who(dec.captain.pick_id),
            "vice": who(dec.captain.vice_id),
            "gap": captain_gap,
            "differential_note": dec.captain.differential_note,
            "differential_gap_threshold": CAPTAIN_DIFF_EP_GAP,
            "differential_max_ownership": CAPTAIN_DIFF_MAX_OWNERSHIP,
        },
        "ranked_starters": starters,
        "bench_order": [who(p) for p in dec.bench_order],
        "chip": {"stance": dec.chip_stance, "double_gw_players": dbl_count,
                 "threshold": DOUBLE_GW_CHIP_STARTERS},
        "watchlist": [{**who(p), "ep_next4": round(ep4(p), 2), "ownership_pct": own(p)}
                      for p in dec.watchlist],
        "codes": explain_codes(dec.reasoning_codes),
    })


_WHY_LOST = {
    "chosen": lambda a, thr: (
        f"chosen — best replacement gains +{a['best_delta']}, clearing the "
        f"+{thr} threshold"),
    "below_threshold": lambda a, thr: (
        f"best replacement gains only +{a['best_delta']}, threshold is +{thr}"),
    "no_candidates": lambda a, thr: (
        "no legal replacement existed at this budget, position and club cap"),
    "not_reached": lambda a, thr: (
        "a higher-severity problem was already solved, and v1 never makes two transfers"),
}
