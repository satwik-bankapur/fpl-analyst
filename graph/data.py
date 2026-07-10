"""DB read helpers (§0.2: nodes read global tables directly; state stays small).

Everything returns plain dicts with floats (Decimals cast once here, never at the
node level), mirroring the Layer-1 rule that string-numerics are cast on ingest.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from fpl.models import DraftSquad, EpScore, Fixture, Gameweek, Player, Team


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def resolve_ep_anchor(session) -> int | None:
    """The single gameweek all ep_scores are anchored at (None if the table is empty)."""
    return session.scalar(select(EpScore.gameweek).limit(1))


def load_ep_map(session, anchor: int) -> dict[int, dict]:
    """player_id -> {ep, ep_next4, components} at the anchor gameweek."""
    rows = session.scalars(select(EpScore).where(EpScore.gameweek == anchor)).all()
    return {
        r.player_id: {"ep": _f(r.ep), "ep_next4": _f(r.ep_next4), "components": r.components}
        for r in rows
    }


def load_players(session) -> dict[int, dict]:
    rows = session.scalars(select(Player)).all()
    return {
        p.id: {
            "id": p.id, "web_name": p.web_name, "full_name": p.full_name,
            "team_id": p.team_id, "position": p.position, "price": _f(p.price),
            "status": p.status, "chance_of_playing": p.chance_of_playing,
            "ownership_pct": _f(p.ownership_pct), "news": p.news or "",
            "form": _f(p.form), "ppg": _f(p.ppg), "minutes": p.minutes,
        }
        for p in rows
    }


def load_teams(session) -> dict[int, dict]:
    rows = session.scalars(select(Team)).all()
    return {t.id: {"id": t.id, "name": t.name, "short_name": t.short_name} for t in rows}


def load_fixtures(session) -> list[dict]:
    """Only scheduled fixtures (gameweek not null); postponed rows are in no GW set (§4)."""
    rows = session.scalars(select(Fixture).where(Fixture.gameweek.isnot(None))).all()
    return [
        {
            "id": f.id, "gameweek": f.gameweek, "home_team": f.home_team,
            "away_team": f.away_team, "home_difficulty": f.home_difficulty,
            "away_difficulty": f.away_difficulty,
        }
        for f in rows
    ]


def load_gameweeks(session) -> list[dict]:
    rows = session.scalars(select(Gameweek).order_by(Gameweek.id)).all()
    return [{"id": g.id, "finished": g.finished, "is_next": g.is_next,
             "is_current": g.is_current} for g in rows]


def load_draft_squad(session, draft_id: UUID) -> dict | None:
    row = session.get(DraftSquad, draft_id)
    if row is None:
        return None
    return {"id": row.id, "player_ids": list(row.player_ids), "captain_id": row.captain_id}


def ep_config_version(ep_map: dict[int, dict]) -> str:
    """The config_version stamped into the components blobs (all identical per sync)."""
    for v in ep_map.values():
        cv = (v.get("components") or {}).get("config_version")
        if cv:
            return cv
    return "unknown"
