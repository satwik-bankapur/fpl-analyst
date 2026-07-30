"""`fpl backfill-archive` — the missed-rollover recovery path (fpl/backfill.py).

The bug this guards against is not a crash, it is plausible nonsense: with an
empty `players_archive`, PRESEASON scoring resolves EVERY player through the
(position, price-band) baseline, every ep collapses to near-identical, and
goalkeepers rise to the top of the table on clean-sheet + save points alone.

So the tests here are not just "rows landed". The load-bearing assertion is
`test_preseason_eps_are_differentiated_after_backfill`: after a backfill, the
top-20 by ep_next4 must actually SPREAD. If someone breaks the archive path
again, that is the test that fails.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from engine.scoring import score_all
from fpl.backfill import BackfillRefused, backfill_archive
from fpl.models import PlayerArchive
from fpl.parsers import parse_fixture, parse_gameweek, parse_player, parse_team
from fpl.sync import _load_archive

ASSETS = Path(__file__).parent / "assets"


def load_asset(name: str):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


SEASON = "2025-26"
SOURCE = ASSETS / "bootstrap_2025-26.json"

# Ids the synthetic new-season snapshot uses, offset clear of the real 1..841.
# A real season reset reassigns element ids from scratch, which is exactly why
# the archive has to be matched on (name, position) rather than on id.
_NEW_SEASON_ID_OFFSET = 10_000


@pytest.fixture(scope="module")
def frozen() -> dict:
    return load_asset("bootstrap_2025-26.json")


@pytest.fixture(scope="module")
def frozen_fixtures() -> list:
    return load_asset("fixtures_2025-26.json")


def _new_season_bootstrap(frozen: dict) -> dict:
    """Turn the finished 2025-26 payload into a plausible fresh-season snapshot.

    This is what the FPL API serves in July: same players, NEW element ids, and
    every season-to-date counter back to zero. Nothing here is scored data — the
    point is that the only usable rates live in the archive.
    """
    boot = copy.deepcopy(frozen)
    for el in boot["elements"]:
        el["id"] += _NEW_SEASON_ID_OFFSET
        el["total_points"] = 0
        el["minutes"] = 0
        el["form"] = "0.0"
        el["points_per_game"] = "0.0"
        for k in ("expected_goals", "expected_assists",
                  "expected_goal_involvements", "expected_goals_conceded"):
            el[k] = "0.00"
    for ev in boot["events"]:
        ev["finished"] = False
        ev["is_current"] = False
        ev["is_next"] = ev["id"] == 1
    return boot


def _brand_new_player(template: dict, pid: int, name: str, cost: int) -> dict:
    """A signing with no history under any name — the honesty-flag path."""
    el = copy.deepcopy(template)
    el.update(id=pid, first_name=name, second_name="Newarrival", web_name=name,
              now_cost=cost, total_points=0, minutes=0,
              form="0.0", points_per_game="0.0")
    return el


def _score_preseason(boot: dict, frozen_fixtures: list, archive: list[dict]):
    players = [parse_player(el) for el in boot["elements"]]
    return players, score_all(
        players,
        [parse_team(t) for t in boot["teams"]],
        [parse_fixture(f) for f in frozen_fixtures],
        [parse_gameweek(e) for e in boot["events"]],
        mode="PRESEASON", anchor=1, archive=archive,
    )


# --- the command itself ----------------------------------------------------

def test_backfill_populates_the_expected_row_count(session_factory, frozen):
    written = backfill_archive(SOURCE, SEASON, session_factory=session_factory)

    assert written == len(frozen["elements"]) == 841
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PlayerArchive)) == 841
        assert s.scalar(
            select(func.count()).select_from(PlayerArchive)
            .where(PlayerArchive.season != SEASON)
        ) == 0


def test_backfill_carries_the_fields_the_engine_reads(session_factory):
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        raya = s.get(PlayerArchive, {"season": SEASON, "id": 1})
    assert raya.full_name == "David Raya Martín"
    assert raya.team_id == 1                 # element.team, not team_code (=3)
    assert raya.position == 1
    assert float(raya.price) == 6.2
    assert float(raya.ppg) == 4.4
    assert raya.minutes == 3330
    assert raya.season == SEASON


def test_rerunning_replaces_rather_than_duplicates(session_factory):
    first = backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    second = backfill_archive(SOURCE, SEASON, force=True, session_factory=session_factory)

    assert first == second
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PlayerArchive)) == second


def test_refuses_to_overwrite_an_existing_season_without_force(session_factory):
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)

    with pytest.raises(BackfillRefused):
        backfill_archive(SOURCE, SEASON, session_factory=session_factory)

    # ...and the refusal left the rows intact.
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PlayerArchive)) == 841


def test_a_different_season_coexists(session_factory):
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    backfill_archive(SOURCE, "2024-25", session_factory=session_factory)

    with session_factory() as s:
        seasons = set(s.scalars(select(PlayerArchive.season)).all())
    assert seasons == {SEASON, "2024-25"}


def test_a_non_bootstrap_payload_is_rejected_before_any_write(session_factory, tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"nope": []}), encoding="utf-8")

    with pytest.raises(Exception):
        backfill_archive(junk, SEASON, session_factory=session_factory)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PlayerArchive)) == 0


# --- what the backfill is FOR ---------------------------------------------

def test_preseason_eps_are_differentiated_after_backfill(
        session_factory, frozen, frozen_fixtures):
    """The whole point. Without an archive every ep collapses onto the baseline."""
    boot = _new_season_bootstrap(frozen)

    # BEFORE: empty archive -> the baseline path for everyone.
    _, before = _score_preseason(boot, frozen_fixtures, archive=None)
    top20_before = sorted((r.ep_next4 for r in before), reverse=True)[:20]
    spread_before = top20_before[0] - top20_before[-1]

    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        archive = _load_archive(s)
    assert archive and len(archive) == 841

    # AFTER: real per-player rates -> a real ranking.
    _, after = _score_preseason(boot, frozen_fixtures, archive=archive)
    top20_after = sorted((r.ep_next4 for r in after), reverse=True)[:20]
    spread_after = top20_after[0] - top20_after[-1]

    assert spread_after > 5 * spread_before
    assert spread_after > 2.0, f"top-20 ep_next4 barely moves: {top20_after}"
    assert len(set(round(v, 2) for v in top20_after)) >= 15, "top-20 is nearly all ties"


def test_top_of_the_table_is_not_all_goalkeepers(
        session_factory, frozen, frozen_fixtures):
    """The visible symptom of the empty archive was a goalkeeper captaincy.

    Asserted as a data outcome, not a rule: nothing in the engine forbids a GK at
    the top, so if this ever fails again it is the archive that is wrong.
    """
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        archive = _load_archive(s)
    players, results = _score_preseason(
        _new_season_bootstrap(frozen), frozen_fixtures, archive)

    pos = {p["id"]: p["position"] for p in players}
    top20 = sorted(results, key=lambda r: r.ep_next4, reverse=True)[:20]
    keepers = sum(1 for r in top20 if pos[r.player_id] == 1)

    assert keepers <= 2, f"{keepers}/20 of the top ep_next4 are goalkeepers"
    assert pos[top20[0].player_id] in (3, 4), "the top pick should be a MID or FWD"


def test_match_method_is_predominantly_name_position(
        session_factory, frozen, frozen_fixtures):
    """New season = new element ids, so the archive joins on (full_name, position)."""
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        archive = _load_archive(s)
    _, results = _score_preseason(_new_season_bootstrap(frozen), frozen_fixtures, archive)

    methods: dict[str, int] = {}
    for r in results:
        m = r.components["match_method"]
        methods[m] = methods.get(m, 0) + 1

    assert methods.get("name_position", 0) > 0.9 * len(results), methods
    assert methods.get("element_id", 0) == 0, "ids were reassigned; none should match by id"


def test_a_recycled_element_id_never_matches_by_id(
        session_factory, frozen, frozen_fixtures):
    """The bug the backfill uncovered on the live DB (2026-07-30).

    FPL reassigns element ids every July. Measured against the real 2026-27
    bootstrap, only 5 of 564 id-to-id joins landed on the same person — so the
    engine's `element_id` fast path was attaching Haaland's season to Mason Mount
    and calling it `match_method: element_id`. The id path must be VERIFIED by
    name, not assumed.
    """
    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        archive = _load_archive(s)

    # Keep every archived id, but shuffle which player holds it — exactly what a
    # season reset does. Nobody's id now points at their own history.
    boot = copy.deepcopy(frozen)
    ids = [el["id"] for el in boot["elements"]]
    for el, new_id in zip(boot["elements"], ids[1:] + ids[:1]):
        el["id"] = new_id
    for ev in boot["events"]:
        ev["finished"] = False
        ev["is_current"] = False

    players, results = _score_preseason(boot, frozen_fixtures, archive)
    names = {p["id"]: p["full_name"] for p in players}
    archive_names = {r["id"]: r["full_name"] for r in archive}

    for r in results:
        if r.components["match_method"] == "element_id":
            # The only ids allowed through are the ones that still agree by name.
            assert names[r.player_id] == archive_names.get(r.player_id), (
                f"id {r.player_id} matched by id but is a different player: "
                f"{names[r.player_id]} vs {archive_names.get(r.player_id)}"
            )


def test_unmatched_new_players_fall_back_with_honesty_flags(
        session_factory, frozen, frozen_fixtures):
    boot = _new_season_bootstrap(frozen)
    template = boot["elements"][100]
    newcomers = {
        90_001: _brand_new_player(template, 90_001, "Nkemdiche", 75),
        90_002: _brand_new_player(template, 90_002, "Vasquez", 45),
    }
    boot["elements"].extend(newcomers.values())

    backfill_archive(SOURCE, SEASON, session_factory=session_factory)
    with session_factory() as s:
        archive = _load_archive(s)
    _, results = _score_preseason(boot, frozen_fixtures, archive)

    by_id = {r.player_id: r for r in results}
    for pid in newcomers:
        comp = by_id[pid].components
        assert comp["match_method"] == "none"
        assert comp["data_basis"] == "baseline_fallback"
        # The honesty flag is the contract: a reader must be able to tell that this
        # number came from a price band and not from the player.
        assert ("no_history_fallback" in comp["flags"]
                or "promoted_team_fallback" in comp["flags"]), comp["flags"]
        # Fell back CLEANLY — a real, finite, non-negative projection.
        assert by_id[pid].ep_next4 >= 0.0
        assert by_id[pid].ep_next4 == pytest.approx(comp["ep_next4"], abs=0.01)
