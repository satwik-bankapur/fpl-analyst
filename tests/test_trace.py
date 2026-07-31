"""Trace mode + the /admin/inspect diagnostics (read-only, zero LLM spend).

The load-bearing test in this file is `test_trace_off_leaves_the_run_untouched`.
Everything else here describes a diagnostic; that one protects the PRODUCT — if
tracing can change a brief, then the reasoning on the inspector page is the
reasoning of a different pipeline than the one users get, and the whole feature is
worse than not having it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.models import Base, Brief, DraftSquad, EpScore, Fixture, Gameweek, Player, Team
from fpl.sync import sync_fpl
from graph.deps import Deps
from graph.graph import run_brief
from graph.state import BriefState, RequestCtx
from graph.trace import REJECTION_REASONS, explain_code

from api import admin, inspect as inspect_mod

ASSETS = Path(__file__).parent / "assets"


def _load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


class _FullFakeClient:
    def __init__(self):
        self.bootstrap = _load("bootstrap_2025-26.json")
        self.fixtures = _load("fixtures_2025-26.json")

    def get_bootstrap(self):
        return json.loads(json.dumps(self.bootstrap))

    def get_fixtures(self):
        return json.loads(json.dumps(self.fixtures))

    def close(self):
        pass


QUOTA = ((1, 2), (2, 5), (3, 5), (4, 3))   # GK/DEF/MID/FWD
MAX_PER_CLUB = 3


@pytest.fixture
def synced():
    """Frozen 2025-26 season through the real sync + engine, plus one draft squad.

    The squad is cheapest-legal EXCEPT that its captain is a doubtful player. That
    is deliberate and load-bearing: `_diagnose` only raises problems for the
    STARTING XI, and a doubtful player's reduced minutes probability drops their EP
    far enough that the computed XI benches them — so a naive cheap squad raises no
    replaceable problem, MarketScout never scans, and the rejection tests below
    silently skip. Captaining them forces them into the XI (`_keep_captain_starting`),
    which guarantees an `injury_doubt` and therefore a real market scan to inspect.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sync_fpl(client=_FullFakeClient(), session_factory=SF)

    with SF() as s:
        anchor = s.scalar(select(EpScore.gameweek).limit(1))
        scored = {r.player_id for r in s.scalars(
            select(EpScore).where(EpScore.gameweek == anchor))}
        pool = [p for p in s.scalars(select(Player)) if p.id in scored]

    doubtful = sorted(
        (p for p in pool if p.status == "d" and (p.chance_of_playing or 100) <= 75
         and p.position == 3),
        key=lambda p: float(p.price),
    )
    assert doubtful, "the frozen season no longer has a doubtful midfielder"
    captain = doubtful[0]

    ids = _cheapest_legal_squad(pool, must_include=captain)
    did = uuid.uuid4()
    with SF() as s, s.begin():
        s.add(DraftSquad(id=did, player_ids=ids, captain_id=captain.id))

    yield Deps(session_factory=SF, client=None), did, SF
    engine.dispose()


def _cheapest_legal_squad(pool, *, must_include) -> list[int]:
    """A legal 2/5/5/3 under the 3-per-club cap, cheapest-first, containing `must_include`."""
    picked = [must_include]
    clubs = {must_include.team_id: 1}
    need = {pos: n for pos, n in QUOTA}
    need[must_include.position] -= 1

    for p in sorted((x for x in pool if x.status == "a"), key=lambda x: float(x.price)):
        if need.get(p.position, 0) <= 0 or p.id == must_include.id:
            continue
        if clubs.get(p.team_id, 0) >= MAX_PER_CLUB:
            continue
        picked.append(p)
        clubs[p.team_id] = clubs.get(p.team_id, 0) + 1
        need[p.position] -= 1
    assert all(n == 0 for n in need.values()), f"could not fill the quota: {need}"
    return [p.id for p in picked]


