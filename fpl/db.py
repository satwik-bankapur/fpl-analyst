"""Engine / session factory and schema creation.

SQLAlchemy models (see `models`) are the single source of truth for the schema.
`init_db()` materializes them; it works against both SQLite (dev) and Postgres (prod).
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
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


def init_db(eng: Engine | None = None) -> None:
    """Create all tables. Idempotent (create-if-not-exists)."""
    Base.metadata.create_all(eng or engine)
