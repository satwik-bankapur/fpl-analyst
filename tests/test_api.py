"""Layer 5 delivery-surface tests (Stage A).

Two DB fixtures, both offline:
  - `golden_sf`: the frozen 2025-26 season synced through the real pipeline (the
    same asset path the graph tests use) — an IN_SEASON DB with ~700 scored players.
  - `_zeroed_preseason_sf`: the day-one dataset — every current player has zeroed
    current-season stats (total_points=0, empty form), with a populated archive, so
    the archive-backed PRESEASON scoring path is the ONLY thing that can produce EP.
    This is the state the live DB is in the moment the real FPL reset fires and the
    first real users arrive; §amendment requires proving the picker + draft→brief
    end-to-end against it.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from engine.scoring import score_all
from fpl.errors import FPLApiError
from fpl.models import Base, EpScore, EuropeanWeek, Fixture, Gameweek, Player, Team, TeamNote
from fpl.models import DraftSquad, EmailSignup
from fpl.sync import sync_fpl
from graph.deps import Deps

from api import mail, service
from api.config import settings
from api.main import app
from api.ratelimit import limiter
from api.service import get_deps

ASSETS = Path(__file__).parent / "assets"


def _load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Client wiring: override get_deps with the test session factory, reset limiter.
# ---------------------------------------------------------------------------

@pytest.fixture
def client_for():
    def make(SF, client_obj=None) -> TestClient:
        app.dependency_overrides[get_deps] = lambda: Deps(session_factory=SF, client=client_obj)
        limiter.reset()
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()
    limiter.reset()


# ---------------------------------------------------------------------------
# Golden DB — frozen season synced through the real pipeline (IN_SEASON).
# ---------------------------------------------------------------------------

class FullFakeClient:
    def __init__(self):
        self.bootstrap = _load("bootstrap_2025-26.json")
        self.fixtures = _load("fixtures_2025-26.json")

    def get_bootstrap(self):
        return copy.deepcopy(self.bootstrap)

    def get_fixtures(self):
        return copy.deepcopy(self.fixtures)

    def close(self):
        pass


@pytest.fixture
def golden_sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sync_fpl(client=FullFakeClient(), session_factory=SF)
    yield SF
    engine.dispose()


@pytest.fixture
def empty_sf():
    """Migrated-but-empty DB — the state a fresh free-tier Neon deploy is in
    before the first cron sync lands."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


def _sample_squad():
    picks = _load("entry_sample_event_38_picks_2025-26.json")
    ordered = sorted(picks["picks"], key=lambda p: p["position"])
    ids = [pk["element"] for pk in ordered]
    cap = next(pk["element"] for pk in picks["picks"] if pk["is_captain"])
    return ids, cap


def _draft_body(ids, captain):
    return {"draft": {"player_ids": ids, "captain_id": captain}}


# ===========================================================================
# Read endpoints
# ===========================================================================

def test_health(golden_sf, client_for):
    r = client_for(golden_sf).get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "IN_SEASON"
    assert body["last_sync"] is not None      # max(ep_scores.computed_at)


def test_players_list_shape_and_cache(golden_sf, client_for):
    r = client_for(golden_sf).get("/api/players")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=3600"
    rows = r.json()
    assert len(rows) > 100
    p = rows[0]
    assert set(p) == {"id", "web_name", "team_short", "position", "price"}
    assert p["position"] in {"GK", "DEF", "MID", "FWD"}


def test_preview_latest(golden_sf, client_for):
    r = client_for(golden_sf).get("/api/preview/latest")
    assert r.status_code == 200
    body = r.json()
    assert "gameweek" in body and isinstance(body["sections"], dict) and body["sections"]


# ===========================================================================
# Brief — draft path (§1.1) + draft read route (drift-noted gap)
# ===========================================================================

def test_post_draft_brief(golden_sf, client_for):
    ids, cap = _sample_squad()
    r = client_for(golden_sf).post("/api/brief", json=_draft_body(ids, cap))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft_id"] and body["brief_id"]
    assert {"verdict", "transfer", "captaincy"} <= set(body["sections"])
    assert body["locked_sections"] == []       # Stage A: no paywall


