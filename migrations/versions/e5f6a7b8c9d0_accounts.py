"""Accounts: accounts, sessions, magic_link_tokens, subscriptions.account_id

The accounts build (2026-08-04). Layer 5 §5's "no user accounts in v1" is
superseded — see the layer5 §8 drift note of the same date.

ADDITIVE ONLY, same rule as d4e5f6a7b8c9: three CREATE TABLEs and one nullable
ADD COLUMN. No existing row is read, moved or rewritten, so this runs against a
populated production database. In particular `subscriptions.account_id` is NOT
backfilled here — the webhook links a subscription to an account when it next
fires, and an unlinked subscription is simply not entitled through a session,
which is the direction that fails safe.

`accounts.email` is TEXT + UNIQUE, not CITEXT. Case-insensitivity is enforced by
normalising at the boundary (`api.accounts.normalize_email`), which is what the
rest of the codebase already does with addresses. CITEXT would need a Postgres
EXTENSION a managed database can refuse, and it does not exist under SQLite where
the tests run — so the guarantee would be untested exactly where it is asserted.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("auth_provider", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique, not merely indexed: two accounts for one address would split a
    # customer's subscription from their session and is unrecoverable by hand.
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)
    op.create_index("ix_accounts_provider_id", "accounts", ["provider_id"])

    op.create_table(
        "sessions",
        # The HASH is the key. The plaintext token lives only in the user's cookie.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
        # CASCADE: deleting an account must not leave a session that resolves to a
        # missing row. The delete path also removes these explicitly, because
        # SQLite does not enforce FK actions unless PRAGMA foreign_keys is on.
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_account_id", "sessions", ["account_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "magic_link_tokens",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_magic_link_tokens_email", "magic_link_tokens", ["email"])

    # Nullable, no backfill — see the module docstring.
    op.add_column("subscriptions", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_subscriptions_account_id", "subscriptions", ["account_id"])
    # Named so the downgrade can drop it; SET NULL rather than CASCADE because a
    # deleted account must never take a billing record with it.
    with op.batch_alter_table("subscriptions") as batch:
        batch.create_foreign_key("fk_subscriptions_account_id", "accounts",
                                 ["account_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_constraint("fk_subscriptions_account_id", type_="foreignkey")
    op.drop_index("ix_subscriptions_account_id", table_name="subscriptions")
    op.drop_column("subscriptions", "account_id")
    op.drop_index("ix_magic_link_tokens_email", table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_account_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_accounts_provider_id", table_name="accounts")
    op.drop_index("ix_accounts_email", table_name="accounts")
    op.drop_table("accounts")