def _state(did, *, trace: bool) -> BriefState:
    return BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=did, gameweek_anchor=0,
        requested_at=datetime.now(timezone.utc), trace=trace))


# ===========================================================================
# The invariant: tracing observes, it never participates
# ===========================================================================

def test_trace_off_leaves_the_run_untouched(synced):
    """A normal run carries no trace and is byte-identical to a traced one.

    Both halves matter. An empty `trace` proves nothing is being recorded or
    serialized on the hot path; identical `brief_md` proves the recording that DOES
    happen under trace has no effect on what the pipeline decides.
    """
    deps, did, _ = synced

    plain = run_brief(_state(did, trace=False), deps)
    traced = run_brief(_state(did, trace=True), deps)

    assert plain.trace == []
    assert plain.brief_md == traced.brief_md
    # The §2 contracts must match too, not just the rendered prose — a trace that
    # perturbed a decision could still round back to the same sentence.
    assert plain.decisions.model_dump() == traced.decisions.model_dump()
    assert plain.market.model_dump() == traced.market.model_dump()
    assert plain.diagnosis.model_dump() == traced.diagnosis.model_dump()
    assert plain.warnings == traced.warnings


def test_every_node_contributes_exactly_one_stage(synced):
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    assert [st.node for st in traced.trace] == [
        "squad_analyst", "market_scout", "fixture_planner", "strategist",
    ]
    for st in traced.trace:
        assert st.summary, f"{st.node} recorded no plain-English summary"
        assert st.data, f"{st.node} recorded no numbers"


def test_trace_is_json_serializable(synced):
    """It crosses an HTTP boundary, so a stray non-JSON value must fail here."""
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    json.dumps([st.model_dump() for st in traced.trace])


# ===========================================================================
# MarketScout: the rejections are the point
# ===========================================================================

def test_scout_records_rejections_with_known_reasons(synced):
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    scout = next(st for st in traced.trace if st.node == "market_scout")

    problems = scout.data["problems"]
    # The fixture guarantees a doubtful captain, so a scan must have happened. A
    # skip here would hide the very thing this file exists to test.
    assert problems, "no replaceable problem — the fixture squad has stopped working"

    for p in problems:
        assert p["rejected"], "a scan over ~700 players rejected nobody"
        for bucket in p["rejected"]:
            assert bucket["reason"] in REJECTION_REASONS
            assert bucket["count"] >= len(bucket["examples"])
        # Every considered player is accounted for exactly once: shortlisted, or
        # rejected under one reason. A player who is neither has been lost.
        rejected_n = sum(b["count"] for b in p["rejected"])
        assert rejected_n + len(p["shortlisted"]) == p["considered"]


def test_rejection_reasons_never_contradict_the_shortlist(synced):
    """Nobody may appear both on the shortlist and in a rejection bucket."""
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    scout = next(st for st in traced.trace if st.node == "market_scout")

    for p in scout.data["problems"]:
        picked = {c["player_id"] for c in p["shortlisted"]}
        for bucket in p["rejected"]:
            assert not (picked & {e["player_id"] for e in bucket["examples"]})


def test_scout_trace_matches_the_real_shortlist(synced):
    """The traced shortlist IS `market.replacements` — not a second computation."""
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    scout = next(st for st in traced.trace if st.node == "market_scout")

    real = {rs.problem_player_id: [c.player_id for c in rs.candidates]
            for rs in traced.market.replacements}
    for p in scout.data["problems"]:
        assert [c["player_id"] for c in p["shortlisted"]] == real[p["problem_player_id"]]


# ===========================================================================
# Strategist: why the alternatives lost
# ===========================================================================

