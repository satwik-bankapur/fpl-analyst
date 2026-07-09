"""Stub EP writer: placeholder formula, but correct blank/double math (§5.1) and
the chance_of_playing NULL = 100% rule (§5.2)."""
from decimal import Decimal

from fpl.parsers import parse_fixture, parse_player
from fpl.ep_stub import stub_ep_rows


def _rows(bootstrap, fixtures_raw):
    players = [parse_player(e) for e in bootstrap["elements"]]
    fixtures = [parse_fixture(f) for f in fixtures_raw]
    rows = stub_ep_rows(players, fixtures, next_gw=1)
    return {r["player_id"]: r for r in rows}


def test_double_gameweek_sums_both_fixtures(bootstrap, fixtures_raw):
    rows = _rows(bootstrap, fixtures_raw)
    saka = rows[1]  # team 1: GW1 has fx1 (H, diff2 -> 1.15) + fx2 (A, diff2 -> 1.15)
    # form 5.0, avail 1.0 -> 5 * (1.15 + 1.15) = 11.5
    assert saka["ep"] == Decimal("11.50")
    assert saka["components"]["next_gw"]["double"] is True
    assert len(saka["components"]["next_gw"]["fixtures"]) == 2


def test_next4_includes_blank_as_zero(bootstrap, fixtures_raw):
    rows = _rows(bootstrap, fixtures_raw)
    saka = rows[1]
    # GW1 double = 11.5, GW2 blank = 0, GW3 single (H diff1 -> 1.3) = 6.5, GW4 blank = 0
    assert saka["ep_next4"] == Decimal("18.00")
    horizon = {h["gameweek"]: h for h in saka["components"]["horizon"]}
    assert horizon[2]["blank"] is True and horizon[2]["points"] == 0
    assert horizon[3]["double"] is False and horizon[3]["points"] == 6.5


def test_null_event_fixture_never_counts(bootstrap, fixtures_raw):
    # fx5 (event null) is team 1 vs team 2; it must not appear in any GW's set.
    rows = _rows(bootstrap, fixtures_raw)
    saka = rows[1]
    # horizon spans GW1..GW4 for team 1: fx1+fx2 (GW1 double), blank GW2, fx4 (GW3), blank GW4.
    horizon_fx = [f for h in saka["components"]["horizon"] for f in h["fixtures"]]
    # only fixtures with a real GW appear; the postponed one (fx5) contributes nothing
    assert len(horizon_fx) == 3  # fx1, fx2 (GW1) + fx4 (GW3)


def test_chance_null_treated_as_full_availability(bootstrap, fixtures_raw):
    rows = _rows(bootstrap, fixtures_raw)
    assert rows[1]["components"]["minutes_prob"] == 1.0  # chance NULL -> 100%
    assert rows[2]["components"]["minutes_prob"] == 0.25  # chance 25 -> 0.25


def test_components_present_and_flagged_stub(bootstrap, fixtures_raw):
    rows = _rows(bootstrap, fixtures_raw)
    for r in rows.values():
        assert r["components"]["stub"] is True  # Layer 2 replaces the formula
        assert "minutes_prob" in r["components"]
