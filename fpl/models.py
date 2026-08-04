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

    Populated by the Layer 2 engine (`engine.scoring.score_all`), which replaced
    the original Layer 1 `ep_stub` behind the same contract. `components` is
    REQUIRED and is the brief writer's raw material.
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
        # Personalized briefs have exactly one owner; the generic weekly preview
        # (Layer 4 §6) has NEITHER (fpl_team_id NULL AND draft_id NULL). So the only
        # thing forbidden is BOTH being set.
        CheckConstraint(
            "NOT (fpl_team_id IS NOT NULL AND draft_id IS NOT NULL)",
            name="brief_not_both_owners",
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
        # the single generic preview per gameweek: unique(gameweek) where both NULL
        Index(
            "uq_brief_generic",
            "gameweek",
            unique=True,
            sqlite_where=text("fpl_team_id IS NULL AND draft_id IS NULL"),
            postgresql_where=text("fpl_team_id IS NULL AND draft_id IS NULL"),
        ),
        # The cache lookup. `graph.cache.get_or_generate` runs
        # `WHERE input_hash = ?` on EVERY brief request, and the three indexes
        # above are all on other columns — so the hottest read in the product was a
        # sequential scan whose cost grows with every brief ever generated. Not
        # unique: a regenerated brief legitimately repeats a hash after a row is
        # superseded (see api/inspect._cache_state).
        Index("ix_briefs_input_hash", "input_hash"),
    )


