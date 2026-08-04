"""Stage B: subscriptions, webhook_events, send_log, rate_hits, unsubscribe tombstone

Spec §1.10 plus three tables the spec implies but does not name:
  - `send_log`   — §3's idempotency key for the weekly batch
  - `rate_hits`  — the DB-backed limiter from docs/AUDIT.md §C2 (the in-process dict
                   reset on every Render wake, so it bounded nothing in production)
  - `email_signups.unsubscribed_at` — §1.8's one-click unsubscribe, as a tombstone

ADDITIVE ONLY. Every operation here is CREATE TABLE / ADD COLUMN (nullable, no
default backfill), so it runs against a populated production database without
reading, moving or dropping a row. Verified against the audit's migration-safety
review (docs/AUDIT.md §M9).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("fpl_team_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subscription_id", sa.Text(), nullable=False),
        sa.Column("provider_customer_id", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # Unique so a redelivered subscription.created updates instead of duplicating.
        sa.UniqueConstraint("provider_subscription_id"),
    )
    op.create_index("ix_subscriptions_email", "subscriptions", ["email"])
    op.create_index("ix_subscriptions_fpl_team_id", "subscriptions", ["fpl_team_id"])

    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        # The PK is the idempotency mechanism, not a convenience.
        sa.PrimaryKeyConstraint("event_id"),
    )

    op.create_table(
        "send_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_send_log_recipient", "send_log",
                    ["email", "gameweek", "template"], unique=True)

    op.create_table(
        "rate_hits",
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bucket", "window_start"),
    )

    # Nullable, no server default: existing rows stay NULL = "still subscribed",
    # which is the correct reading of "we never heard from them".
    op.add_column("email_signups",
                  sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("email_signups", "unsubscribed_at")
    op.drop_table("rate_hits")
    op.drop_index("uq_send_log_recipient", table_name="send_log")
    op.drop_table("send_log")
    op.drop_table("webhook_events")
    op.drop_index("ix_subscriptions_fpl_team_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_email", table_name="subscriptions")
    op.drop_table("subscriptions")