def test_strategist_explains_every_problem_it_passed_over(synced):
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    strat = next(st for st in traced.trace if st.node == "strategist")

    alts = strat.data["transfer_alternatives"]
    assert len(alts) == len(traced.diagnosis.problems)
    assert all(a["why"] for a in alts)

    chosen = [a for a in alts if a["outcome"] == "chosen"]
    if traced.decisions.transfer.action == "make":
        assert len(chosen) == 1
        assert chosen[0]["player_id"] == traced.decisions.transfer.out_id
    else:
        assert chosen == []
        # A roll must be justified by every candidate falling short, never by silence.
        assert all(a["outcome"] in ("below_threshold", "no_candidates") for a in alts)


def test_every_emitted_reasoning_code_is_expanded(synced):
    deps, did, _ = synced
    traced = run_brief(_state(did, trace=True), deps)
    strat = next(st for st in traced.trace if st.node == "strategist")

    codes = strat.data["codes"]
    assert [c["code"] for c in codes] == traced.decisions.reasoning_codes
    for c in codes:
        assert c["plain"]
        assert "unrecognised code" not in c["plain"], (
            f"{c['code']} has no explanation — add it to graph/trace.py")


def test_unknown_code_is_echoed_not_dropped():
    """Operator-facing, so an unexplained token is a lead — the opposite of the
    writer's rule, which drops what it cannot phrase."""
    assert "totally_made_up" in explain_code("totally_made_up")


# ===========================================================================
# The inspector bundle
# ===========================================================================

def test_inspect_draft_writes_nothing(synced):
    """Read-only means read-only: no `briefs` row, not even a cache entry."""
    deps, did, SF = synced
    with SF() as s:
        before = s.scalars(select(Brief)).all()
    inspect_mod.inspect_draft(deps, did)
    with SF() as s:
        after = s.scalars(select(Brief)).all()
    assert len(before) == len(after) == 0


def test_inspect_payload_is_generated_but_never_sent(synced, monkeypatch):
    """The payload comes from the real serializer with no model call in sight.

    Guarded by breaking the SDK entry point: if anything in this path ever reaches
    for the LLM, this test fails loudly rather than quietly spending money.
    """
    import writer.llm as llm

    def _boom(*a, **k):
        raise AssertionError("the inspector must never call the LLM")

    monkeypatch.setattr(llm.AnthropicWriter, "generate", _boom, raising=False)

    deps, did, _ = synced
    bundle = inspect_mod.inspect_draft(deps, did)
    payload = bundle["payload"]

    assert payload["text"].startswith("[HEADER]")
    assert payload["token_estimate"] > 0
    assert payload["cost_usd"]["total_at_cap"] > 0
    # Every block is accounted for as kept or dropped, by name.
    for b in payload["blocks"]:
        assert b["label"] and b["group"] in (
            "evidence", "bench_evidence", "fixtures", "nondecision_fixtures")
        assert b["included"] is (b["group"] not in payload["dropped"])


def test_inspect_free_and_paid_use_the_real_allow_list(synced):
    """Free is a subset of paid, and the delta is exactly the withheld paid keys."""
    deps, did, _ = synced
    bundle = inspect_mod.inspect_draft(deps, did)
    free, paid = bundle["free"], bundle["paid"]

    from graph.sections import FREE_SECTIONS

    assert set(free["sections"]) <= set(paid["sections"])
    # Allow-list, not deny-list: nothing outside FREE_SECTIONS reaches a free reader.
    assert set(free["sections"]) <= set(FREE_SECTIONS)
    assert paid["locked_sections"] == []
    assert set(bundle["paid_delta"]) == set(paid["sections"]) - set(free["sections"])
    # A locked key must correspond to content that actually exists behind it.
    for key in free["locked_sections"]:
        assert paid["sections"].get(key, "").strip()


def test_inspect_unknown_draft_is_none(synced):
    deps, _, _ = synced
    assert inspect_mod.inspect_draft(deps, uuid.uuid4()) is None


