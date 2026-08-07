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
from api.ratelimit import admin_limiter, limiter, request_limiter
from api.service import get_deps

ASSETS = Path(__file__).parent / "assets"


def _load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Client wiring: override get_deps with the test session factory, reset limiter.
# ---------------------------------------------------------------------------

def _reset_limiters():
    for lim in (limiter, request_limiter, admin_limiter):
        lim.reset()


@pytest.fixture
def client_for():
    def make(SF, client_obj=None) -> TestClient:
        app.dependency_overrides[get_deps] = lambda: Deps(session_factory=SF, client=client_obj)
        _reset_limiters()
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()
    _reset_limiters()


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
    assert set(p) == {"id", "web_name", "full_name", "team_short",
                      "position", "price", "status"}
    assert p["position"] in {"GK", "DEF", "MID", "FWD"}


# --- the picker's universe (2026-08-06) -------------------------------------
#
# These three hold boundary B of scripts/player_coverage.py: the payload is the
# draft picker's ENTIRE universe, so anything this route omits cannot be picked
# at all. The walkthrough that prompted them reported "missing players"; the
# backend turned out to be clean and the losses were all in the browser, but
# nothing stopped a filter being added here later, which is what these are for.

def test_players_list_ships_every_row_whatever_the_status(golden_sf, client_for):
    """No WHERE clause. An injured or suspended player is a legal pick — people
    draft them deliberately for the fixture after next — so availability is
    shipped as data, never as an omission."""
    from sqlalchemy import func, select as sa_select
    from fpl.models import Player

    with golden_sf() as s:
        in_db = s.scalar(sa_select(func.count()).select_from(Player))

    rows = client_for(golden_sf).get("/api/players").json()
    assert len(rows) == in_db, "the payload dropped rows the table has"

    # And the unavailable ones are actually present, not merely counted.
    with golden_sf() as s:
        flagged = set(s.scalars(
            sa_select(Player.id).where(Player.status != "a")).all())
    assert flagged, "fixture has no non-available players; this test proves nothing"
    assert flagged <= {r["id"] for r in rows}


def test_players_list_carries_what_the_search_needs(golden_sf, client_for):
    """`web_name` alone is not searchable enough: FPL abbreviates it ("M.Salah"),
    so the picker needs `full_name` to match a real first name."""
    rows = client_for(golden_sf).get("/api/players").json()
    named = [r for r in rows if r["full_name"]]
    assert len(named) > 100
    # At least one row where the full name carries a word web_name does not —
    # the exact case that returned zero results before this shipped.
    assert any(
        set(r["full_name"].lower().split()) - set(r["web_name"].lower().split())
        for r in named
    )


def test_players_list_has_no_limit(golden_sf, client_for):
    """A cap here would look identical to the row cap that hid 332 of 572
    players in the picker. Neither is allowed to come back quietly."""
    rows = client_for(golden_sf).get("/api/players").json()
    by_pos: dict[str, int] = {}
    for r in rows:
        by_pos[r["position"]] = by_pos.get(r["position"], 0) + 1
    # The frozen 2025-26 snapshot has far more than 60 in every outfield slot;
    # 60 was the old per-position cap, so exceeding it proves no cap survives.
    assert min(by_pos[p] for p in ("DEF", "MID")) > 60, by_pos


