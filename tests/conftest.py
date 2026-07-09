"""Shared fixtures. Frozen JSON assets drive everything — no live API calls (CLAUDE.md)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fpl.models import Base

ASSETS = Path(__file__).parent / "assets"


def load_asset(name: str) -> dict | list:
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


@pytest.fixture
def bootstrap() -> dict:
    return load_asset("bootstrap.json")


@pytest.fixture
def fixtures_raw() -> list:
    return load_asset("fixtures.json")


@pytest.fixture
def session_factory():
    """In-memory SQLite shared across sessions (StaticPool keeps one connection)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


class FakeClient:
    """Stands in for FPLClient in sync tests, serving frozen assets."""

    def __init__(self, bootstrap: dict, fixtures: list):
        self._bootstrap = bootstrap
        self._fixtures = fixtures
        self.closed = False

    def get_bootstrap(self) -> dict:
        return copy.deepcopy(self._bootstrap)

    def get_fixtures(self) -> list:
        return copy.deepcopy(self._fixtures)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(bootstrap, fixtures_raw) -> FakeClient:
    return FakeClient(bootstrap, fixtures_raw)
