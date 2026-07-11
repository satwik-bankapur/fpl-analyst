"""Free-tier generic gameweek preview (§6) — a separate product, one call per GW.

Assembles a global payload (top-8 EP with evidence, top-3 differentials, fixture
runs, blanks/doubles, European weeks, ALL team_notes, most_captained) and renders
the fixed four-section structure. This module ships the deterministic template
(the permanent fallback); the LLM voice pass is Layer-4 §8 prompt-iteration work.

Persisted as a briefs row with fpl_team_id NULL and draft_id NULL (the generic
singleton — see the relaxed CHECK in migration b2c3d4e5f6a7).
"""
from __future__ import annotations

from sqlalchemy import select

from fpl.models import Gameweek, TeamNote

from graph.config import DIFFERENTIAL_MAX_OWNERSHIP, TEMPLATE_MIN_OWNERSHIP
from graph.data import (
    load_ep_map, load_fixtures, load_players, load_teams, resolve_ep_anchor,
)
from graph.fixtures_util import (
    DEFAULT_HORIZON, blanks_and_doubles, swing_score, team_next_fixtures,
)

from .config import PREVIEW_DIFFERENTIALS, PREVIEW_TOP_EP

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
_OK = {"a", "d"}


def _r(x):
    return f"{float(x):.1f}"


def _gather(deps, gameweek=None):
    with deps.session_factory() as session:
        anchor = gameweek or resolve_ep_anchor(session)
        players = load_players(session)
        teams = load_teams(session)
        fixtures = load_fixtures(session)
        ep_map = load_ep_map(session, anchor)
        notes = {n.team_id: n.note for n in session.scalars(select(TeamNote)).all()}
        gw = session.get(Gameweek, anchor)
        most_captained = gw.most_captained if gw else None

    scored = [(pid, ep_map[pid]) for pid in ep_map if pid in players
              and players[pid]["status"] in _OK]

    def ep4(pid):
        return ep_map.get(pid, {}).get("ep_next4", 0.0)

    top_ep = sorted(scored, key=lambda t: t[1]["ep_next4"], reverse=True)[:PREVIEW_TOP_EP]
    differentials = sorted(
        [(pid, e) for pid, e in scored if players[pid]["ownership_pct"] < DIFFERENTIAL_MAX_OWNERSHIP],
        key=lambda t: t[1]["ep_next4"], reverse=True)[:PREVIEW_DIFFERENTIALS]

    # trap-of-the-week input: the most-owned player the model is coolest on
    popular = [(pid, e) for pid, e in scored
               if players[pid]["ownership_pct"] > TEMPLATE_MIN_OWNERSHIP]
    trap = min(popular, key=lambda t: t[1]["ep_next4"], default=None)

    team_ids = sorted(teams)
    entering, leaving = [], []
    for tid in team_ids:
        runs = team_next_fixtures(fixtures, tid, anchor, DEFAULT_HORIZON)
        sw = swing_score(runs)
        if sw >= 0.8:
            entering.append(tid)
        elif sw <= -0.8:
            leaving.append(tid)
    blanks, doubles = blanks_and_doubles(fixtures, team_ids, anchor, DEFAULT_HORIZON)

    return dict(anchor=anchor, players=players, teams=teams, ep_map=ep_map, notes=notes,
                most_captained=most_captained, top_ep=top_ep, differentials=differentials,
                trap=trap, entering=entering, leaving=leaving, blanks=blanks, doubles=doubles)


