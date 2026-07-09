"""Programmatic Alembic access — the ONLY sanctioned way to create/upgrade schema.

`create_all` is never used for real schema management (test fixtures only). Schema
changes go through Alembic migrations in `migrations/versions/`.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import settings

_ROOT = Path(__file__).resolve().parent.parent  # repo root (holds alembic.ini + migrations/)


def alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def upgrade_to_head() -> None:
    """Apply all pending migrations (create schema on a fresh DB)."""
    command.upgrade(alembic_config(), "head")
