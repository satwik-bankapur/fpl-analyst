"""Parser casts (spec §1.1 / §5): string→numeric on ingest, price/10, chance NULL rule."""
from decimal import Decimal

from fpl.parsers import parse_fixture, parse_gameweek, parse_player, to_decimal, to_int


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