def assemble_generic_payload(deps, gameweek=None) -> str:
    g = _gather(deps, gameweek)
    players, teams, ep_map, notes = g["players"], g["teams"], g["ep_map"], g["notes"]

    def nm(pid): return players.get(pid, {}).get("web_name", f"#{pid}")
    def short(tid): return teams.get(tid, {}).get("short_name", str(tid))

    L = [f"[GENERIC_PREVIEW] gameweek: {g['anchor']}"]
    if g["most_captained"]:
        L.append(f"most_captained: {nm(g['most_captained'])}")
    L.append("\n[TOP_EP]")
    for pid, e in g["top_ep"]:
        pl = players[pid]
        c = e.get("components", {}) or {}
        L.append(f"- {nm(pid)} [{_POS.get(pl['position'],'?')} {short(pl['team_id'])} "
                 f"£{_r(pl['price'])}] ep {_r(e['ep'])} ep4 {_r(e['ep_next4'])} "
                 f"own {_r(pl['ownership_pct'])}%  flags: {', '.join(c.get('flags', [])) or '-'}")
    L.append("\n[DIFFERENTIALS]")
    for pid, e in g["differentials"]:
        L.append(f"- {nm(pid)} ({short(players[pid]['team_id'])}) ep4 {_r(e['ep_next4'])} "
                 f"own {_r(players[pid]['ownership_pct'])}%")
    if g["trap"]:
        tp, te = g["trap"]
        L.append(f"\n[TRAP_INPUT] {nm(tp)} ({short(players[tp]['team_id'])}) "
                 f"own {_r(players[tp]['ownership_pct'])}% but ep4 only {_r(te['ep_next4'])}")
    L.append("\n[FIXTURES]")
    L.append("entering_good_run: " + (", ".join(short(t) for t in g["entering"]) or "-"))
    L.append("leaving_good_run: " + (", ".join(short(t) for t in g["leaving"]) or "-"))
    for d in g["doubles"]:
        L.append(f"double_gw{d['gw']}: " + ", ".join(short(t) for t in d["team_ids"]))
    for b in g["blanks"]:
        L.append(f"blank_gw{b['gw']}: " + ", ".join(short(t) for t in b["team_ids"]))
    L.append("\n[TEAM_NOTES]")
    for tid in sorted(notes):
        L.append(f"- {short(tid)}: {notes[tid]}")
    return "\n".join(L)


def render_generic_stub(deps, gameweek=None) -> str:
    """Deterministic four-section preview (§6 structure). Robotic; the permanent fallback."""
    g = _gather(deps, gameweek)
    players, teams = g["players"], g["teams"]

    def nm(pid): return players.get(pid, {}).get("web_name", f"#{pid}")
    def short(tid): return teams.get(tid, {}).get("short_name", str(tid))

    L = [f"# Gameweek {g['anchor']} — the week ahead", "", "## The week ahead"]
    if g["most_captained"]:
        L.append(f"The crowd is on {nm(g['most_captained'])} for the armband.")
    top = g["top_ep"]
    if top:
        L.append("Top projected points over the next four: "
                 + ", ".join(f"{nm(pid)} ({_r(e['ep_next4'])})" for pid, e in top[:5]) + ".")

    L.append("\n## Form and fixtures")
    if g["entering"]:
        L.append("Fixtures turning kind for: " + ", ".join(short(t) for t in g["entering"]) + ".")
    if g["leaving"]:
        L.append("Runs getting harder for: " + ", ".join(short(t) for t in g["leaving"]) + ".")
    for d in g["doubles"]:
        L.append(f"Double gameweek {d['gw']}: " + ", ".join(short(t) for t in d["team_ids"]) + ".")
    for b in g["blanks"]:
        L.append(f"Blank gameweek {b['gw']}: " + ", ".join(short(t) for t in b["team_ids"]) + ".")

    L.append("\n## The differentials")
    for pid, e in g["differentials"]:
        L.append(f"- {nm(pid)} ({short(players[pid]['team_id'])}), "
                 f"{_r(players[pid]['ownership_pct'])}% owned, {_r(e['ep_next4'])} over four.")

    L.append("\n## Trap of the week")
    if g["trap"]:
        tp, te = g["trap"]
        L.append(f"{nm(tp)} is in {_r(players[tp]['ownership_pct'])}% of squads, but the model "
                 f"only projects {_r(te['ep_next4'])} over the next four — the crowd may be "
                 f"paying for name, not fixtures.")
    else:
        L.append("No obvious trap this week — the template looks fairly priced.")

    return "\n".join(L)
