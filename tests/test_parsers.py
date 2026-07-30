"""Parser casts (spec §1.1 / §5): string→numeric on ingest, price/10, chance NULL rule."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from fpl.parsers import (
    parse_fixture, parse_gameweek, parse_player, parse_team, to_decimal, to_int,
)


def test_string_numerics_cast_to_decimal(bootstrap):
    saka = parse_player(bootstrap["elements"][0])
    assert saka["form"] == Decimal("5.0")
    assert saka["ppg"] == Decimal("4.5")
    assert saka["ownership_pct"] == Decimal("30.5")
    assert saka["xgi"] == Decimal("7.30")
    # types are Decimal, not str — casting happened at ingest
    assert all(isinstance(saka[k], Decimal) for k in ("form", "ppg", "ownership_pct", "xgi"))


def test_price_divided_by_ten(bootstrap):
    saka = parse_player(bootstrap["elements"][0])  # now_cost 75
    assert saka["price"] == Decimal("7.5")


def test_full_name_is_first_plus_second(bootstrap):
    saka = parse_player(bootstrap["elements"][0])
    assert saka["full_name"] == "Bukayo Saka"


def test_chance_null_stays_none_not_zero(bootstrap):
    saka = parse_player(bootstrap["elements"][0])   # null
    watkins = parse_player(bootstrap["elements"][1])  # 25
    assert saka["chance_of_playing"] is None  # NULL = no flag = treat as 100 downstream
    assert watkins["chance_of_playing"] == 25


def test_news_persisted_verbatim(bootstrap):
    watkins = parse_player(bootstrap["elements"][1])
    assert watkins["news"] == "Knock - 25% chance of playing"


def test_fixture_null_event_preserved(fixtures_raw):
    postponed = parse_fixture(fixtures_raw[4])  # event: null
    assert postponed["gameweek"] is None  # never defaulted to a GW


def test_gameweek_parse(bootstrap):
    gw1 = parse_gameweek(bootstrap["events"][0])
    assert gw1["is_next"] is True and gw1["finished"] is False
    assert gw1["deadline"].tzinfo is not None  # tz-aware


def test_defensive_casts_handle_garbage():
    assert to_decimal("", None) is None
    assert to_decimal("not-a-number", Decimal(0)) == Decimal(0)
    assert to_int(None, 7) == 7


# --- team mapping: element.team -> teams.id (added 2026-07-30) --------------
# FPL exposes TWO team identifiers on every element: `team` (the 1..20 id that
# joins to teams.id) and `team_code` (a stable cross-season club code, 1..90+).
# Confusing them — or deriving a team from a list index — would silently
# mislabel every player, and with them every fixture, FDR and clean-sheet
# number in the system, while still looking plausible on screen. Nothing here
# is fixable-by-observation once it drifts, so it gets a frozen-asset test.

_FROZEN = Path(__file__).parent / "assets" / "bootstrap_2025-26.json"

# Hand-written from the frozen END-OF-2025-26 payload. These are that season's
# clubs, not today's — the point is a fixed expectation the parser must keep
# reproducing, so do NOT "correct" these to a later season's transfers.
EXPECTED_CLUBS = [
    (430, "Haaland", "MCI"),
    (16, "Saka", "ARS"),
    (499, "Isak", "LIV"),
    (235, "Palmer", "CHE"),
    (64, "Watkins", "AVL"),
    # Meslier was at Leeds in 2025-26. If a later season's data shows him
    # elsewhere, that is a transfer, not a mapping bug.
    (339, "Meslier", "LEE"),
]


@pytest.fixture(scope="module")
def frozen_season():
    raw = json.loads(_FROZEN.read_text(encoding="utf-8"))
    teams = {t["id"]: t["short_name"] for t in (parse_team(t) for t in raw["teams"])}
    players = {p["id"]: p for p in (parse_player(el) for el in raw["elements"])}
    codes = {el["id"]: el["team_code"] for el in raw["elements"]}
    return teams, players, codes


@pytest.mark.parametrize("pid,web_name,short_name", EXPECTED_CLUBS)
def test_player_resolves_to_the_right_club(frozen_season, pid, web_name, short_name):
    teams, players, _ = frozen_season
    assert players[pid]["web_name"] == web_name, "asset ids shifted — refresh the pairs"
    assert teams[players[pid]["team_id"]] == short_name


def test_team_id_is_not_the_team_code(frozen_season):
    """The specific confusion this guards against, made explicit.

    `team_code` is a different number in a different namespace. If the parser
    ever read it instead of `team`, most lookups would miss the teams table
    entirely and the rest would land on the wrong club.
    """
    teams, players, codes = frozen_season
    for pid, web_name, short_name in EXPECTED_CLUBS:
        code = codes[pid]
        assert code != players[pid]["team_id"], f"{web_name}: pick a clearer fixture"
        assert teams.get(code) != short_name, (
            f"{web_name}: team_code {code} coincidentally resolves to {short_name}"
        )
