"""Layer 4 writer tests — serializer (§2), validator (§7), writer flow.

Zero LLM spend: the writer flow injects a FakeLLM. The validator is exercised on
synthetic bad outputs (invented name / out-of-payload number / missing header /
over-length), each of which must trigger retry-then-stub-fallback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.models import Base, Brief, DraftSquad, EpScore, Fixture, Gameweek, Player, Team
from graph import BriefState, run_brief
from graph.deps import Deps
from graph.state import RequestCtx
from writer.llm import generate_brief
from writer.serializer import estimate_tokens, serialize_payload
from writer.validator import validate_brief


# --- synthetic DB + a completed BriefState (PRESEASON draft path) -----------

def _T(tid, short):
    return dict(id=tid, name=short, short_name=short,
                strength_overall_home=1200, strength_overall_away=1200,
                strength_attack_home=1200, strength_attack_away=1200,
                strength_defence_home=1200, strength_defence_away=1200)


def _P(pid, team_id, position=3, price=6.0, status="a", ownership=10.0, web=None, news=""):
    return dict(id=pid, web_name=web or f"P{pid}", full_name=web or f"P{pid}",
                team_id=team_id, position=position, price=price, total_points=100,
                form=5.0, ppg=5.0, minutes=1800, status=status, chance_of_playing=None,
                news=news, ownership_pct=ownership, xg=5, xa=3, xgi=8, xgc=10,
                transfers_in_event=0, transfers_out_event=0)


def _EP(pid, ep=5.0, ep4=20.0, flags=None):
    return dict(player_id=pid, gameweek=1, ep=ep, ep_next4=ep4,
                components=dict(config_version="v1.4", flags=flags or [],
                                minutes_prob=1.0, data_basis="current", base=5.0, attack=3.0))


@pytest.fixture
def brief_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    teams = [_T(1, "ARS"), _T(2, "BUR"), _T(3, "CHE")]
    # 15 squad players + one flagged (injured) starter to force evidence + a problem
    players = [_P(p, (p % 3) + 1, position=((p % 4) + 1)) for p in range(101, 116)]
    players[3] = _P(104, 2, position=3, status="i", web="Hurt", news="knee")   # starter, unavailable
    # P115 is a bench slot (draft order 15) with a low ep -> dead_bench_spot problem,
    # so it gets a bench-slot evidence block (the first thing truncation drops).
    ep_rows = [_EP(p, ep4=(2.0 if p == 115 else 20.0)) for p in range(101, 116)]
    fixtures = [dict(id=i, gameweek=g, home_team=1, away_team=2, home_difficulty=2,
                     away_difficulty=3, kickoff=None, finished=False, home_score=None,
                     away_score=None) for i, g in enumerate(range(1, 7), 1)]
    with SF() as s, s.begin():
        for t in teams: s.add(Team(**t))
        for i in range(1, 7): s.add(Gameweek(id=i, name=f"GW{i}", deadline=datetime(2026, 8, 1, tzinfo=timezone.utc), finished=False, is_current=False, is_next=i == 1))
        for p in players: s.add(Player(**p))
        for f in fixtures: s.add(Fixture(**f))
        for e in ep_rows: s.add(EpScore(**e))
        did = uuid.uuid4()
        s.add(DraftSquad(id=did, player_ids=list(range(101, 116)), captain_id=101))
    deps = Deps(session_factory=SF)
    state = run_brief(BriefState(request=RequestCtx(
        mode="PRESEASON", draft_id=did, gameweek_anchor=0,
        requested_at=datetime.now(timezone.utc))), deps)
    yield SF, deps, state
    engine.dispose()


# --- §2 serializer ----------------------------------------------------------

def test_serializer_only_decision_players_get_evidence(brief_env):
    SF, deps, state = brief_env
    payload = serialize_payload(state, deps)
    # captain P101 is a decision player -> evidence block present
    assert "\n[EVIDENCE]" in payload.text
    assert "- P101 " in payload.text
    # the injured starter (a problem) gets an evidence block too
    assert "Hurt" in payload.text
    assert payload.token_estimate <= 3500


def test_serializer_truncation_drops_bench_evidence_first(brief_env):
    SF, deps, state = brief_env
    tiny = serialize_payload(state, deps, max_tokens=1)  # force the drop path
    # bench evidence is dropped before non-decision fixture rows (§2 order)
    assert tiny.dropped and tiny.dropped[0] == "bench_evidence"


# --- §7 validator (pure, no LLM) -------------------------------------------

HEADERS = "## The verdict\nverdict text.\n## This week's transfer\nmake it.\n## Captaincy\ncap.\n"
FILLER = ("the plan holds steady and you should stay patient this week " * 60)
GOOD = HEADERS + FILLER   # headers in order, no names, no numbers, ~360 words
PAYLOAD = "[SQUAD] Haaland Salah bank £1.5m ep 9.0 pts"
KNOWN = {"Haaland", "Salah", "Isak"}


def test_validator_passes_clean_brief():
    assert validate_brief(GOOD, PAYLOAD, KNOWN).ok


def test_validator_flags_invented_name():
    bad = GOOD.replace("this week", "start Isak this week", 1)  # Isak not in payload
    r = validate_brief(bad, PAYLOAD, KNOWN)
    assert not r.ok and any(f.startswith("invented_name:Isak") for f in r.failures)


def test_validator_flags_out_of_payload_number():
    bad = GOOD.replace("this week", "worth £9.9m this week", 1)  # £9.9m not in payload
    r = validate_brief(bad, PAYLOAD, KNOWN)
    assert not r.ok and any("number_not_in_payload:£9.9" in f for f in r.failures)


def test_validator_flags_missing_header():
    bad = GOOD.replace("## Captaincy", "## Something Else")
    r = validate_brief(bad, PAYLOAD, KNOWN)
    assert not r.ok and any("missing_header:## Captaincy" in f for f in r.failures)


def test_validator_flags_over_length():
    bad = HEADERS + ("word " * 800)
    r = validate_brief(bad, PAYLOAD, KNOWN)
    assert not r.ok and any(f.startswith("length:") for f in r.failures)


# --- writer flow with a mocked LLM (zero spend) ----------------------------

class _FakeLLM:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        text = self._outputs.pop(0)
        return text, {"input_tokens": 100, "output_tokens": 200}


def test_writer_good_output_validates_and_persists(brief_env):
    SF, deps, state = brief_env
    llm = _FakeLLM([GOOD])
    res = generate_brief(state, deps, llm=llm, persist=True, input_hash="hash-abc")
    assert res.validated and not res.fell_back
    assert res.model_label.endswith("::v0.1") and res.tokens_out == 200
    with SF() as s:
        assert s.scalar(select(func.count()).select_from(Brief)) == 1
        row = s.scalars(select(Brief)).first()
        assert row.input_hash == "hash-abc" and row.tokens_in == 100


def test_writer_bad_output_retries_then_stub_fallback(brief_env):
    SF, deps, state = brief_env
    bad = "## The verdict\nno other headers here " + ("word " * 5)  # missing headers + too short
    llm = _FakeLLM([bad, bad])                                       # both attempts fail
    res = generate_brief(state, deps, llm=llm, persist=True, input_hash="hash-xyz")
    assert llm.calls == 2                                            # one retry, then give up
    assert res.fell_back and not res.validated
    assert res.model_label.startswith("stub-template-v1::fallback")
    assert res.brief_md.startswith("# FPL Brief")                    # the Layer 3 stub output
    with SF() as s:  # fallback still persisted (with stub label, 0 tokens)
        assert s.scalar(select(func.count()).select_from(Brief)) == 1
