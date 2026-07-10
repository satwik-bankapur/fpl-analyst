"""SquadAnalyst (§2.1) — resolve the squad, join to EP, diagnose problems.

Fatal errors originate here: empty_ep_table (no EP data), invalid_team_id /
no_picks_yet / fpl_api_down (live fetch). On any of these we return only
{"error": ...}; the graph short-circuits every downstream node.
"""
from __future__ import annotations

from fpl.errors import FPLApiError, FPLError, InvalidTeamId, NoPicksYet

from ..config import (
    BLANK_EXPOSURE_STARTERS, COLD_FORM_MIN_PROB, COLD_FORM_RATIO,
    DEAD_BENCH_EP_NEXT4, FIXTURE_CLIFF_SWING, INJURY_DOUBT_CHANCE,
)
from ..data import (
    load_draft_squad, load_ep_map, load_fixtures, load_players, load_teams,
    resolve_ep_anchor,
)
from ..deps import Deps
from ..fixtures_util import DEFAULT_HORIZON, swing_score, team_next_fixtures
from ..state import (
    BriefState, Diagnosis, Pick, PipelineError, Problem, SquadCtx,
)


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def resolve_squad(state: BriefState, deps: Deps):
    """Squad resolution only (no diagnosis) — used by the cache layer to compute
    input_hash before deciding whether to run the full graph. Returns (SquadCtx, err)."""
    req = state.request
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session) or 0
        if req.mode == "IN_SEASON":
            return _resolve_live(deps.client, req, anchor)
        return _resolve_draft(session, req, load_players(session))


def squad_analyst(state: BriefState, deps: Deps) -> dict:
    req = state.request
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        if anchor is None:
            return _fatal("empty_ep_table", "No EP scores available — run a sync first.")
        ep_map = load_ep_map(session, anchor)
        if not ep_map:
            return _fatal("empty_ep_table", "No EP scores at the anchor gameweek.")
        players = load_players(session)
        teams = load_teams(session)
        fixtures = load_fixtures(session)

        if req.mode == "IN_SEASON":
            squad, err = _resolve_live(deps.client, req, anchor)
        else:
            squad, err = _resolve_draft(session, req, players)
        if err is not None:
            return {"error": err}

        diagnosis, warnings = _diagnose(
            squad, ep_map, players, teams, fixtures, anchor, req.mode)

    return {"squad": squad, "diagnosis": diagnosis, "warnings": warnings}


def _fatal(code: str, message: str) -> dict:
    return {"error": PipelineError(code=code, message=message)}


# --- squad resolution ------------------------------------------------------

def _resolve_live(client, req, anchor: int):
    if client is None:
        return None, PipelineError(code="fpl_api_down", message="No FPL client configured.")
    gw = req.gameweek_anchor or anchor
    try:
        entry = client.get_entry(req.fpl_team_id)
        picks_resp = client.get_picks(req.fpl_team_id, gw)
    except InvalidTeamId as e:
        return None, PipelineError(code="invalid_team_id", message=str(e))
    except NoPicksYet as e:
        return None, PipelineError(code="no_picks_yet", message=str(e))
    except (FPLApiError, FPLError) as e:
        return None, PipelineError(code="fpl_api_down", message=f"FPL API unavailable: {e}")
    except Exception as e:  # httpx/other transport error surfaced as api-down
        return None, PipelineError(code="fpl_api_down", message=f"FPL API unavailable: {e}")

    hist = picks_resp.get("entry_history") or {}
    picks = [
        Pick(
            player_id=pk["element"], slot=pk["position"],
            is_captain=bool(pk.get("is_captain")), is_vice=bool(pk.get("is_vice_captain")),
            multiplier=int(pk.get("multiplier", 1)),
        )
        for pk in picks_resp.get("picks", [])
    ]
    squad = SquadCtx(
        team_name=entry.get("name"),
        overall_rank=entry.get("summary_overall_rank"),
        bank=_f(hist.get("bank")) / 10.0,           # ×10 encoding
        squad_value=_f(hist.get("value")) / 10.0,
        active_chip=picks_resp.get("active_chip"),
        picks=picks,
    )
    return squad, None


