"""Shared fixture-run maths (§2.3).

SquadAnalyst needs a team's `swing` to detect `fixture_cliff` problems, and
FixturePlanner needs the full run + swing for its outlook. Same computation, one
home — so the swing that raises a problem and the swing the Writer quotes can
never disagree.
"""
from __future__ import annotations

from statistics import mean

from .config import FIXTURE_HORIZON


def team_next_fixtures(fixtures: list[dict], team_id: int, anchor: int, horizon: int):
    """Ordered [(gw, opponent_id, is_home, fdr)] for a team, GW>=anchor, next `horizon` GWs.

    A blank GW simply has no entry; a double GW contributes two entries.
    """
    out = []
    for gw in range(anchor, anchor + horizon):
        for fx in fixtures:
            if fx["gameweek"] != gw:
                continue
            if fx["home_team"] == team_id:
                out.append((gw, fx["away_team"], True, fx["home_difficulty"]))
            elif fx["away_team"] == team_id:
                out.append((gw, fx["home_team"], False, fx["away_difficulty"]))
    return out


def swing_score(runs: list[tuple]) -> float:
    """avg_fdr(first 3) − avg_fdr(next 3), sign-normalized so +ve = improving run (§2.3).

    Lower FDR is easier, so an improving run has lower *later* FDR: we return
    (early_avg − late_avg) so that easier-ahead => positive.
    """
    fdrs = [r[3] for r in runs]
    first, second = fdrs[:3], fdrs[3:6]
    if not first or not second:
        return 0.0
    return round(mean(first) - mean(second), 3)


def blanks_and_doubles(fixtures: list[dict], team_ids: list[int], anchor: int, horizon: int):
    """([{gw, team_ids}] blanks, [...] doubles) within the horizon for the given teams."""
    blanks, doubles = [], []
    for gw in range(anchor, anchor + horizon):
        counts = {tid: 0 for tid in team_ids}
        for fx in fixtures:
            if fx["gameweek"] != gw:
                continue
            for side in (fx["home_team"], fx["away_team"]):
                if side in counts:
                    counts[side] += 1
        blank = [tid for tid, c in counts.items() if c == 0]
        double = [tid for tid, c in counts.items() if c >= 2]
        if blank:
            blanks.append({"gw": gw, "team_ids": sorted(blank)})
        if double:
            doubles.append({"gw": gw, "team_ids": sorted(double)})
    return blanks, doubles


DEFAULT_HORIZON = FIXTURE_HORIZON
