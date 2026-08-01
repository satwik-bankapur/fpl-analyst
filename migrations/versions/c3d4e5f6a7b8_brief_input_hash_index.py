"""index briefs.input_hash — the brief cache lookup (audit 2026-08-01)

`graph.cache.get_or_generate` runs `SELECT ... FROM briefs WHERE input_hash = ?`
on every brief request. Nothing indexed that column, so the check that exists to
AVOID work was itself a sequential scan growing with the table.

Non-unique on purpose: a hash legitimately repeats across superseded rows
(api/inspect._cache_state), so a unique index would reject a valid insert.

Additive and online-safe: CREATE INDEX on a table with no rows to rewrite. No
data is read, moved or dropped, so this can run against a populated production DB.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_briefs_input_hash", "briefs", ["input_hash"])


def downgrade() -> None:
    op.drop_index("ix_briefs_input_hash", table_name="briefs")