def test_draft_brief_roundtrip_and_404(golden_sf, client_for):
    client = client_for(golden_sf)
    ids, cap = _sample_squad()
    did = client.post("/api/brief", json=_draft_body(ids, cap)).json()["draft_id"]

    got = client.get(f"/api/brief/draft/{did}")
    assert got.status_code == 200
    assert {"verdict", "transfer", "captaincy"} <= set(got.json()["sections"])

    missing = client.get(f"/api/brief/draft/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "invalid_draft_id"


# ===========================================================================
# Rate limit (§1.1): 5 generations/IP/hour, cache hits exempt
# ===========================================================================

def test_rate_limit_blocks_sixth_generation(golden_sf, client_for):
    client = client_for(golden_sf)
    ids, _ = _sample_squad()
    # 6 distinct captains -> 6 distinct input hashes -> 6 real generations.
    for cap in ids[:5]:
        assert client.post("/api/brief", json=_draft_body(ids, cap)).status_code == 200
    sixth = client.post("/api/brief", json=_draft_body(ids, ids[5]))
    assert sixth.status_code == 429
    assert "retry-after" in {k.lower() for k in sixth.headers}


def test_cache_hits_do_not_count(golden_sf, client_for):
    client = client_for(golden_sf)
    ids, cap = _sample_squad()
    body = _draft_body(ids, cap)
    # First is a miss (1 generation); the next 8 are identical -> cache hits, exempt.
    for _ in range(9):
        assert client.post("/api/brief", json=body).status_code == 200


# ===========================================================================
# Signup (§1.5) + Resend welcome (mocked)
# ===========================================================================

def test_signup_persists_and_sends_welcome(golden_sf, client_for, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send_welcome", lambda email, **kw: sent.append(email) or True)
    r = client_for(golden_sf).post("/api/signup", json={"email": "fan@example.com"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert sent == ["fan@example.com"]
    with golden_sf() as s:
        assert s.get(EmailSignup, "fan@example.com") is not None


def test_signup_rejects_bad_email(golden_sf, client_for):
    r = client_for(golden_sf).post("/api/signup", json={"email": "notanemail"})
    assert r.status_code == 422


def test_welcome_noop_without_key(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    assert mail.send_welcome("x@example.com") is False   # skipped, no network


# ===========================================================================
# Admin (§1.9): bearer ADMIN_TOKEN
# ===========================================================================

def test_admin_notes_requires_token(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    body = {"team_id": 1, "note": "Set-piece routine changed."}

    assert client.post("/api/admin/notes", json=body).status_code == 401
    ok = client.post("/api/admin/notes", json=body,
                     headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    with golden_sf() as s:
        assert s.get(TeamNote, 1).note == "Set-piece routine changed."


# ===========================================================================
# Admin sync (free-tier topology): the external-cron replacement for the
# in-process APScheduler, which a sleeping Render free service can't hold.
# ===========================================================================

def _ep_count(SF) -> int:
    with SF() as s:
        return s.scalar(select(func.count()).select_from(EpScore))


def test_admin_sync_requires_token_and_populates_db(empty_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(empty_sf, FullFakeClient())

    assert client.post("/api/admin/sync").status_code == 401
    assert _ep_count(empty_sf) == 0            # the rejected call synced nothing

    r = client.post("/api/admin/sync", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "IN_SEASON" and body["ep_rows"] > 100
    assert _ep_count(empty_sf) == body["ep_rows"]   # the reported count is the real one


def test_admin_sync_upstream_failure_is_503_and_leaves_db_untouched(
        empty_sf, client_for, monkeypatch):
    """Stale beats broken: a failed fetch must abort without writing, and must
    fail loudly (503) so the cron job doesn't log a phantom success."""
    monkeypatch.setattr(settings, "admin_token", "secret")

    class DownClient:
        def get_bootstrap(self):
            raise FPLApiError("upstream 503")

        def get_fixtures(self):
            raise FPLApiError("upstream 503")

        def close(self):
            pass

    r = client_for(empty_sf, DownClient()).post(
        "/api/admin/sync", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "sync_aborted"
    assert _ep_count(empty_sf) == 0


# ===========================================================================
# Day-one pre-season data semantics (§amendment) — archive-backed EP only
# ===========================================================================

def _P(pid, team_id, position, price=6.0):
    """A player with ZEROED current-season stats — day-one state."""
    return dict(id=pid, web_name=f"P{pid}", full_name=f"Player {pid}",
                team_id=team_id, position=position, price=price, total_points=0,
                form=0.0, ppg=0.0, minutes=0, status="a", chance_of_playing=None,
                news="", ownership_pct=5.0, xg=0, xa=0, xgi=0, xgc=0,
                transfers_in_event=0, transfers_out_event=0)


def _archive_row(pid, team_id, position, price=6.0):
    """Real prior-season stats — the only thing that can produce EP day one."""
    return dict(id=pid, full_name=f"Player {pid}", team_id=team_id, position=position,
                price=price, ppg=5.0, xgi=8.0, minutes=2500, season="2024-25")


def _T(tid, short):
    return dict(id=tid, name=short, short_name=short,
                strength_overall_home=1200, strength_overall_away=1200,
                strength_attack_home=1200, strength_attack_away=1200,
                strength_defence_home=1200, strength_defence_away=1200)


def _G(gid, is_next=False):
    return dict(id=gid, name=f"GW{gid}", deadline=datetime(2026, 8, 1, tzinfo=timezone.utc),
                finished=False, is_current=False, is_next=is_next,
                avg_score=None, most_captained=None)


def _FX(fid, gw, home, away):
    return dict(id=fid, gameweek=gw, home_team=home, away_team=away,
                home_difficulty=3, away_difficulty=3, kickoff=None,
                finished=False, home_score=None, away_score=None)


# Day-one squad rules (2026-07-30): POST /api/brief now enforces the FPL squad
# rules server-side, and MAX_PER_CLUB=3 means a legal 15 needs at least 5 clubs.
# The fixture therefore spans 6 clubs instead of 2. DAY_ONE_SQUAD below is a
# hand-checked legal 15 out of it — 2/5/5/3 across 6 clubs, £90.0m.
_DAY_ONE_TEAMS = 6
_DAY_ONE_PLAYERS = 22


def _day_one_team(pid: int) -> int:
    return ((pid - 1) % _DAY_ONE_TEAMS) + 1


def _day_one_position(pid: int) -> int:
    return (pid % 4) + 1


DAY_ONE_SQUAD = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 17, 18]


@pytest.fixture
def zeroed_preseason():
    """Day-one DB: zeroed current players, populated archive, EP via score_all."""
    teams = [_T(tid, f"T{tid}") for tid in range(1, _DAY_ONE_TEAMS + 1)]
    # 22 players spread round-robin over 6 clubs and all four positions.
    players = [_P(pid, team_id=_day_one_team(pid), position=_day_one_position(pid))
               for pid in range(1, _DAY_ONE_PLAYERS + 1)]
    archive = [_archive_row(p["id"], p["team_id"], p["position"]) for p in players]
    gameweeks = [_G(1, is_next=True), _G(2), _G(3), _G(4)]
    # Every team plays every horizon GW -> no blanks.
    fixtures = [_FX(gw * 10 + h, gw, h, h + 1)
                for gw in (1, 2, 3, 4) for h in (1, 3, 5)]

    results = score_all(players, teams, fixtures, gameweeks,
                        mode="PRESEASON", anchor=1, archive=archive)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SF() as s, s.begin():
        for t in teams:
            s.add(Team(**t))
        for g in gameweeks:
            s.add(Gameweek(**g))
        for p in players:
            s.add(Player(**p))
        for f in fixtures:
            s.add(Fixture(**f))
        for r in results:
            s.add(EpScore(**r.to_row()))
    yield SF, results
    engine.dispose()


def test_day_one_archive_backed_scoring_is_nonzero(zeroed_preseason):
    """Sanity: with zeroed current stats, EP is nonzero ONLY because the archive
    carried it. A current-only score would be flat 0 (form/ppg/xgi all zero)."""
    _SF, results = zeroed_preseason
    assert any(r.ep_next4 > 0 for r in results)
    assert all(r.components["data_basis"].startswith("archive_") for r in results)


def test_day_one_mode_is_preseason(zeroed_preseason, client_for):
    SF, _ = zeroed_preseason
    assert client_for(SF).get("/api/health").json()["mode"] == "PRESEASON"


def test_day_one_picker_and_draft_end_to_end(zeroed_preseason, client_for):
    SF, _ = zeroed_preseason
    client = client_for(SF)

    # 1) Picker payload works day one (zeroed players still listed with price/pos).
    players = client.get("/api/players").json()
    assert len(players) == _DAY_ONE_PLAYERS and all(p["price"] == 6.0 for p in players)

    # 2) Full draft -> brief on archive-backed EP, at the API level.
    r = client.post("/api/brief", json=_draft_body(DAY_ONE_SQUAD, DAY_ONE_SQUAD[0]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"verdict", "transfer", "captaincy"} <= set(body["sections"])
    assert body["gameweek"] == 0        # pre-season brief anchor


def test_preseason_team_id_routes_to_draft(zeroed_preseason, client_for):
    SF, _ = zeroed_preseason
    r = client_for(SF).post("/api/brief", json={"team_id": 123})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "no_picks_yet" and detail["hint"] == "preseason_draft"


# ===========================================================================
# Squad-rule validation on POST /api/brief (2026-07-30)
#
# POST /api/brief is public and unauthenticated. The picker enforces the same
# rules in the browser, which stops honest mistakes and nothing else — every
# case below is a request the picker cannot produce but curl can. Each asserts
# the SPECIFIC error code, because "400" alone would let two rules collapse
# into one and go unnoticed.
# ===========================================================================

def _pool():
    """(id, position, club, price) for every player in the frozen season."""
    return [{"id": e["id"], "pos": e["element_type"], "team": e["team"],
             "price": e["now_cost"] / 10}
            for e in _load("bootstrap_2025-26.json")["elements"]]


def _fill(quota, *, cheapest=True, cap=3, seed=()):
    """Greedy squad build to `quota`, honouring a per-club `cap`, from `seed`."""
    pool = sorted(_pool(), key=lambda p: p["price"], reverse=not cheapest)
    chosen = list(seed)
    taken = {p["id"] for p in chosen}
    clubs: dict[int, int] = {}
    counts: dict[int, int] = {}
    for p in chosen:
        clubs[p["team"]] = clubs.get(p["team"], 0) + 1
        counts[p["pos"]] = counts.get(p["pos"], 0) + 1
    for pos, need in quota.items():
        for p in pool:
            if counts.get(pos, 0) >= need:
                break
            if p["pos"] != pos or p["id"] in taken or clubs.get(p["team"], 0) >= cap:
                continue
            chosen.append(p)
            taken.add(p["id"])
            clubs[p["team"]] = clubs.get(p["team"], 0) + 1
            counts[pos] = counts.get(pos, 0) + 1
    return chosen


QUOTA = {1: 2, 2: 5, 3: 5, 4: 3}


def _ids(squad):
    return [p["id"] for p in squad]


def _reject(client, ids, captain, code):
    r = client.post("/api/brief", json=_draft_body(ids, captain))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == code, detail
    assert detail["message"]           # a specific, human-readable reason
    return detail


def test_a_legal_squad_is_accepted(golden_sf, client_for):
    """The acceptance baseline: the same builder the rejection cases mutate."""
    squad = _fill(QUOTA)
    ids = _ids(squad)
    assert len(ids) == 15 and round(sum(p["price"] for p in squad), 1) <= 100.0

    r = client_for(golden_sf).post("/api/brief", json=_draft_body(ids, ids[0]))
    assert r.status_code == 200, r.text
    assert r.json()["draft_id"]


@pytest.mark.parametrize("n", [14, 16])
def test_wrong_squad_size_rejected(golden_sf, client_for, n):
    ids = _ids(_fill(QUOTA))
    ids = ids[:n] if n < 15 else ids + [ids[0] + 500]
    _reject(client_for(golden_sf), ids, ids[0], "squad_size")


def test_duplicate_players_rejected(golden_sf, client_for):
    ids = _ids(_fill(QUOTA))
    ids[-1] = ids[0]                      # still 15 entries, only 14 players
    _reject(client_for(golden_sf), ids, ids[0], "duplicate_players")


def test_unknown_player_ids_rejected(golden_sf, client_for):
    ids = _ids(_fill(QUOTA))
    ids[-1] = 999_999
    detail = _reject(client_for(golden_sf), ids, ids[0], "unknown_players")
    assert "999999" in detail["message"]


def test_wrong_position_counts_rejected(golden_sf, client_for):
    # 3 GK / 5 DEF / 5 MID / 2 FWD — fifteen players, illegal shape.
    ids = _ids(_fill({1: 3, 2: 5, 3: 5, 4: 2}))
    assert len(ids) == 15
    _reject(client_for(golden_sf), ids, ids[0], "position_counts")


def test_over_budget_rejected(golden_sf, client_for):
    squad = _fill(QUOTA, cheapest=False)     # the 15 most expensive legal picks
    assert round(sum(p["price"] for p in squad), 1) > 100.0
    ids = _ids(squad)
    detail = _reject(client_for(golden_sf), ids, ids[0], "budget_exceeded")
    assert "100.0" in detail["message"]


def test_fourth_player_from_one_club_rejected(golden_sf, client_for):
    """The club cap. MarketScout has always honoured it; the endpoint did not."""
    pool = _pool()
    club = pool[0]["team"]
    # Four from one club, one per position, so ONLY the club rule is broken.
    seed = [next(p for p in pool if p["team"] == club and p["pos"] == pos)
            for pos in (1, 2, 3, 4)]
    squad = _fill(QUOTA, seed=seed)
    ids = _ids(squad)
    assert len(ids) == 15
    assert sum(1 for p in squad if p["team"] == club) == 4

    detail = _reject(client_for(golden_sf), ids, ids[0], "club_over_cap")
    assert "ARS" in detail["message"] and "(4)" in detail["message"]


def test_captain_outside_the_squad_rejected(golden_sf, client_for):
    ids = _ids(_fill(QUOTA))
    outsider = next(p["id"] for p in _pool() if p["id"] not in ids)
    _reject(client_for(golden_sf), ids, outsider, "captain_not_in_squad")


def test_no_captain_is_still_allowed(golden_sf, client_for):
    """captain_id is optional — the Strategist picks its own. Absent != invalid."""
    ids = _ids(_fill(QUOTA))
    r = client_for(golden_sf).post("/api/brief", json=_draft_body(ids, None))
    assert r.status_code == 200, r.text


def test_a_rejected_draft_is_not_persisted(golden_sf, client_for):
    """An illegal squad must not leave a draft row (or a signup) behind."""
    ids = _ids(_fill(QUOTA))[:14]
    client = client_for(golden_sf)
    r = client.post("/api/brief", json={"draft": {"player_ids": ids, "captain_id": ids[0]},
                                        "email": "nope@example.com"})
    assert r.status_code == 400
    with golden_sf() as s:
        assert s.scalar(select(func.count()).select_from(DraftSquad)) == 0
        assert s.scalar(select(func.count()).select_from(EmailSignup)) == 0


def test_the_club_cap_is_one_shared_constant():
    """MarketScout, the API validator and the frontend must not disagree."""
    import importlib
    from pathlib import Path as _Path

    from graph.config import MAX_PER_CLUB

    # `graph.nodes.market_scout` the NAME is the node function (re-exported in
    # graph/nodes/__init__.py), so reach the module itself.
    ms = importlib.import_module("graph.nodes.market_scout")

    assert MAX_PER_CLUB == 3                      # FPL's actual rule
    assert ms.MAX_PER_CLUB is MAX_PER_CLUB        # imported, not redefined
    mirror = (_Path(__file__).parents[1] / "web" / "lib" / "api.ts").read_text(
        encoding="utf-8")
    assert f"export const MAX_PER_CLUB = {MAX_PER_CLUB};" in mirror
