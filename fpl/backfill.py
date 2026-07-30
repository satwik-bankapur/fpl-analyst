"""Missed-rollover recovery: rebuild `players_archive` from a season's bootstrap JSON.

WHY THIS EXISTS (2026-07-30). `players_archive` is normally written exactly once
per season, by `_archive_current_season()` inside a sync that DETECTS the rollover
(sync §6.2: local DB has finished GWs + the fetched bootstrap shows all events
unfinished and every total_points reset). That detection has one blind spot: if no
sync runs between the last finished gameweek and the API's season reset, the
rollover is never observed. `players` is then overwritten with the new season's
blank slate and last season's rates are gone — silently.

The symptom is not an error, it is *plausible nonsense*: with an empty archive,
`_resolve_preseason` falls through to the (position, price-band) baseline for
EVERY player, so every ep is near-identical and the only remaining
differentiator is clean-sheet + save points. Goalkeepers float to the top of the
table and get recommended as captain. The engine is behaving exactly as
specified — it has no data.

This module is the recovery path: point it at an end-of-season bootstrap payload
and it reconstructs that season's archive rows directly, no rollover required.

It is deliberately a committed command and not a scratch script — the next missed
rollover is a matter of when, not if, and this is what fixes it in one line.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import delete, func, insert, select

from .db import SessionLocal
from .errors import FPLError
from .models import PlayerArchive
from .parsers import parse_player

log = logging.getLogger("fpl.backfill")


class BackfillRefused(FPLError):
    """The season already has archive rows and --force was not given."""

    code = "backfill_refused"


def load_elements(path: str | Path) -> list[dict]:
    """Read `elements` out of a bootstrap-static JSON payload (file on disk).

    Accepts either the full bootstrap object or a bare list of elements, because
    both shapes turn up in saved payloads.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    elements = raw.get("elements") if isinstance(raw, dict) else raw
    if not isinstance(elements, list) or not elements:
        raise FPLError(f"{path}: no 'elements' list found — not a bootstrap payload")
    return elements


def backfill_archive(
    path: str | Path,
    season: str,
    *,
    force: bool = False,
    session_factory=SessionLocal,
) -> int:
    """Rebuild `players_archive` for `season` from the bootstrap payload at `path`.

    Idempotent by construction: every row for `season` is deleted and rewritten in
    one transaction, so re-running replaces rather than duplicating (the table's PK
    is (season, id), so a duplicate would otherwise be an IntegrityError anyway).

    Raises BackfillRefused if rows already exist for `season` and `force` is False
    — overwriting a real rollover's archive is the one thing this must not do by
    accident.

    Returns the number of rows written.
    """
    elements = load_elements(path)          # parse before touching the DB
    rows = [{**parse_player(el), "season": season} for el in elements]

    with session_factory() as session, session.begin():
        existing = session.scalar(
            select(func.count()).select_from(PlayerArchive)
            .where(PlayerArchive.season == season)
        )
        if existing and not force:
            raise BackfillRefused(
                f"players_archive already has {existing} rows for season {season}; "
                "pass --force to replace them"
            )
        session.execute(delete(PlayerArchive).where(PlayerArchive.season == season))
        session.execute(insert(PlayerArchive), rows)

    log.warning(
        "ARCHIVE BACKFILL: season=%s rows=%d replaced=%d source=%s",
        season, len(rows), existing or 0, path,
    )
    return len(rows)
