"""Defensive parsers: raw FPL JSON dicts -> validated field dicts for the ORM.

Two hard rules enforced here (CLAUDE.md, spec §1.1 / §5):
  - FPL string-numerics (form, ppg, xg*, selected_by_percent) are cast to numeric
    ON INGEST, never at query time.
  - `chance_of_playing_next_round == NULL` means NO FLAG -> stored as None (=> treat
    as 100 downstream). It is NEVER coerced to 0.

Ingest by explicit field name and ignore unknown keys (§5.6 schema drift): the FPL
API adds fields mid-season; we never "insert the whole object".
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def to_decimal(value, default: Decimal | None = None) -> Decimal | None:
    """Cast an FPL string-numeric to Decimal. Empty/None/garbage -> `default`."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def to_int(value, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def to_bool(value) -> bool:
    return bool(value)


def to_dt(value) -> datetime | None:
    """Parse an FPL ISO-UTC timestamp (e.g. '2026-08-15T17:30:00Z') to aware datetime."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --- element (player) ------------------------------------------------------

def parse_player(el: dict) -> dict:
    """Map one `elements` entry to a `players` row dict (also used for archive)."""
    first = (el.get("first_name") or "").strip()
    second = (el.get("second_name") or "").strip()
    full_name = f"{first} {second}".strip()
    return {
        "id": to_int(el["id"]),
        "web_name": el.get("web_name") or "",
        "full_name": full_name,
        "team_id": to_int(el.get("team")),
        "position": to_int(el.get("element_type")),
        "price": to_decimal(el.get("now_cost"), Decimal(0)) / Decimal(10),  # ×10 encoding
        "total_points": to_int(el.get("total_points"), 0),
        "form": to_decimal(el.get("form"), Decimal(0)),
        "ppg": to_decimal(el.get("points_per_game"), Decimal(0)),
        "minutes": to_int(el.get("minutes"), 0),
        "status": (el.get("status") or "a")[:1],
        # NULL stays NULL — do NOT default to 0 (spec §5.2).
        "chance_of_playing": to_int(el.get("chance_of_playing_next_round"), None),
        "news": el.get("news") or "",  # verbatim
        "ownership_pct": to_decimal(el.get("selected_by_percent"), Decimal(0)),
        "xg": to_decimal(el.get("expected_goals"), Decimal(0)),
        "xa": to_decimal(el.get("expected_assists"), Decimal(0)),
        "xgi": to_decimal(el.get("expected_goal_involvements"), Decimal(0)),
        "xgc": to_decimal(el.get("expected_goals_conceded"), Decimal(0)),
        "transfers_in_event": to_int(el.get("transfers_in_event"), 0),
        "transfers_out_event": to_int(el.get("transfers_out_event"), 0),
    }


# --- team ------------------------------------------------------------------

def parse_team(t: dict) -> dict:
    return {
        "id": to_int(t["id"]),
        "name": t.get("name") or "",
        "short_name": t.get("short_name") or "",
        "strength_overall_home": to_int(t.get("strength_overall_home"), 0),
        "strength_overall_away": to_int(t.get("strength_overall_away"), 0),
        "strength_attack_home": to_int(t.get("strength_attack_home"), 0),
        "strength_attack_away": to_int(t.get("strength_attack_away"), 0),
        "strength_defence_home": to_int(t.get("strength_defence_home"), 0),
        "strength_defence_away": to_int(t.get("strength_defence_away"), 0),
    }


# --- event (gameweek) ------------------------------------------------------

def parse_gameweek(ev: dict) -> dict:
    return {
        "id": to_int(ev["id"]),
        "name": ev.get("name") or "",
        "deadline": to_dt(ev.get("deadline_time")),
        "finished": to_bool(ev.get("finished")),
        "is_current": to_bool(ev.get("is_current")),
        "is_next": to_bool(ev.get("is_next")),
        "avg_score": to_int(ev.get("average_entry_score"), None),
        "most_captained": to_int(ev.get("most_captained"), None),
    }


# --- fixture ---------------------------------------------------------------

def parse_fixture(fx: dict) -> dict:
    return {
        "id": to_int(fx["id"]),
        # event may be NULL — preserve it (postponed/unscheduled). NEVER default to a GW.
        "gameweek": to_int(fx.get("event"), None),
        "home_team": to_int(fx.get("team_h")),
        "away_team": to_int(fx.get("team_a")),
        "home_difficulty": to_int(fx.get("team_h_difficulty"), 3),
        "away_difficulty": to_int(fx.get("team_a_difficulty"), 3),
        "kickoff": to_dt(fx.get("kickoff_time")),
        "finished": to_bool(fx.get("finished")),
        "home_score": to_int(fx.get("team_h_score"), None),
        "away_score": to_int(fx.get("team_a_score"), None),
    }
