"""relax briefs owner CHECK for the generic preview + generic uniqueness (Layer 4 §6)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-10 22:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode recreates the table on SQLite (CHECK cannot be altered in place);
    # on Postgres it issues DROP/ADD CONSTRAINT.
    with op.batch_alter_table("briefs", schema=None) as batch:
        batch.drop_constraint("brief_exactly_one_owner", type_="check")
        batch.create_check_constraint(
            "brief_not_both_owners",
            "NOT (fpl_team_id IS NOT NULL AND draft_id IS NOT NULL)",
        )
    op.create_index(
        "uq_brief_generic", "briefs", ["gameweek"], unique=True,
        sqlite_where=sa.text("fpl_team_id IS NULL AND draft_id IS NULL"),
        postgresql_where=sa.text("fpl_team_id IS NULL AND draft_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_brief_generic", table_name="briefs")
    with op.batch_alter_table("briefs", schema=None) as batch:
        batch.drop_constraint("brief_not_both_owners", type_="check")
        batch.create_check_constraint(
            "brief_exactly_one_owner",
            "(fpl_team_id IS NULL) <> (draft_id IS NULL)",
        )
