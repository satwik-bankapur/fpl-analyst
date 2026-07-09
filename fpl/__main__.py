"""CLI for the data layer.

    python -m fpl init-db     apply Alembic migrations (create/upgrade schema)
    python -m fpl sync        run sync_fpl() once against the live FPL API
    python -m fpl mode        print derived app_mode (PRESEASON / IN_SEASON)
    python -m fpl run         start the in-process scheduler (blocks)

Schema is managed by Alembic, never create_all (see fpl/migrate.py). `sync`, `mode`,
and `run` assume the schema already exists — run `init-db` once first.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .db import SessionLocal
from .errors import FPLError
from .migrate import upgrade_to_head
from .sync import derive_mode, next_gameweek, sync_fpl


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="fpl", description="FPL Analyst data layer (Layer 1)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="apply Alembic migrations (create/upgrade schema)")
    sub.add_parser("sync", help="run sync_fpl() once against the live FPL API")
    sub.add_parser("mode", help="print derived app_mode")
    sub.add_parser("run", help="start the in-process scheduler (blocks)")

    args = parser.parse_args(argv)

    if args.cmd == "init-db":
        upgrade_to_head()
        print("schema migrated to head")
        return 0

    if args.cmd == "sync":
        try:
            result = sync_fpl()
        except FPLError as exc:
            print(f"sync aborted: {exc}", file=sys.stderr)
            return 1
        print(
            f"mode={result.mode} players={result.players_updated} ep_rows={result.ep_rows} "
            f"fixtures={result.fixtures} rollover={result.rollover} {result.duration_s}s"
        )
        return 0

    if args.cmd == "mode":
        with SessionLocal() as session:
            mode = derive_mode(session)
            ng = next_gameweek(session)
        print(f"mode={mode} next_gw={ng.id if ng else None}")
        return 0

    if args.cmd == "run":
        from .scheduler import build_scheduler

        scheduler = build_scheduler()
        scheduler.start()
        print("scheduler started (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown()
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