class TeamNote(Base):
    """Curated scouting notes (Layer 4 §1.1) — the moat table, maintained by hand.

    One note per team (tactical identity, set pieces, rotation, pen takers). Its
    max(updated_at) is `notes_version`, part of the brief cache key (Layer 3 §4).
    """

    __tablename__ = "team_notes"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    note: Mapped[str] = mapped_column(Text)  # 2–3 lines
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)  # seed rows for tests
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EuropeanWeek(Base):
    """Hand-maintained European fixture schedule (Layer 4 §1.2).

    Feeds the Layer 2 rotation dampener and makes rotation hedging mandatory in the
    Writer. max(updated_at) joins `notes_version` in the brief cache key.
    """

    __tablename__ = "european_weeks"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    gameweek: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition: Mapped[str] = mapped_column(Text)  # 'UCL' / 'UEL' / 'UECL'
    tie_importance: Mapped[int] = mapped_column(SmallInteger)  # 1–3
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EmailSignup(Base):
    __tablename__ = "email_signups"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    fpl_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str] = mapped_column(Text, default="free")  # 'free' / 'paid'
    source: Mapped[str | None] = mapped_column(Text, nullable=True)  # acquisition tracking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # MAIL consent, not billing state. Set by the one-click unsubscribe (§1.8).
    # A TOMBSTONE, not a delete: without the row, the next signup form submission or
    # CSV re-import silently re-adds someone who asked to leave. The delete path
    # (§1.8, "delete my data") is the one that removes the row, and it is a
    # different, deliberate action.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class Account(Base):
    """A logged-in identity (accounts build, 2026-08-04). Layer 5 §5's "no accounts
    in v1" rule is superseded — see the layer5 drift note of the same date.

    **`email` is the join key to everything that predates accounts.** Subscriptions,
    signups and send_log were all keyed by address, so the account is introduced as
    a layer *over* the address rather than a replacement for it. The address is
    stored already-normalised (stripped, lowercased) by `api.accounts.normalize_email`
    and carries a UNIQUE index.

    Case-insensitivity is normalisation-at-the-boundary, NOT a `citext` column: the
    codebase already lowercases every address it touches (`_email_of`,
    `unsubscribe_token`, `service.signup`), a `citext` column needs a Postgres
    EXTENSION that a managed database can refuse, and it would be silently absent
    under SQLite, where the whole test suite runs — so the property would be
    untested exactly where it is asserted. One writer path, one normalisation, one
    unique index, and `tests/test_accounts.py::test_email_case_is_normalised_on_every_path`
    holds it.
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, index=True)
    # 'magic_link' / 'google'. The provider of the FIRST successful login; an
    # account created by magic link that later signs in with Google keeps the
    # original value and gains `provider_id`. Never used for gating — it is a
    # support/audit field, and treating it as a credential would mean an attacker
    # picking their own auth method.
    auth_provider: Mapped[str] = mapped_column(Text, default="magic_link")
    # Google's stable subject id (`sub`). NULL for magic-link-only accounts.
    provider_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # Terms acceptance, captured at account creation for BOTH providers. A NULL
    # `terms_accepted_at` on an existing row means "created before terms were
    # tracked" and is treated as needing acceptance, not as having accepted.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    """A browser session. The class is `AuthSession` because `Session` is
    SQLAlchemy's own; the TABLE is `sessions` as specified.

    **Only the hash of the session token is stored.** The plaintext exists in the
    user's cookie and nowhere else, so a database leak yields no usable session —
    the same property `magic_link_tokens` has, for the same reason. Lookup is by
    hash, which is why `token_hash` is the primary key rather than a surrogate id.
    """

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MagicLinkToken(Base):
    """A single-use sign-in token, hashed at rest, 15-minute expiry.

    `consumed_at` rather than a DELETE: a token that has been used must be
    *distinguishable* from one that never existed, so a replayed link can be logged
    as a replay. Both cases return the same thing to the caller.

    Keyed by `email`, not by account id — requesting a link must not require an
    account to exist, or the endpoint would answer "no such user" by failing, which
    is the enumeration oracle the route exists to avoid.
    """

    __tablename__ = "magic_link_tokens"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    """Paid access (spec §1.10). WRITTEN ONLY BY THE VERIFIED WEBHOOK HANDLER.

    This table — not `email_signups.tier` — is the source of truth for entitlement.
    `tier` is reachable from the public unauthenticated signup route, so deriving
    paid access from it would let a caller grant themselves the paid product (the
    2026-08-01 audit found `service.signup` doing exactly the reverse, silently
    demoting people). Entitlement asks `billing.is_entitled`, which reads here.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, index=True)
    # The account this subscription belongs to (accounts build, 2026-08-04).
    # NULLABLE on purpose and permanently: the webhook resolves it from the
    # verified `custom_data.email`, but a subscription whose account could not be
    # resolved must still be RECORDED — a billing row we refuse to write because
    # of an identity problem is money taken with no trace of it. An unlinked row
    # is simply not entitled through any session, which is the safe direction.
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    fpl_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(Text, default="paddle")
    # Unique so a redelivered `subscription.created` updates rather than duplicates.
    # For the one-time season pass there is no Paddle subscription, so this holds
    # the transaction id prefixed `txn:` — see api/billing.py.
    provider_subscription_id: Mapped[str] = mapped_column(Text, unique=True)
    provider_customer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[str] = mapped_column(Text)                    # 'monthly' / 'season'
    # Free text from Paddle ON PURPOSE — never an enum. A status this code has never
    # heard of must be storable so it can be seen, and must not be entitled. The
    # allow-list in api/billing.ENTITLED_STATUSES decides; the column just records.
    status: Mapped[str] = mapped_column(Text)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WebhookEvent(Base):
    """Processed provider events (spec §1.10). The PK IS the idempotency mechanism.

    Paddle redelivers on timeout or 5xx, so a duplicate `subscription.created` must
    not grant twice or mail twice. The handler INSERTs here inside the same
    transaction as the state change; a PK violation means "already processed" ->
    return 200 and do nothing. Do NOT replace this with a `SELECT ... if not exists`
    check, which races against a concurrent redelivery.
    """

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # The verified payload, kept for billing disputes. Contains the customer email,
    # so it inherits the retention policy of `email_signups`, not of `briefs`.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SendLog(Base):
    """One row per (recipient, gameweek, template) — the send batch's idempotency key.

    Inserted BEFORE the provider call, not after: a crash between call and record
    would otherwise re-send on the next run, and a duplicate email to a paying
    customer is worse than a missing one we can retry deliberately.
    """

    __tablename__ = "send_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text)
    gameweek: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)      # 'paid_brief' / 'free_preview'
    status: Mapped[str] = mapped_column(Text)        # 'sent' / 'failed' / 'skipped'
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("uq_send_log_recipient", "email", "gameweek", "template", unique=True),
    )


class RateHit(Base):
    """Fixed-window counter for the DB-backed rate limiter (audit §C2).

    Replaces the in-process dict, which reset on every Render wake and so bounded
    nothing in production. `bucket` is an IP for the per-caller limits and the
    literal 'global' for the whole-service generation cap.

    # ponytail: fixed window, not sliding — a caller can burst across a boundary and
    # briefly get 2x. That is the right trade for an abuse ceiling; make it sliding
    # only if the burst itself becomes the problem.
    """

    __tablename__ = "rate_hits"

    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)


class Meta(Base):
    """Small key-value table for pipeline state (e.g. `season_current`, spec §6.2 step 4)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