def test_coverage_diagnostic_still_mirrors_the_picker():
    """`scripts/player_coverage.py` reimplements the picker's cap and search in
    Python because it cannot import TypeScript. A silent drift between the two
    turns the diagnostic into a confident liar, so pin the pair here.

    This asserts on the SOURCE of DraftPicker.tsx, which is unusual and
    deliberate: the alternative is a browser test runner the repo does not have,
    for a two-constant contract.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    picker = (root / "web" / "components" / "DraftPicker.tsx").read_text(encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "player_coverage", root / "scripts" / "player_coverage.py")
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)

    # 1. The row cap is gone from the picker, and the mirror agrees.
    assert "ROWS_PER_POS" not in picker, (
        "the per-position row cap is back in DraftPicker.tsx — it hid 332 of 572 "
        "players. If that is intended, set ROWS_PER_POS in player_coverage.py to match.")
    assert cov.ROWS_PER_POS == 0

    # 2. Both folds agree on the names that were unfindable before.
    for raw, typed in [("Aït-Nouri", "ait-nouri"), ("Gyökeres", "gyokeres"),
                       ("Ødegaard", "odegaard"), ("Muñoz", "munoz"),
                       ("Fábio Vieira", "fabio vieira"), ("Groß", "gross")]:
        assert cov._picker_search(typed, {"web_name": raw, "full_name": "",
                                          "team_short": "XXX"}), raw

    # 3. The picker still searches all three fields the mirror does.
    for field in ("web_name", "full_name", "team_short"):
        assert field in picker, f"picker no longer references {field}"


def test_preview_latest(golden_sf, client_for):
    r = client_for(golden_sf).get("/api/preview/latest")
    assert r.status_code == 200
    body = r.json()
    assert "gameweek" in body and isinstance(body["sections"], dict) and body["sections"]


def test_the_landing_teaser_lists_outfield_players_not_keepers(golden_sf):
    """The landing page renders ONLY the first preview section, and it was five
    goalkeepers on near-identical projections — which reads as broken to anyone
    who plays FPL (2026-08-06).

    It is also a real signal, not a rendering quirk: on a database with an empty
    `players_archive` every projection collapses onto the (position, price-band)
    baseline and keepers win on clean-sheet points alone. The teaser is the one
    place that failure is public.
    """
    from graph.deps import Deps
    from writer.generic import render_generic_stub

    md = render_generic_stub(Deps(session_factory=golden_sf, client=None))
    headline = md.split("## ")[1]           # the_week_ahead, all the landing shows

    with golden_sf() as s:
        keepers = {p.web_name for p in s.scalars(select(Player).where(Player.position == 1))}
    assert keepers, "fixture has no goalkeepers; this test proves nothing"

    top_line = next(l for l in headline.splitlines() if "Top projected" in l)
    assert "outfield" in top_line, "the list is no longer labelled as outfield-only"
    named = [k for k in keepers if k in top_line]
    assert not named, f"goalkeepers in the landing teaser: {named}"


def test_the_landing_teaser_says_something_about_the_week(golden_sf):
    """One line of shape, so the public preview is not just a list of five names.

    Counts and gameweek numbers only — every clause comes straight off the
    gathered fixture data, and each is dropped when its input is empty rather
    than padded.
    """
    from graph.deps import Deps
    from writer.generic import render_generic_stub

    md = render_generic_stub(Deps(session_factory=golden_sf, client=None))
    headline = md.split("## ")[1]
    assert "The shape of it:" in headline, headline


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


# ===========================================================================
# FREE_BRIEF_ACCESS — the login gate (2026-08-06)
#
# Three modes, all enforced in ONE place (`routes._enforce_brief_access`) and
# all server-side: the decision reads cookies this API set, never anything the
# page claims. `open` is the default and is the behaviour every other test in
# this file already assumes.
# ===========================================================================

@pytest.fixture(autouse=True)
def _access_mode_default(monkeypatch):
    """Pin the default for every test in this file.

    ApiSettings reads a .env, so without this a developer with FREE_BRIEF_ACCESS
    set locally would see unrelated tests fail with a 403 they cannot explain.

    `session_cookie_secure=False` for the same reason tests/test_accounts.py
    does it: TestClient talks http://testserver, and a Secure cookie is
    correctly refused over plain http — so with the production default every
    cookie assertion here would fail while the code was working.
    """
    monkeypatch.setattr(settings, "free_brief_access", "open")
    monkeypatch.setattr(settings, "session_cookie_secure", False)


def _sign_in(client, SF, email="gated@example.com"):
    """Give the client a real session cookie, the way a magic link would."""
    from api import accounts

    with SF() as s, s.begin():
        account, _ = accounts.find_or_create_account(
            s, email, provider="magic_link", accept_terms_now=True)
        token = accounts.create_session(s, account.id)
    client.cookies.set(accounts.SESSION_COOKIE, token)
    return token


def test_open_mode_never_asks_for_a_login(golden_sf, client_for, monkeypatch):
    """The default. Two anonymous briefs in a row, both fine.

    This is the accounts constraint restated as a test: with FREE_BRIEF_ACCESS
    unset, nothing about the free funnel changed.
    """
    monkeypatch.setattr(settings, "free_brief_access", "open")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()
    for _ in range(2):
        r = client.post("/api/brief", json=_draft_body(ids, cap))
        assert r.status_code == 200, r.text


def test_second_gate_gives_one_brief_then_asks(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "free_brief_access", "second_gate")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    first = client.post("/api/brief", json=_draft_body(ids, cap))
    assert first.status_code == 200, "the first brief must stay anonymous"
    assert client.cookies.get("fpl_free_brief"), "the free brief was not marked as spent"

    second = client.post("/api/brief", json=_draft_body(ids, cap))
    assert second.status_code == 403
    assert second.json()["detail"]["error"] == "sign_in_required"
    # A prompt, not a wall: 401 would make browsers and proxies treat this as a
    # credential failure, and the frontend renders 403 beside the squad.
    assert second.status_code != 401


def test_second_gate_lets_a_signed_in_user_carry_on(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "free_brief_access", "second_gate")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    assert client.post("/api/brief", json=_draft_body(ids, cap)).status_code == 200
    assert client.post("/api/brief", json=_draft_body(ids, cap)).status_code == 403

    _sign_in(client, golden_sf)
    after = client.post("/api/brief", json=_draft_body(ids, cap))
    assert after.status_code == 200, "signing in must clear the gate"


def test_login_first_gates_the_very_first_brief(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "free_brief_access", "login_first")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    anon = client.post("/api/brief", json=_draft_body(ids, cap))
    assert anon.status_code == 403
    assert anon.json()["detail"]["error"] == "sign_in_required"

    _sign_in(client, golden_sf)
    assert client.post("/api/brief", json=_draft_body(ids, cap)).status_code == 200


def test_a_gated_request_writes_nothing(golden_sf, client_for, monkeypatch):
    """The gate runs before any row is written.

    A refused caller must not leave a draft_squads row or an email signup behind
    for a brief they never received — that is a junk row and, for the email, a
    subscriber who never got what they gave the address for.
    """
    monkeypatch.setattr(settings, "free_brief_access", "login_first")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    with golden_sf() as s:
        before = s.scalar(select(func.count()).select_from(DraftSquad))

    body = _draft_body(ids, cap) | {"email": "never@example.com"}
    assert client.post("/api/brief", json=body).status_code == 403

    with golden_sf() as s:
        assert s.scalar(select(func.count()).select_from(DraftSquad)) == before
        assert s.get(EmailSignup, "never@example.com") is None


def test_an_illegal_squad_does_not_burn_the_free_brief(golden_sf, client_for, monkeypatch):
    """A 400 must not spend the one anonymous brief.

    `_mark_free_brief_used` is called after generation for exactly this reason:
    a first-time visitor who mistypes a squad should not be asked to sign in as
    a result of our validation.
    """
    monkeypatch.setattr(settings, "free_brief_access", "second_gate")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    bad = client.post("/api/brief", json=_draft_body(ids[:5], cap))
    assert bad.status_code == 400
    assert not client.cookies.get("fpl_free_brief")

    assert client.post("/api/brief", json=_draft_body(ids, cap)).status_code == 200


def test_reading_a_brief_is_never_gated(golden_sf, client_for, monkeypatch):
    """FREE_BRIEF_ACCESS gates GENERATION only.

    Reads stay open in every mode: the emailed full-view link is a PAID delivery
    mechanism, and gating the read would break it for the customers who are
    paying. The paywall still decides what a reader sees; this decides who may
    make a brief exist.
    """
    monkeypatch.setattr(settings, "free_brief_access", "login_first")
    client = client_for(golden_sf)
    ids, cap = _sample_squad()

    _sign_in(client, golden_sf)
    did = client.post("/api/brief", json=_draft_body(ids, cap)).json()["draft_id"]

    client.cookies.clear()
    anon = client.get(f"/api/brief/draft/{did}")
    assert anon.status_code == 200, "an anonymous reader still gets an existing brief"
    assert anon.json()["sections"]


def test_health_publishes_the_mode(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "free_brief_access", "second_gate")
    body = client_for(golden_sf).get("/api/health").json()
    assert body["free_brief_access"] == "second_gate"


def test_an_unknown_mode_refuses_to_boot(monkeypatch):
    """No safe default exists, so a typo must not be guessed at.

    Falling back to `open` silently ignores a gate someone configured; falling
    back to `login_first` silently walls the free product. Both are invisible
    until the conversion numbers move.
    """
    from api.main import _check_access_config

    monkeypatch.setattr(settings, "free_brief_access", "second-gate")  # hyphen typo
    with pytest.raises(RuntimeError, match="FREE_BRIEF_ACCESS"):
        _check_access_config()

    for good in ("open", "second_gate", "login_first"):
        monkeypatch.setattr(settings, "free_brief_access", good)
        _check_access_config()


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

def test_signup_persists_and_sends_a_confirm_link(golden_sf, client_for, monkeypatch):
    """Double opt-in (2026-08-06): signup asks, it does not welcome.

    This test used to assert a welcome email went out here. It deliberately does
    not any more — a welcome to an address nobody proved they can read is the
    exact message double opt-in exists to stop. The welcome now fires on
    confirmation (see test_confirming_sends_the_welcome).
    """
    confirms, welcomes = [], []
    monkeypatch.setattr(mail, "send_confirm_link",
                        lambda email, token, **kw: confirms.append((email, token)) or True)
    monkeypatch.setattr(mail, "send_welcome", lambda email, **kw: welcomes.append(email) or True)

    r = client_for(golden_sf).post("/api/signup", json={"email": "fan@example.com"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert [e for e, _ in confirms] == ["fan@example.com"]
    assert welcomes == [], "a welcome must not precede the confirmation"

    with golden_sf() as s:
        row = s.get(EmailSignup, "fan@example.com")
        assert row is not None
        assert row.tier == "pending"
        assert row.confirmed_at is None
        assert row.confirm_token_hash, "no token was stored to confirm against"


def test_confirming_sends_the_welcome(golden_sf, client_for, monkeypatch):
    """The welcome moved here from signup. This is the moment it is earned."""
    tokens_sent, welcomes = [], []
    monkeypatch.setattr(mail, "send_confirm_link",
                        lambda email, token, **kw: tokens_sent.append(token) or True)
    monkeypatch.setattr(mail, "send_welcome", lambda email, **kw: welcomes.append(email) or True)
    client = client_for(golden_sf)

    client.post("/api/signup", json={"email": "fan@example.com"})
    assert welcomes == []

    r = client.get(f"/api/signup/confirm?token={tokens_sent[0]}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/?confirmed=confirmed")
    assert welcomes == ["fan@example.com"]

    with golden_sf() as s:
        assert s.get(EmailSignup, "fan@example.com").tier == "free"


def test_a_bad_confirm_token_redirects_without_saying_why(golden_sf, client_for):
    """Forged, expired and already-consumed must not be distinguishable to the
    holder of a token — the same rule /auth/verify follows."""
    r = client_for(golden_sf).get("/api/signup/confirm?token=nope", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/?confirmed=invalid")


def test_confirming_never_signs_anyone_in(golden_sf, client_for, monkeypatch):
    """Proving an address is readable is not proving who is reading it.

    A link in an inbox is forwardable, and quietly minting a session from one
    would be a much larger promise than this route makes.
    """
    tokens_sent = []
    monkeypatch.setattr(mail, "send_confirm_link",
                        lambda email, token, **kw: tokens_sent.append(token) or True)
    monkeypatch.setattr(mail, "send_welcome", lambda *a, **kw: True)
    client = client_for(golden_sf)

    client.post("/api/signup", json={"email": "fan@example.com"})
    r = client.get(f"/api/signup/confirm?token={tokens_sent[0]}", follow_redirects=False)
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert client.get("/api/auth/me").json()["signed_in"] is False


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


# ===========================================================================
# Admin console (2026-07-30) — /admin's read surface + the curated-table writes.
#
# The load-bearing test in this block is the no-bump pair. `max(updated_at)` on
# team_notes / european_weeks IS half the brief cache key (docs/CLAUDE.md
# "Configuration"), and the admin UI re-sends whatever is in the form on every
# click. Without skip-if-identical, opening a note and pressing Save without
# typing would invalidate every cached brief in the database.
# ===========================================================================

ADMIN_GETS = [
    "/api/admin/overview",
    "/api/admin/notes",
    "/api/admin/euro-weeks",
    "/api/admin/players",
    "/api/admin/players?q=sa&flagged=true",
    "/api/admin/fixtures/ARS",
    "/api/admin/top",
    "/api/admin/flag-queue",
    "/api/admin/inspect/briefs",
]

# Inspector routes that take an id. Split out because the authorized call answers
# 404 (nothing with that id) rather than 200 — the point here is that the token
# gate fires BEFORE the lookup, so an anonymous caller cannot probe for which
# draft ids exist.
ADMIN_GETS_NOT_FOUND = [
    "/api/admin/inspect/draft/00000000-0000-0000-0000-000000000000",
    "/api/admin/inspect/team/999999/1",
    "/api/admin/inspect/player/99999999",
]


@pytest.mark.parametrize("path", ADMIN_GETS_NOT_FOUND)
def test_admin_inspect_by_id_gates_before_lookup(golden_sf, client_for, monkeypatch, path):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers=_AUTH).status_code == 404

ADMIN_POSTS = [
    ("/api/admin/notes", {"team_id": 1, "note": "x"}),
    ("/api/admin/euro-weeks", {"team_id": 1, "gameweek": 5,
                               "competition": "UCL", "tie_importance": 2}),
    ("/api/admin/euro-weeks/replace", {"rows": []}),
]

_AUTH = {"Authorization": "Bearer secret"}


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_reads_reject_missing_and_wrong_token(golden_sf, client_for, monkeypatch, path):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers=_AUTH).status_code == 200


@pytest.mark.parametrize("path,body", ADMIN_POSTS)
def test_admin_writes_reject_missing_and_wrong_token(golden_sf, client_for, monkeypatch,
                                                     path, body):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(path, json=body, headers=_AUTH).status_code == 200


def test_admin_endpoints_closed_when_token_unset(golden_sf, client_for, monkeypatch):
    """Empty ADMIN_TOKEN closes admin entirely — never open-by-misconfiguration."""
    monkeypatch.setattr(settings, "admin_token", "")
    client = client_for(golden_sf)
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/overview", headers={"Authorization": "Bearer "}).status_code == 401


# --- the cache-key rule ----------------------------------------------------

def _notes_version(SF) -> str:
    """Exactly what the brief cache key reads (graph.data.curated_notes_version)."""
    from graph.data import curated_notes_version
    with SF() as s:
        return curated_notes_version(s)


def test_saving_an_unchanged_note_does_not_bump_updated_at(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    body = {"team_id": 1, "note": "Arteta rotates the left back in Europe weeks."}

    first = client.post("/api/admin/notes", json=body, headers=_AUTH)
    assert first.status_code == 200 and first.json()["changed"] is True
    with golden_sf() as s:
        stamp = s.get(TeamNote, 1).updated_at
    version = _notes_version(golden_sf)

    # The no-op save: identical text, so nothing is written and the cache key holds.
    again = client.post("/api/admin/notes", json=body, headers=_AUTH)
    assert again.status_code == 200 and again.json()["changed"] is False
    with golden_sf() as s:
        assert s.get(TeamNote, 1).updated_at == stamp
        assert s.get(TeamNote, 1).note == body["note"]
    assert _notes_version(golden_sf) == version


def test_saving_a_changed_note_does_bump_updated_at(golden_sf, client_for, monkeypatch):
    """The other half: a real edit MUST invalidate, or briefs go stale silently."""
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)

    client.post("/api/admin/notes", json={"team_id": 1, "note": "First."}, headers=_AUTH)
    with golden_sf() as s:
        stamp = s.get(TeamNote, 1).updated_at
    version = _notes_version(golden_sf)

    changed = client.post("/api/admin/notes",
                          json={"team_id": 1, "note": "Second — pens changed hands."},
                          headers=_AUTH)
    assert changed.json()["changed"] is True
    with golden_sf() as s:
        assert s.get(TeamNote, 1).updated_at > stamp
    assert _notes_version(golden_sf) != version


def test_unchanged_euro_week_grid_does_not_bump(golden_sf, client_for, monkeypatch):
    """Same rule on the grid save — the one most likely to be handed no edits."""
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    rows = [{"team_id": 1, "gameweek": 5, "competition": "UCL", "tie_importance": 3},
            {"team_id": 2, "gameweek": 5, "competition": "UEL", "tie_importance": 2}]

    assert client.post("/api/admin/euro-weeks/replace", json={"rows": rows},
                       headers=_AUTH).json()["changed"] == 2
    with golden_sf() as s:
        stamps = {(r.team_id, r.gameweek): r.updated_at
                  for r in s.scalars(select(EuropeanWeek))}

    assert client.post("/api/admin/euro-weeks/replace", json={"rows": rows},
                       headers=_AUTH).json()["changed"] == 0
    with golden_sf() as s:
        assert {(r.team_id, r.gameweek): r.updated_at
                for r in s.scalars(select(EuropeanWeek))} == stamps

    # Dropping a row is a real change: it must delete, and only that row.
    assert client.post("/api/admin/euro-weeks/replace", json={"rows": rows[:1]},
                       headers=_AUTH).json()["changed"] == 1
    with golden_sf() as s:
        remaining = list(s.scalars(select(EuropeanWeek)))
        assert [(r.team_id, r.gameweek) for r in remaining] == [(1, 5)]
        assert remaining[0].updated_at == stamps[(1, 5)]   # survivor untouched


def test_euro_week_grid_rejects_duplicate_team_gameweek(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    dupe = {"team_id": 1, "gameweek": 5, "competition": "UCL", "tie_importance": 2}
    r = client_for(golden_sf).post("/api/admin/euro-weeks/replace",
                                   json={"rows": [dupe, dict(dupe, competition="UEL")]},
                                   headers=_AUTH)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "duplicate_team_gameweek"


# --- flagged players -------------------------------------------------------

@pytest.fixture
def flagged_sf():
    """Two scored players: one carrying an honesty flag, one clean.

    Hand-seeded rather than taken from the golden season so the assertion is about
    the endpoint's filter, not about which real player happened to be injured in
    the frozen snapshot.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SF() as s, s.begin():
        s.add(Team(**_T(1, "AAA")))
        s.add(Player(**_P(1, 1, 4)))          # flagged
        s.add(Player(**_P(2, 1, 3)))          # clean
        s.add(EpScore(player_id=1, gameweek=1, ep=1.0, ep_next4=4.0, components={
            "match_method": "element_id",
            # One flag from each honesty family the endpoint recognises, plus two
            # it must ignore (`double_gw`, `injury_doubt` are ordinary facts).
            "flags": ["unavailable:i", "thin_minutes_sample", "no_history_fallback",
                      "european_fixture_week:UCL:3", "double_gw:2", "injury_doubt:50%"],
        }))
        s.add(EpScore(player_id=2, gameweek=1, ep=2.0, ep_next4=8.0, components={
            "match_method": "element_id", "flags": ["double_gw:2", "early_season_blend"],
        }))
    yield SF
    engine.dispose()


