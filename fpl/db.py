"""Engine / session factory.

Schema is managed by Alembic migrations (see `migrations/`, applied via
`fpl.migrate.upgrade_to_head` / `python -m fpl init-db`) — NOT by create_all.
`Base.metadata.create_all` is test-only (tests build a throwaway in-memory schema).
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    connect_args = {}
    kwargs = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        # The launch topology is Render free (sleeps after ~15 min idle) in front of
        # Neon free (autosuspends the compute). Both sides drop TCP connections the
        # pool still believes are live, so the FIRST request after any quiet period
        # hands a dead socket to a user and 500s. pool_pre_ping spends one cheap
        # round trip to find that out before the query instead of after it;
        # pool_recycle retires connections before the idle timeout can.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
    engine = create_engine(url, future=True, connect_args=connect_args, **kwargs)
    if url.startswith("sqlite"):
        # Enforce FK constraints on SQLite (off by default) so dev matches prod.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial pragma
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
