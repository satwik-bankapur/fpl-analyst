"""FixturePlanner (§2.3) — turn raw fixtures into run/swing intelligence.

Covers squad teams plus every team shortlisted by MarketScout, anchored at the
EP anchor GW (= GW1 for a pre-season brief, the heart of the gameweek-0 report).
"""
from __future__ import annotations

from ..config import SWING_ENTER, SWING_LEAVE
from ..data import load_fixtures, load_players, load_teams, resolve_ep_anchor
from ..deps import Deps
from ..fixtures_util import (
    DEFAULT_HORIZON, blanks_and_doubles, swing_score, team_next_fixtures,
)
from ..state import BriefState, FixtureOutlook, FixtureSlot, GwTeams, TeamRun


def fixture_planner(state: BriefState, deps: Deps) -> dict:
    squad, market = state.squad, state.market
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        players = load_players(session)
        teams = load_teams(session)
        fixtures = load_fixtures(session)

    tids: set[int] = set()
    for pk in squad.picks:
        tids.add(players.get(pk.player_id, {}).get("team_id"))
    for rs in market.replacements:
        for c in rs.candidates:
            tids.add(players.get(c.player_id, {}).get("team_id"))
    for c in market.differentials + market.template_missing:
        tids.add(players.get(c.player_id, {}).get("team_id"))
    tids.discard(None)

    team_runs: list[TeamRun] = []
    entering: list[int] = []
    leaving: list[int] = []
    for tid in sorted(tids):
        runs = team_next_fixtures(fixtures, tid, anchor, DEFAULT_HORIZON)
        next6 = [
            FixtureSlot(gw=r[0], opponent_short=teams.get(r[1], {}).get("short_name", "?"),
                        is_home=r[2], fdr=r[3])
            for r in runs
        ]
        sw = swing_score(runs)
        team_runs.append(TeamRun(team_id=tid, next6=next6, swing=sw))
        if sw >= SWING_ENTER:
            entering.append(tid)
        elif sw <= SWING_LEAVE:
            leaving.append(tid)

    blanks, doubles = blanks_and_doubles(fixtures, sorted(tids), anchor, DEFAULT_HORIZON)
    outlook = FixtureOutlook(
        teams=team_runs,
        entering_good_run=sorted(entering),
        leaving_good_run=sorted(leaving),
        blanks=[GwTeams(**b) for b in blanks],
        doubles=[GwTeams(**d) for d in doubles],
    )
    return {"fixture_outlook": outlook}
