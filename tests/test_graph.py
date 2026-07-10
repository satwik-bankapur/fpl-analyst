"""Layer 3 pipeline tests (§7).

Golden path runs the frozen 2025-26 assets + the sample squad end-to-end through
the stub Writer (both entry points: live IN_SEASON and PRESEASON draft). Targeted
rules (3-per-club, roll, blank exposure, fatal errors, cache) use compact
synthetic in-memory DBs for exact control.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.errors import FPLApiError, InvalidTeamId, NoPicksYet
from fpl.models import (
    Base, Brief, DraftSquad, EpScore, Fixture, Gameweek, Player, Team,
)
from fpl.sync import sync_fpl
from graph import BriefState, run_brief
from graph.cache import compute_input_hash, get_or_generate
from graph.data import resolve_ep_anchor
from graph.deps import Deps
from graph.state import (
    Decisions, Diagnosis, FixtureOutlook, MarketScan, RequestCtx, SquadCtx,
)

ASSETS = Path(__file__).parent / "assets"


def _load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def _now():
    return datetime.now(timezone.utc)


# ===========================================================================
# Golden path — frozen 2025-26 assets + sample squad, full graph, stub Writer
# ===========================================================================

class FullFakeClient:
    """Frozen bootstrap/fixtures for sync + sample entry/picks for the live path."""

    def __init__(self):
        self.bootstrap = _load("bootstrap_2025-26.json")
        self.fixtures = _load("fixtures_2025-26.json")
        self.entry = _load("entry_sample_2025-26.json")
        self.picks = _load("entry_sample_event_38_picks_2025-26.json")

    def get_bootstrap(self):
        return copy.deepcopy(self.bootstrap)

    def get_fixtures(self):
        return copy.deepcopy(self.fixtures)

    def get_entry(self, team_id):
        return copy.deepcopy(self.entry)

    def get_picks(self, team_id, gameweek):
        return copy.deepcopy(self.picks)

    def close(self):
        pass


@pytest.fixture
def golden():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    client = FullFakeClient()
    sync_fpl(client=client, session_factory=SF)          # populate players/teams/fixtures/ep
    with SF() as s:
        anchor = resolve_ep_anchor(s)
    yield SF, Deps(session_factory=SF, client=client), client, anchor
    engine.dispose()


def _make_draft_from_picks(SF, picks_asset) -> uuid.UUID:
    ordered = sorted(picks_asset["picks"], key=lambda p: p["position"])
    ids = [pk["element"] for pk in ordered]
    cap = next(pk["element"] for pk in picks_asset["picks"] if pk["is_captain"])
    did = uuid.uuid4()
    with SF() as s, s.begin():
        s.add(DraftSquad(id=did, player_ids=ids, captain_id=cap))
    return did


def test_golden_path_in_season_end_to_end(golden):
    SF, deps, client, anchor = golden
    state = BriefState(request=RequestCtx(
        mode="IN_SEASON", fpl_team_id=123, gameweek_anchor=anchor, requested_at=_now()))
    final = run_brief(state, deps)

    assert final.error is None
    # every node populated its contract
    assert isinstance(final.squad, SquadCtx) and len(final.squad.picks) == 15
    assert isinstance(final.diagnosis, Diagnosis)
    assert isinstance(final.market, MarketScan)
    assert isinstance(final.fixture_outlook, FixtureOutlook)
    assert isinstance(final.decisions, Decisions)
    assert final.brief_md.startswith("# FPL Brief")
    # captain is one of the XI; bench order covers the 4 bench slots
    xi = {pk.player_id for pk in final.squad.picks if pk.slot <= 11}
    assert final.decisions.captain.pick_id in xi
    assert len(final.decisions.bench_order) == 4
    assert "free_transfers_assumed" in final.warnings


def test_golden_path_preseason_draft_end_to_end(golden):
    SF, deps, client, anchor = golden
    did = _make_draft_from_picks(SF, client.picks)
    state = BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=did, gameweek_anchor=0, requested_at=_now()))
    final = run_brief(state, deps)

    assert final.error is None
    assert isinstance(final.decisions, Decisions)
    assert final.squad.overall_rank is None          # pre-season has no rank
    assert final.brief_md.startswith("# FPL Brief")


def test_state_serializes_under_50kb_and_fast(golden):
    import time
    SF, deps, client, anchor = golden
    state = BriefState(request=RequestCtx(
        mode="IN_SEASON", fpl_team_id=123, gameweek_anchor=anchor, requested_at=_now()))
    t0 = time.monotonic()
    final = run_brief(state, deps)
    elapsed = time.monotonic() - t0
    size = len(final.model_dump_json().encode("utf-8"))
    assert size < 50_000, f"state is {size} bytes"
    assert elapsed < 3.0, f"pipeline took {elapsed:.2f}s"


# ===========================================================================
# Synthetic mini-DB builders for targeted rule tests
# ===========================================================================

def _T(tid, short):
    return dict(id=tid, name=short, short_name=short,
                strength_overall_home=1200, strength_overall_away=1200,
                strength_attack_home=1200, strength_attack_away=1200,
                strength_defence_home=1200, strength_defence_away=1200)


def _G(gid, finished=False, is_next=False, is_current=False):
    return dict(id=gid, name=f"GW{gid}", deadline=datetime(2026, 8, 1, tzinfo=timezone.utc),
                finished=finished, is_current=is_current, is_next=is_next)


def _P(pid, team_id, position=3, price=6.0, status="a", chance=None,
       form=5.0, ppg=5.0, minutes=1800, ownership=10.0, web=None):
    return dict(id=pid, web_name=web or f"P{pid}", full_name=web or f"P{pid}",
                team_id=team_id, position=position, price=price, total_points=100,
                form=form, ppg=ppg, minutes=minutes, status=status,
                chance_of_playing=chance, news="", ownership_pct=ownership,
                xg=5, xa=3, xgi=8, xgc=10, transfers_in_event=0, transfers_out_event=0)


def _EP(pid, gw=1, ep=5.0, ep4=20.0, flags=None, minutes_prob=1.0):
    return dict(player_id=pid, gameweek=gw, ep=ep, ep_next4=ep4,
                components=dict(config_version="v1.3", flags=flags or [],
                                minutes_prob=minutes_prob))


def _build_db(teams, players, ep_rows, gameweeks=None, fixtures=None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SF() as s, s.begin():
        for t in teams:
            s.add(Team(**t))
        for g in (gameweeks or [_G(1, is_next=True)]):
            s.add(Gameweek(**g))
        for p in players:
            s.add(Player(**p))
        for f in (fixtures or []):
            s.add(Fixture(**f))
        for e in ep_rows:
            s.add(EpScore(**e))
    return engine, SF


def _draft(SF, ids, captain):
    did = uuid.uuid4()
    with SF() as s, s.begin():
        s.add(DraftSquad(id=did, player_ids=ids, captain_id=captain))
    return did


def _preseason_state(did):
    return BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=did, gameweek_anchor=0, requested_at=_now()))


# ===========================================================================
# §7.3 — 3-per-club filter
# ===========================================================================

def test_three_per_club_blocks_same_club_replacement():
    teams = [_T(1, "ARS"), _T(2, "BUR"), _T(3, "CHE"), _T(4, "NEW")]
    # squad: 3 ARS (101-103) + injured BUR MID (104, starter) + 11 CHE fillers
    players = [
        _P(101, 1, position=1), _P(102, 1, position=2), _P(103, 1, position=2),
        _P(104, 2, position=3, status="i"),                      # injured -> unavailable
    ] + [_P(p, 3, position=3) for p in range(105, 116)]
    # candidates outside squad, same position (MID): ARS (excluded) vs NEW (ok)
    players += [_P(201, 1, position=3, ownership=5.0, web="ArsMid"),
                _P(202, 4, position=3, ownership=5.0, web="NewMid")]
    ep_rows = [_EP(p, ep4=20.0) for p in range(101, 116)]
    ep_rows += [_EP(104, ep4=5.0), _EP(201, ep4=30.0), _EP(202, ep4=25.0)]
    # de-dup 104 (added twice above); keep the ep4=5 one
    ep_rows = [e for e in ep_rows if not (e["player_id"] == 104 and e["ep_next4"] == 20.0)]

    engine, SF = _build_db(teams, players, ep_rows)
    try:
        did = _draft(SF, list(range(101, 116)), captain=101)
        final = run_brief(_preseason_state(did), Deps(session_factory=SF))
        rsets = {rs.problem_player_id: rs for rs in final.market.replacements}
        assert 104 in rsets, "injured starter should get a replacement set"
        cand_ids = {c.player_id for c in rsets[104].candidates}
        assert 201 not in cand_ids, "ARS candidate must be blocked (squad already has 3 ARS)"
        assert 202 in cand_ids, "NEW candidate is allowed"
    finally:
        engine.dispose()


# ===========================================================================
# §7.4 — no-problems squad -> roll, no empty-list crash
# ===========================================================================

def test_no_problems_squad_rolls():
    teams = [_T(1, "ARS"), _T(2, "BUR")]
    players = [_P(p, 1 if p <= 108 else 2, position=((p % 4) + 1)) for p in range(101, 116)]
    ep_rows = [_EP(p, ep=6.0, ep4=24.0) for p in range(101, 116)]  # all healthy, bench not dead
    engine, SF = _build_db(teams, players, ep_rows)
    try:
        did = _draft(SF, list(range(101, 116)), captain=101)
        final = run_brief(_preseason_state(did), Deps(session_factory=SF))
        assert final.error is None
        assert final.diagnosis.problems == []
        assert final.decisions.transfer.action == "roll"
        assert final.decisions.transfer.in_id is None
        assert final.brief_md.startswith("# FPL Brief")
    finally:
        engine.dispose()


# ===========================================================================
# §7.5 — blank-heavy horizon -> blank_exposure, chip_stance unaffected
# ===========================================================================

def test_blank_exposure_raised_chip_unaffected():
    teams = [_T(1, "ARS")]
    players = [_P(p, 1, position=((p % 4) + 1)) for p in range(101, 116)]
    # 5 starters (101-105) carry a blank_gw flag; no double_gw anywhere
    ep_rows = []
    for p in range(101, 116):
        flags = ["blank_gw:3"] if p <= 105 else []
        ep_rows.append(_EP(p, flags=flags))
    engine, SF = _build_db(teams, players, ep_rows)
    try:
        did = _draft(SF, list(range(101, 116)), captain=101)
        final = run_brief(_preseason_state(did), Deps(session_factory=SF))
        types = {pr.type for pr in final.diagnosis.problems}
        assert "blank_exposure" in types
        assert final.decisions.chip_stance == "hold"       # v1: unaffected by blanks
    finally:
        engine.dispose()


# ===========================================================================
# cold_form is IN_SEASON only (Layer 2 §5.1 — form undefined pre-season)
# ===========================================================================

def test_cold_form_suppressed_in_preseason():
    """Pre-season draft of zero-form players raises NO cold_form (form is undefined)."""
    teams = [_T(1, "ARS")]
    # form 0.0, ppg 5.0, nailed — would trip cold_form if the guard were absent
    players = [_P(p, 1, position=((p % 4) + 1), form=0.0, ppg=5.0) for p in range(101, 116)]
    ep_rows = [_EP(p, minutes_prob=1.0) for p in range(101, 116)]
    engine, SF = _build_db(teams, players, ep_rows)
    try:
        did = _draft(SF, list(range(101, 116)), captain=101)
        final = run_brief(_preseason_state(did), Deps(session_factory=SF))
        assert not any(pr.type == "cold_form" for pr in final.diagnosis.problems)
    finally:
        engine.dispose()


def test_cold_form_still_flagged_in_season():
    """A genuinely cold nailed player IS flagged in an IN_SEASON brief."""
    teams = [_T(1, "ARS")]
    # player 101: form 2.0 < 0.6*ppg(6.0)=3.6, minutes_prob 1.0 -> cold_form
    players = [_P(101, 1, position=3, form=2.0, ppg=6.0)]
    players += [_P(p, 1, position=((p % 4) + 1), form=5.0, ppg=5.0) for p in range(102, 116)]
    ep_rows = [_EP(p, minutes_prob=1.0) for p in range(101, 116)]
    engine, SF = _build_db(teams, players, ep_rows)

    class _Client:  # serves a live squad = players 101-115, 101 captain
        def get_entry(self, team_id):
            return {"name": "T", "summary_overall_rank": 1}

        def get_picks(self, team_id, gameweek):
            return {"active_chip": None, "entry_history": {"bank": 0, "value": 1000},
                    "picks": [{"element": p, "position": p - 100,
                               "is_captain": p == 101, "is_vice_captain": p == 102,
                               "multiplier": 2 if p == 101 else 1} for p in range(101, 116)]}

        def close(self):
            pass

    try:
        state = BriefState(request=RequestCtx(
            mode="IN_SEASON", fpl_team_id=1, gameweek_anchor=1, requested_at=_now()))
        final = run_brief(state, Deps(session_factory=SF, client=_Client()))
        cold = [pr for pr in final.diagnosis.problems if pr.type == "cold_form"]
        assert [pr.player_id for pr in cold] == [101]
    finally:
        engine.dispose()


# ===========================================================================
# §7.6 — fatal-error paths: correct error, no partial brief, no LLM
# ===========================================================================

class _RaisingClient:
    def __init__(self, exc):
        self.exc = exc

    def get_entry(self, team_id):
        raise self.exc

    def get_picks(self, team_id, gameweek):
        raise self.exc

    def close(self):
        pass


def _in_season_state():
    return BriefState(request=RequestCtx(
        mode="IN_SEASON", fpl_team_id=999, gameweek_anchor=1, requested_at=_now()))


@pytest.mark.parametrize("exc,code", [
    (InvalidTeamId(999), "invalid_team_id"),
    (NoPicksYet(999, 1), "no_picks_yet"),
    (FPLApiError("timeout"), "fpl_api_down"),
])
def test_fatal_live_errors(exc, code):
    teams = [_T(1, "ARS")]
    players = [_P(p, 1) for p in range(101, 116)]
    ep_rows = [_EP(p) for p in range(101, 116)]
    engine, SF = _build_db(teams, players, ep_rows)
    try:
        deps = Deps(session_factory=SF, client=_RaisingClient(exc))
        final = run_brief(_in_season_state(), deps)
        assert final.error is not None and final.error.code == code
        assert final.decisions is None and final.market is None   # no partial brief
        assert final.brief_md.startswith("# Brief unavailable")
    finally:
        engine.dispose()


def test_empty_ep_table_is_fatal():
    teams = [_T(1, "ARS")]
    players = [_P(p, 1) for p in range(101, 116)]
    engine, SF = _build_db(teams, players, ep_rows=[])       # no EP scores
    try:
        deps = Deps(session_factory=SF, client=FullFakeClient())
        final = run_brief(_in_season_state(), deps)
        assert final.error is not None and final.error.code == "empty_ep_table"
        assert final.decisions is None
    finally:
        engine.dispose()


# ===========================================================================
# §7.7 — cache: identical request -> one run; changed captain -> rerun
# ===========================================================================

def test_cache_hit_and_captain_invalidation():
    teams = [_T(1, "ARS"), _T(2, "BUR")]
    players = [_P(p, 1 if p <= 108 else 2, position=((p % 4) + 1)) for p in range(101, 116)]
    ep_rows = [_EP(p, ep=6.0, ep4=24.0) for p in range(101, 116)]
    engine, SF = _build_db(teams, players, ep_rows)
    try:
        deps = Deps(session_factory=SF)
        runs = {"n": 0}

        def spy(state, d):
            runs["n"] += 1
            return run_brief(state, d)

        did = _draft(SF, list(range(101, 116)), captain=101)
        s1, hit1 = get_or_generate(_preseason_state(did), deps, run_fn=spy)
        s2, hit2 = get_or_generate(_preseason_state(did), deps, run_fn=spy)
        assert hit1 is False and hit2 is True
        assert runs["n"] == 1, "identical request must run the graph exactly once"

        # change the captain -> new input_hash -> rerun
        did2 = _draft(SF, list(range(101, 116)), captain=102)
        s3, hit3 = get_or_generate(_preseason_state(did2), deps, run_fn=spy)
        assert hit3 is False
        assert runs["n"] == 2

        with SF() as s:
            assert s.scalar(select(func.count()).select_from(Brief)) == 2
    finally:
        engine.dispose()
