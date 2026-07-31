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
from ..data import load_ep_map, load_players, load_teams, resolve_ep_anchor
from ..deps import Deps
from ..state import BriefState, Candidate, MarketScan, Replacement, ReplacementSet
from ..trace import REJECTION_EXAMPLES, TraceStage

_REPLACEABLE = {"unavailable", "injury_doubt", "fixture_cliff", "cold_form"}
_OK_STATUS = {"a", "d"}  # exclude i/s/u/n everywhere


def _reject_reason(pid, out, budget, counts, players, squad_ids) -> str | None:
    """Why `pid` cannot replace `out`, or None if it can. THE filter — one copy.

    Both the live shortlist and the trace call this, so a rejection the inspector
    reports is by construction the rejection that actually happened. Duplicating
    these predicates at the trace site is how a diagnostic starts lying.

    Order is by decreasing fundamentality so the reported reason is the most
    basic thing wrong, not the last check to fail.
    """
    pl = players[pid]
    if pid in squad_ids:
        return "already_in_squad"
    if pl["position"] != out["position"]:
        return "wrong_position"
    if pl["status"] not in _OK_STATUS:
        return "unavailable_status"
    if pl["price"] > budget + 1e-9:
        return "over_budget"
    if counts.get(pl["team_id"], 0) >= MAX_PER_CLUB:
        return "club_cap"
    return None


def market_scout(state: BriefState, deps: Deps) -> dict:
    squad, diag = state.squad, state.diagnosis
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        ep_map = load_ep_map(session, anchor)
        players = load_players(session)
        teams = load_teams(session) if state.request.trace else {}

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

    # --- replacements per diagnosed problem --------------------------------
    # `scored` is every ep-scored player; `universe` is that set minus the
    # unavailable, in the SAME iteration order as before trace mode existed — the
    # shortlist a reader gets must not depend on whether an operator is watching.
    # The trace needs the wider `scored` set so an unavailable player can be
    # reported as rejected-for-that-reason rather than silently absent.
    scored = [pid for pid in ep_map if pid in players]
    universe = [pid for pid in scored if players[pid]["status"] in _OK_STATUS]

    replacements: list[ReplacementSet] = []
    warnings: list[str] = []
    trace_problems: list[dict] = []
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

        cands = [pid for pid in universe
                 if _reject_reason(pid, out, budget, counts, players, squad_ids) is None]
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

        if state.request.trace:
            trace_problems.append(_trace_problem(
                prob, out, out_ep4, budget, counts, cands, cand_models,
                scored, players, teams, ep_map, squad_ids, ep4))

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
    out = {"market": market, "warnings": out_warnings}
    if state.request.trace:
        out["trace"] = [_trace(trace_problems, market, players, teams, len(scored),
                               len(universe))]
    return out


# --- trace (diagnostic only; see graph/trace.py) ---------------------------
#
# The whole point of tracing this node is the players that DID NOT make it. The
# live path keeps only survivors, so "why isn't X on the shortlist?" was
# unanswerable without re-running the filter by hand. Every rejection below comes
# from `_reject_reason` — the same call the shortlist itself is built from.

def _trace_problem(prob, out, out_ep4, budget, counts, cands, cand_models,
                   scored, players, teams, ep_map, squad_ids, ep4) -> dict:
    def name(pid):
        return players.get(pid, {}).get("web_name", f"#{pid}")

    def club(pid):
        return teams.get(players.get(pid, {}).get("team_id"), {}).get("short_name", "?")

    def card(pid):
        return {"player_id": pid, "name": name(pid), "club": club(pid),
                "price": players.get(pid, {}).get("price", 0.0),
                "ep_next4": round(ep4(pid), 2),
                "ep_next4_delta": round(ep4(pid) - out_ep4, 2)}

    shortlisted = {c.player_id for c in cand_models}
    buckets: dict[str, list[int]] = {}
    for pid in scored:
        reason = _reject_reason(pid, out, budget, counts, players, squad_ids)
        if reason is None:
            if pid in shortlisted:
                continue
            # Survived every hard filter. Two distinct ways to still not appear:
            # it projects no better than the player leaving, or it simply ranked
            # below the shortlist cut. The first is the interesting one.
            reason = ("insufficient_ep_delta" if ep4(pid) <= out_ep4 else "below_top_n")
        buckets.setdefault(reason, []).append(pid)

    rejected = []
    for reason, pids in buckets.items():
        pids.sort(key=ep4, reverse=True)
        rejected.append({
            "reason": reason,
            "count": len(pids),
            "examples": [card(pid) for pid in pids[:REJECTION_EXAMPLES]],
        })
    rejected.sort(key=lambda r: -r["count"])

    return {
        "problem_player_id": prob.player_id,
        "problem_player": name(prob.player_id),
        "problem_type": prob.type,
        "severity": prob.severity,
        "out_ep_next4": round(out_ep4, 2),
        "budget": round(budget, 1),
        "position": out["position"],
        "considered": len(scored),
        "passed_filters": len(cands),
        "shortlisted": [
            {"player_id": c.player_id, "name": name(c.player_id), "club": club(c.player_id),
             "ep_next4": c.ep_next4, "ep_next4_delta": c.ep_next4_delta,
             "price_delta": c.price_delta, "ownership_pct": c.ownership_pct,
             "key_flags": c.key_flags}
            for c in cand_models
        ],
        "rejected": rejected,
    }


def _trace(trace_problems, market, players, teams, n_scored, n_available) -> TraceStage:
    def card(c):
        pl = players.get(c.player_id, {})
        return {"player_id": c.player_id,
                "name": pl.get("web_name", f"#{c.player_id}"),
                "club": teams.get(pl.get("team_id"), {}).get("short_name", "?"),
                "ep_next4": c.ep_next4, "ownership_pct": c.ownership_pct,
                "key_flags": c.key_flags}

    summary = [f"{n_scored} scored players considered; {n_available} available after the "
               f"FPL status filter."]
    for p in trace_problems:
        summary.append(
            f"Replacing {p['problem_player']} ({p['problem_type']}): "
            f"{p['passed_filters']} of {p['considered']} passed every filter, "
            f"{len(p['shortlisted'])} shortlisted.")
        summary += [f"  rejected {r['count']} — {r['reason'].replace('_', ' ')}"
                    for r in p["rejected"]]
    if not trace_problems:
        summary.append("No replaceable problem was raised, so no market scan ran.")

    return TraceStage(node="market_scout", summary=summary, data={
        "problems": trace_problems,
        "differentials": [card(c) for c in market.differentials],
        "template_missing": [card(c) for c in market.template_missing],
    })
