"""SQLAlchemy models — the full Layer-1 schema (spec §2).

Every table from §2 plus a small `meta` key-value table referenced by the
season-rollover routine (§6.2 step 4). Numeric precisions mirror the spec; on
SQLite precision is not enforced but the column types round-trip correctly.

Note on JSON: SQLAlchemy's `JSON` maps to JSONB on Postgres and JSON on SQLite,
so the `components` / `player_ids` blobs are portable across both.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FPL id
    name: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str] = mapped_column(Text)
    strength_overall_home: Mapped[int] = mapped_column(Integer)
    strength_overall_away: Mapped[int] = mapped_column(Integer)
    strength_attack_home: Mapped[int] = mapped_column(Integer)
    strength_attack_away: Mapped[int] = mapped_column(Integer)
    strength_defence_home: Mapped[int] = mapped_column(Integer)
    strength_defence_away: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class _PlayerColumns:
    """Columns shared by `players` (current snapshot) and `players_archive` (prior seasons).

    A mixin so the two tables cannot drift apart (archive == players + `season`).
    """

    web_name: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)  # first + second
    team_id: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(SmallInteger)  # 1 GK, 2 DEF, 3 MID, 4 FWD
    price: Mapped[Decimal] = mapped_column(Numeric(4, 1))  # £m = now_cost / 10
    total_points: Mapped[int] = mapped_column(Integer)
    form: Mapped[Decimal] = mapped_column(Numeric(4, 1))  # cast from string
    ppg: Mapped[Decimal] = mapped_column(Numeric(4, 2))  # cast from string
    minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(1))
    # NULL = no flag = treat as 100 downstream. NEVER coerce to 0.
    chance_of_playing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    news: Mapped[str] = mapped_column(Text, default="")  # verbatim
    ownership_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # cast from string
    xg: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    xa: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    xgi: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    xgc: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    transfers_in_event: Mapped[int] = mapped_column(Integer)
    transfers_out_event: Mapped[int] = mapped_column(Integer)


class Player(_PlayerColumns, Base):
    """Current-season snapshot, overwritten each sync."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FPL element id
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerArchive(_PlayerColumns, Base):
    """Prior seasons, append-only. Written ONLY by the season-rollover routine (§6.2).

    This table is what powers pre-season analysis.
    """

    __tablename__ = "players_archive"

    season: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. '2025-26'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FPL element id
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerHistory(Base):
    """Daily snapshot log, append-only.

    WRITE-ONLY IN v1 — intentional (CLAUDE.md, spec §2). No v1 code reads this table;
    it accrues price/ownership time-series that are unrecoverable if not logged from
    day one. Do NOT remove as dead code.
    """

    __tablename__ = "player_history"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    gameweek: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    form: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    ownership_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(1))
    total_points: Mapped[int] = mapped_column(Integer)


class Gameweek(Base):
    __tablename__ = "gameweeks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished: Mapped[bool] = mapped_column(Boolean)
    is_current: Mapped[bool] = mapped_column(Boolean)
    is_next: Mapped[bool] = mapped_column(Boolean)
    avg_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    most_captained: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = postponed/unscheduled: in no gameweek's fixture set.
    gameweek: Mapped[int | None] = mapped_column(ForeignKey("gameweeks.id"), nullable=True)
    home_team: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    home_difficulty: Mapped[int] = mapped_column(SmallInteger)
    away_difficulty: Mapped[int] = mapped_column(SmallInteger)
    kickoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished: Mapped[bool] = mapped_column(Boolean)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EpScore(Base):
    """Computed output of the heuristic layer, rewritten each sync.

    In Layer 1 this is populated by the STUB writer (`ep_stub`). Layer 2 replaces
    the formula behind the same contract. `components` is REQUIRED and is the brief
    writer's raw material.
    """

    __tablename__ = "ep_scores"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gameweek: Mapped[int] = mapped_column(Integer, primary_key=True)  # horizon anchor (next GW)
    ep: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # next-GW expected points
    ep_next4: Mapped[Decimal] = mapped_column(Numeric(6, 2))  # 4-GW horizon
    components: Mapped[dict] = mapped_column(JSON)  # labeled breakdown; JSONB on Postgres
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DraftSquad(Base):
    """Pre-season manual squads (§6.3)."""

    __tablename__ = "draft_squads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    player_ids: Mapped[list] = mapped_column(JSON)  # array of 15 element ids
    captain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Brief(Base):
    """Generated output + cost cache."""

    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fpl_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL in pre-season
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("draft_squads.id"), nullable=True
    )  # NULL in season mode
    gameweek: Mapped[int] = mapped_column(Integer)  # 0 = pre-season brief
    brief_md: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)  # hash of squad + EP inputs
    model: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # exactly one of fpl_team_id / draft_id is non-null
        CheckConstraint(
            "(fpl_team_id IS NULL) <> (draft_id IS NULL)", name="brief_exactly_one_owner"
        ),
        # unique(fpl_team_id, gameweek) where fpl_team_id not null
        Index(
            "uq_brief_team_gw",
            "fpl_team_id",
            "gameweek",
            unique=True,
            sqlite_where=text("fpl_team_id IS NOT NULL"),
            postgresql_where=text("fpl_team_id IS NOT NULL"),
        ),
        # unique(draft_id) where draft_id not null
        Index(
            "uq_brief_draft",
            "draft_id",
            unique=True,
            sqlite_where=text("draft_id IS NOT NULL"),
            postgresql_where=text("draft_id IS NOT NULL"),
        ),
    )


class EmailSignup(Base):
    __tablename__ = "email_signups"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    fpl_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str] = mapped_column(Text, default="free")  # 'free' / 'paid'
    source: Mapped[str | None] = mapped_column(Text, nullable=True)  # acquisition tracking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Meta(Base):
    """Small key-value table for pipeline state (e.g. `season_current`, spec §6.2 step 4)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
