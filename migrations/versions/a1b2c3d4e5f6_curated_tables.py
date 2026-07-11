"""curated tables: team_notes + european_weeks (Layer 4 §1)

Revision ID: a1b2c3d4e5f6
Revises: 1281f84b2676
Create Date: 2026-07-10 22:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "1281f84b2676"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_notes",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_table(
        "european_weeks",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("competition", sa.Text(), nullable=False),
        sa.Column("tie_importance", sa.SmallInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("team_id", "gameweek"),
    )


def downgrade() -> None:
    op.drop_table("european_weeks")
    op.drop_table("team_notes")