def test_flagged_players_returns_the_seeded_flagged_player(flagged_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(flagged_sf)

    everyone = client.get("/api/admin/players", headers=_AUTH).json()
    assert everyone["total"] == 2

    only = client.get("/api/admin/players?flagged=true", headers=_AUTH).json()
    assert only["total"] == 1
    row = only["players"][0]
    assert row["id"] == 1
    assert row["ep_next4"] == 4.0
    # The four honesty families are listed; the ordinary scoring facts are not.
    assert row["flags"] == ["unavailable:i", "thin_minutes_sample",
                            "no_history_fallback", "european_fixture_week:UCL:3"]


def test_player_search_matches_on_name(flagged_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(flagged_sf)
    assert client.get("/api/admin/players?q=P1", headers=_AUTH).json()["total"] == 1
    assert client.get("/api/admin/players?q=zzz", headers=_AUTH).json()["total"] == 0


# --- read-surface shapes ---------------------------------------------------

def test_overview_reports_real_counts_and_match_methods(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    body = client_for(golden_sf).get("/api/admin/overview", headers=_AUTH).json()

    assert body["mode"] == "IN_SEASON"
    assert body["last_sync"] is not None
    assert body["counts"]["players"] > 100
    assert body["counts"]["ep_scores"] == body["counts"]["players"]
    assert len(body["teams"]) == 20
    with golden_sf() as s:
        assert body["counts"]["fixtures"] == s.scalar(
            select(func.count()).select_from(Fixture))
    # Every scored player is accounted for in exactly one match_method bucket.
    assert sum(body["match_method"].values()) == body["counts"]["ep_scores"]


def test_notes_endpoint_lists_every_club_even_without_a_note(golden_sf, client_for, monkeypatch):
    """The UI shows 20 cards; a club with no note still needs a card to type into."""
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    rows = client.get("/api/admin/notes", headers=_AUTH).json()
    assert len(rows) == 20
    assert all(r["note"] == "" and r["updated_at"] is None for r in rows)

    client.post("/api/admin/notes", json={"team_id": 1, "note": "Written."}, headers=_AUTH)
    rows = client.get("/api/admin/notes", headers=_AUTH).json()
    assert len(rows) == 20
    written = [r for r in rows if r["note"]]
    assert len(written) == 1 and written[0]["updated_at"] is not None


def test_top_is_ordered_by_ep_next4(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    rows = client_for(golden_sf).get("/api/admin/top?n=20", headers=_AUTH).json()
    assert len(rows) == 20
    assert [r["ep_next4"] for r in rows] == sorted((r["ep_next4"] for r in rows), reverse=True)
    assert all(r["position"] in {"GK", "DEF", "MID", "FWD"} for r in rows)
    assert all(r["team_short"] != "?" for r in rows)


def test_fixtures_endpoint_matches_the_shared_swing_util(golden_sf, client_for, monkeypatch):
    """The admin swing must be the util's swing — not a second implementation."""
    monkeypatch.setattr(settings, "admin_token", "secret")
    from graph.data import load_fixtures, resolve_ep_anchor
    from graph.fixtures_util import swing_score, team_next_fixtures

    body = client_for(golden_sf).get("/api/admin/fixtures/ars?n=6", headers=_AUTH).json()
    assert body["team_short"] == "ARS"          # lookup is case-insensitive
    assert len(body["fixtures"]) <= 6

    with golden_sf() as s:
        expected = team_next_fixtures(load_fixtures(s), body["team_id"],
                                      resolve_ep_anchor(s), 6)
    assert body["swing"] == swing_score(expected)
    assert [f["fdr"] for f in body["fixtures"]] == [r[3] for r in expected]


def test_fixtures_endpoint_404s_on_an_unknown_club(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    r = client_for(golden_sf).get("/api/admin/fixtures/ZZZ", headers=_AUTH)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "unknown_team"


def test_every_served_timestamp_carries_a_utc_offset(golden_sf, client_for, monkeypatch):
    """Naive ISO strings are parsed as LOCAL time by `new Date()` in the browser.

    SQLite hands back naive datetimes, so serializing them raw shifted every
    rendered timestamp by the viewer's UTC offset — an 8-hour-old sync that was
    two hours old (2026-07-30). Assert the offset is present at the boundary.
    """
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    client.post("/api/admin/notes", json={"team_id": 1, "note": "n."}, headers=_AUTH)
    client.post("/api/admin/euro-weeks",
                json={"team_id": 1, "gameweek": 5, "competition": "UCL",
                      "tie_importance": 2}, headers=_AUTH)

    stamps = []
    o = client.get("/api/admin/overview", headers=_AUTH).json()
    stamps += [o["last_sync"], o["next_deadline"]]
    stamps += [r["updated_at"] for r in client.get("/api/admin/notes",
                                                   headers=_AUTH).json() if r["updated_at"]]
    stamps += [r["updated_at"] for r in client.get("/api/admin/euro-weeks",
                                                   headers=_AUTH).json()]
    h = client.get("/api/health").json()
    stamps += [h["last_sync"], h["next_deadline"]]

    assert stamps and all(s is not None for s in stamps)
    for s in stamps:
        parsed = datetime.fromisoformat(s)
        assert parsed.tzinfo is not None, f"naive timestamp served: {s}"
        assert parsed.utcoffset().total_seconds() == 0, f"not UTC: {s}"


def test_admin_is_never_linked_from_a_public_page():
    """/admin is operator-only. A link from any public route leaks its existence."""
    from pathlib import Path as _Path

    web = _Path(__file__).parents[1] / "web"
    public = [p for p in web.glob("app/**/*.tsx") if "app\\admin" not in str(p)
              and "app/admin" not in str(p).replace("\\", "/")]
    public += [p for p in web.glob("components/*.tsx") if "Admin" not in p.name]
    offenders = [str(p) for p in public if "/admin" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"public files link to /admin: {offenders}"


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


# ===========================================================================
# Security audit regressions (2026-08-01) — docs/AUDIT.md
#
# Every test here pins a hole that was open on `main` at audit time. They assert
# BEHAVIOUR (a status code, a stored row), not the constant that implements it,
# so retuning a limit cannot silently delete the guarantee.
# ===========================================================================

# --- A: the per-IP key must not be client-supplied -------------------------

def test_rate_limit_key_ignores_a_forged_x_forwarded_for(golden_sf, client_for):
    """Rotating X-Forwarded-For must NOT hand the caller a fresh bucket.

    `_client_ip` read `split(",")[0]` — the leftmost entry, which is entirely
    whatever the client typed. One header per request bypassed every per-IP limit
    on the surface. A proxy APPENDS the peer it saw, so only the rightmost entry
    is evidence.
    """
    client = client_for(golden_sf)
    ids, _ = _sample_squad()
    # Same real peer (the rightmost hop), five different forged prefixes.
    for i, cap in enumerate(ids[:5]):
        r = client.post("/api/brief", json=_draft_body(ids, cap),
                        headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"})
        assert r.status_code == 200, r.text
    blocked = client.post("/api/brief", json=_draft_body(ids, ids[5]),
                          headers={"X-Forwarded-For": "10.0.0.99, 203.0.113.7"})
    assert blocked.status_code == 429


def test_distinct_real_peers_still_get_their_own_budget(golden_sf, client_for):
    """The other half: the fix must not collapse every caller into one bucket."""
    client = client_for(golden_sf)
    ids, _ = _sample_squad()
    for cap in ids[:5]:
        assert client.post("/api/brief", json=_draft_body(ids, cap),
                           headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 200
    other = client.post("/api/brief", json=_draft_body(ids, ids[5]),
                        headers={"X-Forwarded-For": "198.51.100.4"})
    assert other.status_code == 200, other.text


# --- A: unbounded table growth / mail volume -------------------------------

def test_replayed_brief_posts_stop_creating_draft_rows(golden_sf, client_for):
    """A cache HIT still writes a draft_squads row, so generations cannot bound it.

    The generation limiter never fires on a replayed identical body (by design —
    "cache hits don't count"), which left an unauthenticated caller able to insert
    rows forever. The request budget is what closes that.
    """
    client = client_for(golden_sf)
    ids, cap = _sample_squad()
    body = _draft_body(ids, cap)

    codes = [client.post("/api/brief", json=body).status_code for _ in range(40)]
    assert 429 in codes, "replaying one body was never refused"
    with golden_sf() as s:
        rows = s.scalar(select(func.count()).select_from(DraftSquad))
    assert rows == codes.count(200), "a refused request still wrote a draft row"
    assert rows < 40


def test_signup_is_rate_limited_so_the_confirm_mail_cannot_be_weaponised(
        golden_sf, client_for, monkeypatch):
    """POST /api/signup mails an address the caller never proved they own.

    Double opt-in shrank the blast radius — the worst case is now one expiring
    confirmation rather than a permanent list entry and a weekly brief — but one
    unwanted email is still one too many at volume, so the cap stays.
    """
    sent = []
    monkeypatch.setattr(mail, "send_confirm_link",
                        lambda email, token, **kw: sent.append(email) or True)
    client = client_for(golden_sf)

    codes = [client.post("/api/signup", json={"email": "victim@example.com"}).status_code
             for _ in range(40)]
    assert 429 in codes
    assert len(sent) == codes.count(200) < 40


# --- A: input bounds at the trust boundary ---------------------------------

@pytest.mark.parametrize("body", [
    # A Python int is unbounded; a Postgres `integer` column is not. Each of these
    # reached the DB and raised a DataError — a 500 on chosen input — before bounds.
    {"draft": {"player_ids": [10 ** 40] * 15, "captain_id": 1}},
    {"draft": {"player_ids": [1] * 15, "captain_id": 10 ** 40}},
    {"draft": {"player_ids": [-1] * 15}},
    {"team_id": 10 ** 40},
])
def test_brief_rejects_out_of_range_ids(golden_sf, client_for, body):
    assert client_for(golden_sf).post("/api/brief", json=body).status_code == 422


@pytest.mark.parametrize("body", [
    {"email": "a@b.com", "source": "x" * 65},     # unbounded Text on a public route
    {"email": "a@b.com", "team_id": 10 ** 40},
    {"email": "a@b.com", "team_id": -5},
])
def test_signup_rejects_out_of_range_fields(golden_sf, client_for, body):
    assert client_for(golden_sf).post("/api/signup", json=body).status_code == 422


@pytest.mark.parametrize("path", ["/api/brief/1/999", "/api/preview/999",
                                  "/api/brief/99999999999999999999/1"])
def test_path_params_are_bounded(golden_sf, client_for, path):
    assert client_for(golden_sf).get(path).status_code == 422


# --- A/E: re-signup must not destroy what is already stored ----------------

def test_resignup_fills_blanks_and_never_overwrites(golden_sf, client_for, monkeypatch):
    """`session.merge` wrote every column it was handed, including the NULLs.

    POST /api/brief with an email calls signup with `team_id=None, source="draft"`,
    so a homepage signup that carried a team id lost it the moment the same person
    used the draft modal. `tier` was rewritten to 'free' unconditionally, which in
    Stage B demotes a paying subscriber.
    """
    monkeypatch.setattr(mail, "send_welcome", lambda email, **kw: True)
    client = client_for(golden_sf)

    client.post("/api/signup", json={"email": "fan@example.com", "team_id": 4242,
                                     "source": "homepage"})
    with golden_sf() as s:                       # billing owns tier, not signup
        row = s.get(EmailSignup, "fan@example.com")
        row.tier = "paid"
        s.commit()

    # The draft-modal path: same address, no team id, a different source.
    client.post("/api/signup", json={"email": "fan@example.com", "source": "draft"})

    with golden_sf() as s:
        row = s.get(EmailSignup, "fan@example.com")
        assert row.fpl_team_id == 4242           # not NULLed
        assert row.source == "homepage"          # not overwritten
        assert row.tier == "paid"                # not demoted


def test_signup_still_records_a_team_id_that_arrives_later(golden_sf, client_for,
                                                           monkeypatch):
    """Fill-blanks must still FILL — an empty column takes the new value."""
    monkeypatch.setattr(mail, "send_welcome", lambda email, **kw: True)
    client = client_for(golden_sf)
    client.post("/api/signup", json={"email": "late@example.com"})
    client.post("/api/signup", json={"email": "late@example.com", "team_id": 77,
                                     "source": "draft"})
    with golden_sf() as s:
        row = s.get(EmailSignup, "late@example.com")
        assert row.fpl_team_id == 77 and row.source == "draft"


# --- B: the admin surface ---------------------------------------------------

def _fill_path(path: str) -> str:
    """Substitute a valid value for every `{param}` in a route template."""
    out = path.replace("{draft_id}", "00000000-0000-0000-0000-000000000000")
    out = out.replace("{team_short}", "ARS")
    while "{" in out:                            # every remaining param is numeric
        head, _, rest = out.partition("{")
        _, _, tail = rest.partition("}")
        out = head + "1" + tail
    return out


def test_every_admin_route_rejects_an_anonymous_caller(golden_sf, client_for,
                                                       monkeypatch):
    """Walked from the live router, not from a hand-kept list.

    The existing coverage is a literal list of paths, which is only ever as complete
    as whoever last added a route remembered to make it. One unguarded
    /api/admin/* route is the entire operator surface, so the assertion has to be
    derived from the thing that actually serves traffic.
    """
    from api.routes import router as _router      # the router `app` includes

    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    admin_limiter.limit = 10_000                 # this test IS a burst of failures
    try:
        checked = []
        for route in _router.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/admin"):
                continue
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                r = client.request(method, _fill_path(path), json={})
                assert r.status_code == 401, (
                    f"{method} {path} answered {r.status_code} with no token")
                checked.append(f"{method} {path}")

        # A guard on the guard: if the walk matches nothing, every assertion above
        # passes vacuously. Raised 15 -> 18 when Stage B added /admin/preview,
        # /admin/send and /admin/subscriptions (2026-08-01). Raise it whenever the
        # surface grows — that is the point of the number.
        assert len(checked) >= 18, checked
    finally:
        admin_limiter.limit = settings.admin_fail_limit


def test_admin_rejects_a_token_that_is_a_prefix_of_the_real_one(golden_sf, client_for,
                                                               monkeypatch):
    """Constant-time compare, sanity side: no partial credit for a prefix."""
    monkeypatch.setattr(settings, "admin_token", "supersecrettoken")
    client = client_for(golden_sf)
    for wrong in ("supersecrettoke", "supersecrettokenX", "", "s"):
        assert client.get("/api/admin/overview",
                          headers={"Authorization": f"Bearer {wrong}"}).status_code == 401


def test_admin_auth_uses_a_constant_time_compare():
    """`!=` on str short-circuits at the first differing byte, leaking a prefix
    length through timing. Asserted structurally: a timing measurement is flaky by
    nature, and what must not regress is the call, not a duration."""
    import inspect as _inspect

    from api import routes as _routes

    src = _inspect.getsource(_routes._require_admin)
    assert "compare_digest" in src
    assert "token != settings.admin_token" not in src


def test_repeated_admin_failures_are_throttled(golden_sf, client_for, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    client = client_for(golden_sf)
    admin_limiter.limit = 5
    try:
        codes = [client.get("/api/admin/overview",
                            headers={"Authorization": "Bearer wrong"}).status_code
                 for _ in range(8)]
        assert codes[:5] == [401] * 5
        assert codes[-1] == 429
        # A correct token is never recorded, so the operator is never locked out.
        assert client.get("/api/admin/overview", headers=_AUTH).status_code == 200
    finally:
        admin_limiter.limit = settings.admin_fail_limit


# --- A: the schema is not a public map -------------------------------------

def test_openapi_and_docs_are_closed_by_default(golden_sf, client_for):
    client = client_for(golden_sf)
    assert settings.docs_enabled is False
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --- F: connections survive Render sleep + Neon autosuspend ----------------

def test_postgres_engine_pre_pings_and_recycles(monkeypatch):
    """Without these, the first request after any quiet period 500s.

    Render free sleeps after ~15 min idle and Neon free autosuspends its compute;
    both drop TCP connections the pool still believes are live. Asserted by
    capturing the kwargs because a real Postgres engine needs psycopg, which is an
    optional extra and absent in CI.
    """
    from fpl import db as _db

    seen = {}

    def _spy(url, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(_db, "create_engine", _spy)
    _db.make_engine("postgresql://u:p@h/dbname")
    assert seen.get("pool_pre_ping") is True
    assert seen.get("pool_recycle", 0) > 0

    seen.clear()
    monkeypatch.setattr(_db, "event", type("E", (), {"listens_for": lambda *a: (lambda f: f)}))
    _db.make_engine("sqlite://")
    # SQLite is a local file with no idle timeout; pinging it is pure overhead.
    assert "pool_pre_ping" not in seen


# --- G: the cache lookup is indexed ----------------------------------------

def test_briefs_input_hash_is_indexed(golden_sf):
    """`WHERE input_hash = ?` runs on every brief request; it was a table scan."""
    from sqlalchemy import inspect as sa_inspect

    with golden_sf() as s:
        names = {ix["name"] for ix in sa_inspect(s.get_bind()).get_indexes("briefs")}
    assert "ix_briefs_input_hash" in names
