"""`sync_fpl()` — the idempotent data-refresh job (spec §3), plus mode derivation
and season-rollover (§6.1, §6.2).

Ordering guarantee (CLAUDE.md, "Sync failure -> abort without touching the DB.
Stale beats broken."): we fetch AND validate BOTH source payloads (bootstrap +
fixtures) up front, before any write. Only once both are in hand do we open a single
transaction and perform every write. A failed/partial fetch therefore leaves the DB
exactly as it was. The logical write order still follows §3 inside that transaction.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, insert, select

from .client import FPLClient
from .errors import FPLError, SyncAborted
from .models import (
    EpScore,
    Fixture,
    Gameweek,
    Meta,
    Player,
    PlayerArchive,
    PlayerHistory,
    Team,
)
from .parsers import parse_fixture, parse_gameweek, parse_player, parse_team, to_int
from .db import SessionLocal

from engine.scoring import score_all

log = logging.getLogger("fpl.sync")

PRESEASON = "PRESEASON"
IN_SEASON = "IN_SEASON"


@dataclass
class SyncResult:
    mode: str
    players_updated: int
    ep_rows: int
    fixtures: int
    rollover: bool
    duration_s: float


# --- mode / horizon derivation (§6.1) --------------------------------------

def derive_mode(session) -> str:
    """Compute app_mode from the `gameweeks` table. Never stored; always computed.

    IN_SEASON: some GW is current, or at least one GW has finished (GW1 started).
    PRESEASON: events exist for the season but none is current and GW1 hasn't started.
    """
    if session.scalar(select(func.count()).select_from(Gameweek).where(Gameweek.is_current)):
        return IN_SEASON
    if session.scalar(select(func.count()).select_from(Gameweek).where(Gameweek.finished)):
        return IN_SEASON
    return PRESEASON


def next_gameweek(session) -> Gameweek | None:
    """The next unlocked gameweek: prefer is_next, then is_current, then lowest unfinished."""
    g = session.scalars(select(Gameweek).where(Gameweek.is_next)).first()
    if g:
        return g
    g = session.scalars(select(Gameweek).where(Gameweek.is_current)).first()
    if g:
        return g
    return session.scalars(
        select(Gameweek).where(~Gameweek.finished).order_by(Gameweek.id)
    ).first()


# --- meta helpers ----------------------------------------------------------

def get_meta(session, key: str) -> str | None:
    row = session.get(Meta, key)
    return row.value if row else None


def set_meta(session, key: str, value: str) -> None:
    session.merge(Meta(key=key, value=value))


def _load_archive(session) -> list[dict] | None:
    """Load the latest archived season's rows as engine-ready dicts.

    Feeds the Layer 2 engine's pre-season resolution and early-season blend (§5).
    Returns None when nothing is archived yet (in-season, pre-first-rollover).
    """
    rows = session.scalars(select(PlayerArchive)).all()
    if not rows:
        return None
    latest = max(r.season for r in rows)
    return [
        {
            "id": r.id,
            "full_name": r.full_name,
            "team_id": r.team_id,
            "position": r.position,
            "price": float(r.price),
            "ppg": float(r.ppg),
            "xgi": float(r.xgi),
            "minutes": r.minutes,
            "season": r.season,
        }
        for r in rows
        if r.season == latest
    ]


# --- season rollover (§6.2) ------------------------------------------------

def _season_label(deadlines) -> str | None:
    """PL season label (e.g. '2026-27') from a set of GW deadlines (Aug–May span)."""
    yrs = [d for d in deadlines if d is not None]
    if not yrs:
        return None
    earliest = min(yrs)
    start = earliest.year if earliest.month >= 7 else earliest.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def detect_rollover(session, events_raw: list[dict], elements_raw: list[dict]) -> bool:
    """The API has rolled to a new season (§6.2): local DB has finished GWs, but the
    fetched bootstrap shows ALL events unfinished AND total_points reset to 0."""
    local_finished = session.scalar(
        select(func.count()).select_from(Gameweek).where(Gameweek.finished)
    )
    if not local_finished:
        return False
    if any(ev.get("finished") for ev in events_raw):
        return False
    all_points_zero = all((to_int(el.get("total_points"), 0) or 0) == 0 for el in elements_raw)
    return all_points_zero


def _archive_current_season(session, new_season: str | None) -> None:
    """Copy current `players` -> `players_archive` (outgoing season tag), then truncate
    ep_scores and record the new season. Runs ONCE, BEFORE the new-season sync
    overwrites `players` (getting this wrong destroys pre-season's only data)."""
    outgoing = _season_label(session.scalars(select(Gameweek.deadline)).all()) or "unknown"

    current = session.scalars(select(Player)).all()
    cols = [c.key for c in Player.__table__.columns if c.key != "updated_at"]
    archive_rows = []
    for p in current:
        row = {c: getattr(p, c) for c in cols}
        row["season"] = outgoing
        archive_rows.append(row)
    if archive_rows:
        session.execute(insert(PlayerArchive), archive_rows)

    # ep_scores is season-specific — clear it; the new-season sync repopulates.
    session.execute(delete(EpScore))

    if new_season:
        set_meta(session, "season_current", new_season)
    log.warning("SEASON ROLLOVER: archived %s players as season=%s", len(archive_rows), outgoing)


# --- the sync job (§3) -----------------------------------------------------

def sync_fpl(client: FPLClient | None = None, session_factory=SessionLocal) -> SyncResult:
    started = time.monotonic()
    owns_client = client is None
    client = client or FPLClient()
    try:
        # STEP 1 — fetch & validate BOTH payloads before touching the DB.
        try:
            bootstrap = client.get_bootstrap()
            fixtures_raw = client.get_fixtures()
        except FPLError as exc:
            raise SyncAborted(f"fetch failed, DB untouched: {exc}") from exc

        for key in ("elements", "teams", "events"):
            if key not in bootstrap or not isinstance(bootstrap[key], list):
                raise SyncAborted(f"bootstrap missing/invalid key '{key}', DB untouched")

        elements_raw = bootstrap["elements"]
        teams_raw = bootstrap["teams"]
        events_raw = bootstrap["events"]

        # Parse up front so a malformed payload also aborts before any write.
        try:
            teams = [parse_team(t) for t in teams_raw]
            gameweeks = [parse_gameweek(e) for e in events_raw]
            players = [parse_player(el) for el in elements_raw]
            fixtures = [parse_fixture(f) for f in fixtures_raw]
        except (KeyError, TypeError) as exc:
            raise SyncAborted(f"payload parse failed, DB untouched: {exc}") from exc

        # All source data validated. Now one transaction; a failure rolls it all back.
        with session_factory() as session:
            with session.begin():
                # STEP 2 — rollover check (archive BEFORE overwriting players).
                rollover = detect_rollover(session, events_raw, elements_raw)
                if rollover:
                    new_season = _season_label([g["deadline"] for g in gameweeks])
                    _archive_current_season(session, new_season)

                # STEP 3 — upsert teams & gameweeks (parents), overwrite players.
                for t in teams:
                    session.merge(Team(**t))
                for g in gameweeks:
                    session.merge(Gameweek(**g))

                anchor = None  # resolve next GW after gameweeks are in
                session.flush()
                ng = next_gameweek(session)
                anchor = ng.id if ng else 1

                session.execute(delete(Player))
                if players:
                    session.execute(insert(Player), players)

                # STEP 4 — append today's player_history rows (skip if already present).
                today = date.today()
                existing_today = set(
                    session.scalars(
                        select(PlayerHistory.player_id).where(PlayerHistory.snapshot_date == today)
                    ).all()
                )
                history_rows = [
                    {
                        "player_id": p["id"],
                        "snapshot_date": today,
                        "gameweek": anchor,
                        "price": p["price"],
                        "form": p["form"],
                        "ownership_pct": p["ownership_pct"],
                        "status": p["status"],
                        "total_points": p["total_points"],
                    }
                    for p in players
                    if p["id"] not in existing_today
                ]
                if history_rows:
                    session.execute(insert(PlayerHistory), history_rows)

                # STEP 5 — refresh fixtures (preserve NULL gameweeks).
                session.execute(delete(Fixture))
                if fixtures:
                    session.execute(insert(Fixture), fixtures)

                # STEP 6 — recompute ep_scores via the Layer 2 engine (table rewrite).
                mode = derive_mode(session)
                archive_rows = _load_archive(session)
                results = score_all(
                    players, teams, fixtures, gameweeks,
                    mode=mode, anchor=anchor, archive=archive_rows,
                )
                ep_rows = [r.to_row() for r in results]
                session.execute(delete(EpScore))
                if ep_rows:
                    session.execute(insert(EpScore), ep_rows)

        duration = time.monotonic() - started
        result = SyncResult(
            mode=mode,
            players_updated=len(players),
            ep_rows=len(ep_rows),
            fixtures=len(fixtures),
            rollover=rollover,
            duration_s=round(duration, 3),
        )
        # STEP 7 — one summary line.
        log.info(
            "sync_fpl OK mode=%s players=%d ep_rows=%d fixtures=%d rollover=%s anchor_gw=%s %.2fs",
            result.mode, result.players_updated, result.ep_rows, result.fixtures,
            result.rollover, anchor, result.duration_s,
        )
        return result
    finally:
        if owns_client:
            client.close()