def _resolve_draft(session, req, players):
    if req.draft_id is None:
        return None, PipelineError(code="no_picks_yet", message="No draft squad id provided.")
    draft = load_draft_squad(session, req.draft_id)
    if draft is None:
        return None, PipelineError(
            code="no_picks_yet", message="Draft squad not found — create one first."
        )
    cap = draft["captain_id"]
    # Pre-season draft is an ordered 15: slots 1–11 start, 12–15 bench (order = the
    # manager's chosen XI; the sample fixture preserves the real GW lineup order).
    picks = [
        Pick(player_id=pid, slot=i, is_captain=(pid == cap), is_vice=False,
             multiplier=2 if pid == cap else 1)
        for i, pid in enumerate(draft["player_ids"], 1)
    ]
    squad_value = round(sum(players.get(pid, {}).get("price", 0.0)
                            for pid in draft["player_ids"]), 1)
    squad = SquadCtx(team_name=None, overall_rank=None, bank=0.0,
                     squad_value=squad_value, active_chip=None, picks=picks)
    return squad, None


# --- diagnosis -------------------------------------------------------------

def _diagnose(squad, ep_map, players, teams, fixtures, anchor, mode):
    starters = [pk for pk in squad.picks if pk.slot <= 11]
    bench = [pk for pk in squad.picks if pk.slot >= 12]

    def ep(pid):  return ep_map.get(pid, {}).get("ep", 0.0)
    def ep4(pid): return ep_map.get(pid, {}).get("ep_next4", 0.0)
    def comp(pid): return ep_map.get(pid, {}).get("components", {}) or {}
    def flags(pid): return comp(pid).get("flags", []) or []

    squad_ep_gw = round(sum(ep(pk.player_id) * pk.multiplier for pk in starters), 2)
    squad_ep_next4 = round(sum(ep4(pk.player_id) * pk.multiplier for pk in starters), 2)

    problems: list[Problem] = []
    blank_starters: list[int] = []

    for pk in starters:
        pid = pk.player_id
        pl = players.get(pid, {})
        status = pl.get("status", "a")
        chance = pl.get("chance_of_playing")
        news = pl.get("news", "")
        fl = flags(pid)

        if status in ("i", "s", "u", "n"):
            problems.append(Problem(
                type="unavailable", player_id=pid, severity=3,
                evidence=[f"status:{status}"] + ([news] if news else [])
                         + [f for f in fl if f.startswith("unavailable")]))
        elif status == "d" and chance is not None and chance <= INJURY_DOUBT_CHANCE:
            problems.append(Problem(
                type="injury_doubt", player_id=pid, severity=2,
                evidence=[f"chance_of_playing:{chance}%"] + ([news] if news else [])
                         + [f for f in fl if f.startswith("injury_doubt")]))

        runs = team_next_fixtures(fixtures, pl.get("team_id"), anchor, DEFAULT_HORIZON)
        swing = swing_score(runs)
        if swing <= FIXTURE_CLIFF_SWING:
            short = teams.get(pl.get("team_id"), {}).get("short_name", "?")
            problems.append(Problem(
                type="fixture_cliff", player_id=pid, severity=2,
                evidence=[f"swing:{swing}", f"team:{short}"]))

        # cold_form is IN_SEASON only: pre-season the raw `form` field is undefined
        # (Layer 2 §5.1 — the engine substitutes archive ppg there), so a zero-form
        # snapshot would false-positive the entire squad. See spec §8 drift note.
        if mode == "IN_SEASON":
            form, ppg = pl.get("form", 0.0), pl.get("ppg", 0.0)
            mprob = comp(pid).get("minutes_prob", 0.0)
            if ppg > 0 and form < COLD_FORM_RATIO * ppg and mprob > COLD_FORM_MIN_PROB:
                problems.append(Problem(
                    type="cold_form", player_id=pid, severity=1,
                    evidence=[f"form:{form}", f"ppg:{ppg}", f"minutes_prob:{mprob}"]))

        if any(f.startswith("blank_gw") for f in fl):
            blank_starters.append(pid)

    if len(blank_starters) >= BLANK_EXPOSURE_STARTERS:
        problems.append(Problem(
            type="blank_exposure", player_id=None, severity=2,
            evidence=[f"{len(blank_starters)} starters blank in the next-4 horizon"]
                     + [f"pid:{p}" for p in blank_starters]))

    for pk in bench:
        pid = pk.player_id
        if ep4(pid) < DEAD_BENCH_EP_NEXT4:
            problems.append(Problem(
                type="dead_bench_spot", player_id=pid, severity=1,
                evidence=[f"ep_next4:{round(ep4(pid), 2)}"]))

    captain = next((pk.player_id for pk in squad.picks if pk.is_captain), None)
    diagnosis = Diagnosis(
        squad_ep_gw=squad_ep_gw, squad_ep_next4=squad_ep_next4,
        problems=problems, free_transfers=1, captain_current=captain)
    return diagnosis, ["free_transfers_assumed"]