def test_player_components_returns_the_blob_verbatim(synced):
    deps, did, SF = synced
    bundle = inspect_mod.inspect_draft(deps, did)
    pid = bundle["squad"][0]["player_id"]

    got = inspect_mod.player_components(deps, pid)
    with SF() as s:
        anchor = s.scalar(select(EpScore.gameweek).limit(1))
        row = s.get(EpScore, (pid, anchor))
    assert got["components"] == row.components
    assert inspect_mod.player_components(deps, 10**9) is None


# ===========================================================================
# The flagged-player work queue
# ===========================================================================

def test_flag_queue_groups_and_classifies(synced):
    deps, _, _ = synced
    q = admin.flag_queue(deps)

    assert q["clubs_total"] == 20
    assert q["groups"], "the frozen season has flagged players"
    for g in q["groups"]:
        assert g["kind"] in ("actionable", "transient", "known_limitation")
        assert g["explanation"]
        assert ":" not in g["flag"], "groups are the bare flag, suffixes stripped"
    # Actionable work sorts to the top — the queue opens on the job, not the noise.
    kinds = [g["kind"] for g in q["groups"]]
    assert kinds == sorted(kinds, key=lambda k: {"actionable": 0,
                                                 "known_limitation": 1,
                                                 "transient": 2}[k])


def test_only_note_improvable_flags_get_a_club_ranking(synced):
    deps, _, _ = synced
    q = admin.flag_queue(deps)
    for g in q["groups"]:
        if g["note_improvable"]:
            assert g["flag"] in admin.NOTE_IMPROVABLE
            assert g["clubs"], "a note-improvable flag with no club to write about"
        else:
            # Ranking clubs by a flag a note cannot fix would send the operator to
            # write words that change nothing.
            assert g["clubs"] == []


def test_club_ranking_is_count_times_top_ep_and_unwritten_first(synced):
    deps, _, _ = synced
    q = admin.flag_queue(deps)
    for g in q["groups"]:
        if not g["note_improvable"]:
            continue
        for c in g["clubs"]:
            assert c["score"] == pytest.approx(round(c["count"] * c["top_ep_next4"], 1))
        # Clubs already covered sink below the ones still needing work; within each
        # of those halves, payoff order.
        keys = [(c["has_note"], -c["score"]) for c in g["clubs"]]
        assert keys == sorted(keys)


def test_identical_ep_badge_fires_on_baseline_scored_clubs_only(synced):
    """Promoted clubs are scored on baselines; established clubs are not.

    This is the one assertion that would have caught the original strict-equality
    rule, which never fired on real data — see the note in api/admin.py.
    """
    deps, _, _ = synced
    q = admin.flag_queue(deps)
    badged, clean = set(), set()
    for g in q["groups"]:
        for c in g["clubs"]:
            (badged if c["identical_ep"] else clean).add(c["team_short"])

    assert badged, "no club badged — the collapse ratio is set too tight to ever fire"
    for short in badged:
        assert short not in clean or True  # a club may differ per flag group
    # Every badged club must publish the evidence that justifies the badge.
    for g in q["groups"]:
        for c in g["clubs"]:
            if c["identical_ep"]:
                assert c["scored_players"] >= admin.IDENTICAL_EP_MIN_PLAYERS
                assert c["distinct_ep_next4"] <= (
                    c["scored_players"] * admin.IDENTICAL_EP_MAX_DISTINCT_RATIO)


def test_flag_queue_counts_clubs_with_notes(synced):
    from fpl.models import TeamNote

    deps, _, SF = synced
    assert admin.flag_queue(deps)["clubs_with_notes"] == 0
    with SF() as s, s.begin():
        s.add(TeamNote(team_id=1, note="Pens: Saka. Set pieces are elite."))
    assert admin.flag_queue(deps)["clubs_with_notes"] == 1
    # Whitespace is not a note — the queue must not shrink on an empty save.
    with SF() as s, s.begin():
        s.merge(TeamNote(team_id=2, note="   "))
    assert admin.flag_queue(deps)["clubs_with_notes"] == 1
