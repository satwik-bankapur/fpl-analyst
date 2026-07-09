"""STUB EP writer (spec §7.6).

PLACEHOLDER ONLY. Layer 2 (engine/) replaces the formula behind the same
`ep_scores` contract — do not grow this into the real heuristic. Its job is to put
*real rows* in `ep_scores` so downstream layers can be built against them.

Placeholder formula: EP = availability × form × Σ(fixture multipliers in the GW).
It is deliberately trivial EXCEPT for two things that are data-layer correctness,
not heuristic quality, so the stub gets them right (spec §5.1):
  - BLANK  (team has no fixture in a GW) -> that GW contributes 0.
  - DOUBLE (team has two fixtures in a GW) -> sum both.
And it honors `chance_of_playing is None => 100%` (spec §5.2).

The `components` blob here is a stub shape flagged `"stub": true`; the real
labeled-breakdown contract is Layer 2 §3.
"""
from __future__ import annotations

from decimal import Decimal

# Placeholder FDR->multiplier table. Layer 2 owns the real, versioned config (§7);
# this is intentionally crude and lives here only so the stub can produce rows.
_STUB_FDR_MULT = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.85, 5: 0.70}
_HORIZON = 4  # next-4-GW window for ep_next4


def _availability(chance_of_playing: int | None) -> float:
    """chance_of_playing NULL => no flag => 100%. Otherwise chance/100."""
    if chance_of_playing is None:
        return 1.0
    return max(0.0, min(100, chance_of_playing)) / 100.0


def _team_fixtures_in_gw(fixtures: list[dict], team_id: int, gw: int) -> list[dict]:
    """All of a team's fixtures in one GW: 0 = blank, 1 = normal, 2 = double.

    Each entry: {opponent, venue: 'H'|'A', difficulty, multiplier}. Fixtures with a
    NULL gameweek (postponed) are excluded — they belong to no GW's set (§5.1).
    """
    out = []
    for fx in fixtures:
        if fx["gameweek"] != gw:
            continue
        if fx["home_team"] == team_id:
            diff = fx["home_difficulty"]
            out.append({"opponent": fx["away_team"], "venue": "H", "difficulty": diff,
                        "multiplier": _STUB_FDR_MULT.get(diff, 1.0)})
        elif fx["away_team"] == team_id:
            diff = fx["away_difficulty"]
            out.append({"opponent": fx["home_team"], "venue": "A", "difficulty": diff,
                        "multiplier": _STUB_FDR_MULT.get(diff, 1.0)})
    return out


def _gw_points(form: float, avail: float, fixtures_in_gw: list[dict]) -> float:
    # Blank -> empty list -> 0. Double -> two entries summed.
    return form * avail * sum(f["multiplier"] for f in fixtures_in_gw)


def stub_ep_rows(
    players: list[dict],
    fixtures: list[dict],
    next_gw: int,
) -> list[dict]:
    """Pure function: produce one `ep_scores` row dict per player.

    `players` entries need: id, form, chance_of_playing, team_id.
    `fixtures` entries need: gameweek, home_team, away_team, home_difficulty,
    away_difficulty (i.e. parsed fixture dicts).
    """
    horizon_gws = list(range(next_gw, next_gw + _HORIZON))
    rows: list[dict] = []

    for p in players:
        form = float(p.get("form") or 0)
        avail = _availability(p.get("chance_of_playing"))
        team_id = p["team_id"]

        next_fixtures = _team_fixtures_in_gw(fixtures, team_id, next_gw)
        ep = _gw_points(form, avail, next_fixtures)

        per_gw = []
        ep_next4 = 0.0
        for gw in horizon_gws:
            gw_fx = _team_fixtures_in_gw(fixtures, team_id, gw)
            pts = _gw_points(form, avail, gw_fx)
            ep_next4 += pts
            per_gw.append({"gameweek": gw, "fixtures": gw_fx, "points": round(pts, 2),
                           "blank": len(gw_fx) == 0, "double": len(gw_fx) >= 2})

        rows.append({
            "player_id": p["id"],
            "gameweek": next_gw,  # horizon anchor
            "ep": Decimal(f"{ep:.2f}"),
            "ep_next4": Decimal(f"{ep_next4:.2f}"),
            "components": {
                "stub": True,  # <- Layer 2 replaces this with the §3 contract
                "formula": "availability * form * sum(fixture_multiplier)",
                "minutes_prob": round(avail, 3),
                "form": form,
                "next_gw": {
                    "gameweek": next_gw,
                    "fixtures": next_fixtures,
                    "blank": len(next_fixtures) == 0,
                    "double": len(next_fixtures) >= 2,
                },
                "horizon": per_gw,
            },
        })

    return rows
