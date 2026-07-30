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


# --- starting XI selection (draft path only) -------------------------------
# A draft squad is fifteen names in submission order. FPL has no lineup concept
# before a gameweek opens, so slots 1–15 used to be pick order and the pipeline
# analysed — and reported on — an ARBITRARY eleven. That is how a demo brief came
# to bench Haaland. Since 2026-07-30 the draft path COMPUTES the XI; see the
# layer3 §2.1 drift note.
#
# The live IN_SEASON path is untouched: there, slots come from the manager's real
# lineup and are not ours to second-guess.
XI_SIZE = 11
BENCH_SIZE = 4
# position -> (min, max) in a legal starting XI. 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD.
FORMATION_LIMITS = {1: (1, 1), 2: (3, 5), 3: (2, 5), 4: (1, 3)}
GK = 1


def _legal_formations() -> list[dict[int, int]]:
    """Every position split that makes a legal XI (there are eight)."""
    out = []
    for d in range(*_span(2)):
        for m in range(*_span(3)):
            f = XI_SIZE - FORMATION_LIMITS[GK][0] - d - m
            if FORMATION_LIMITS[4][0] <= f <= FORMATION_LIMITS[4][1]:
                out.append({1: 1, 2: d, 3: m, 4: f})
    return out


def _span(pos: int) -> tuple[int, int]:
    lo, hi = FORMATION_LIMITS[pos]
    return lo, hi + 1


def _is_legal(counts: dict[int, int]) -> bool:
    if sum(counts.values()) != XI_SIZE:
        return False
    return all(lo <= counts.get(pos, 0) <= hi for pos, (lo, hi) in FORMATION_LIMITS.items())


def _best_xi(player_ids, players, ep) -> list[int] | None:
    """The highest summed anchor-GW `ep` eleven that satisfies the formation rules.

    Exact, not a heuristic: within a fixed formation the positions are
    independent, so taking the top-n per position is optimal — and there are only
    eight formations to try. Returns None when the fifteen cannot field a legal
    XI at all (a draft row predating server-side squad validation), in which case
    the caller keeps submission order rather than inventing something.
    """
    by_pos: dict[int, list[int]] = {}
    for pid in player_ids:
        pos = players.get(pid, {}).get("position")
        if pos is not None:
            by_pos.setdefault(pos, []).append(pid)
    for pos in by_pos:                       # ep desc, id as a stable tie-break
        by_pos[pos].sort(key=lambda pid: (-ep(pid), pid))

    best, best_total = None, None
    for form in _legal_formations():
        if any(len(by_pos.get(pos, [])) < n for pos, n in form.items()):
            continue
        xi = [pid for pos, n in sorted(form.items()) for pid in by_pos[pos][:n]]
        total = sum(ep(pid) for pid in xi)
        if best_total is None or total > best_total:
            best, best_total = xi, total
    return best


def _keep_captain_starting(xi: list[int], captain_id, players, ep) -> list[int]:
    """Never bench the user's captain silently.

    The captain is the one pick the manager actually made, so if the optimiser
    leaves them out we swap them back in over the lowest-`ep` starter whose
    departure still leaves a legal formation. Costs projected points by
    construction — that is the point; the alternative is quietly overriding an
    explicit choice.
    """
    if captain_id is None or captain_id in xi:
        return xi
    cap_pos = players.get(captain_id, {}).get("position")
    if cap_pos is None:
        return xi

    counts: dict[int, int] = {}
    for pid in xi:
        pos = players.get(pid, {}).get("position")
        counts[pos] = counts.get(pos, 0) + 1

    for out in sorted(xi, key=lambda pid: (ep(pid), pid)):
        out_pos = players.get(out, {}).get("position")
        trial = dict(counts)
        trial[out_pos] = trial.get(out_pos, 0) - 1
        trial[cap_pos] = trial.get(cap_pos, 0) + 1
        if _is_legal(trial):
            return [captain_id if pid == out else pid for pid in xi]
    return xi


def _draft_slots(player_ids, captain_id, players, ep_map) -> list[int]:
    """Order the fifteen as [XI…, bench…]; index+1 becomes the pick slot.

    Bench order is ep_next4 desc with the backup keeper pinned to the first bench
    slot — FPL's substitution rules never bring an outfielder on for a keeper, so
    a bench GK's position in the order is fixed, not earned.
    """
    def ep(pid):
        return ep_map.get(pid, {}).get("ep", 0.0)

    def ep4(pid):
        return ep_map.get(pid, {}).get("ep_next4", 0.0)

    xi = _best_xi(player_ids, players, ep)
    if xi is None:
        return list(player_ids)              # not a legal fifteen — leave it alone
    xi = _keep_captain_starting(xi, captain_id, players, ep)

    starting = set(xi)
    bench = [pid for pid in player_ids if pid not in starting]
    bench_gk = [pid for pid in bench if players.get(pid, {}).get("position") == GK]
    bench_out = sorted((pid for pid in bench if pid not in bench_gk),
                       key=lambda pid: (-ep4(pid), pid))
    return xi + bench_gk + bench_out


def resolve_squad(state: BriefState, deps: Deps):
    """Squad resolution only (no diagnosis) — used by the cache layer to compute
    input_hash before deciding whether to run the full graph. Returns (SquadCtx, err)."""
    req = state.request
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session) or 0
        if req.mode == "IN_SEASON":
            return _resolve_live(deps.client, req, anchor)
        # The draft path needs EP to compute the XI, so this is no longer a
        # players-only read — the cache's input_hash is unaffected either way
        # (it hashes ids and multipliers, never slots).
        return _resolve_draft(session, req, load_players(session),
                              load_ep_map(session, anchor))


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
            squad, err = _resolve_draft(session, req, players, ep_map)
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


def _resolve_draft(session, req, players, ep_map):
    if req.draft_id is None:
        return None, PipelineError(code="no_picks_yet", message="No draft squad id provided.")
    draft = load_draft_squad(session, req.draft_id)
    if draft is None:
        return None, PipelineError(
            code="no_picks_yet", message="Draft squad not found — create one first."
        )
    cap = draft["captain_id"]
    # Slots 1–11 start, 12–15 bench. The ORDER is computed, not submitted: a draft
    # squad has no lineup, so trusting the order the player ids arrived in meant
    # analysing an arbitrary eleven (2026-07-30, layer3 §2.1 drift note).
    ordered = _draft_slots(draft["player_ids"], cap, players, ep_map)
    picks = [
        Pick(player_id=pid, slot=i, is_captain=(pid == cap), is_vice=False,
             multiplier=2 if pid == cap else 1)
        for i, pid in enumerate(ordered, 1)
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
