"""Double opt-in: email_signups.confirmed_at + confirm_token_hash

The 2026-08-06 pre-launch batch. `POST /api/signup` is public and
unauthenticated, so until now anyone could put a stranger's address on the
mailing list — a mail bomb pointed at them and a spam-rate problem pointed at
our sending domain. The route has carried a comment naming double opt-in as the
fix since Stage B; this is that fix.

ADDITIVE, same rule as d4e5f6a7b8c9 and e5f6a7b8c9d0 — two nullable ADD COLUMNs
and no table rewritten.

WITH ONE DELIBERATE EXCEPTION, WHICH IS THE ONLY INTERESTING LINE IN THIS FILE:
existing rows are backfilled `confirmed_at = created_at`, i.e. grandfathered as
confirmed. Read the reasoning before copying it anywhere:

  - `api.send` refuses to mail a NULL `confirmed_at`. Leaving existing rows NULL
    would therefore silently empty the entire free list, and "silently" is the
    problem — nobody would notice until a Friday send went out to nobody.
  - These addresses were not harvested. Each was typed into a signup form by
    someone who wanted the thing. Double opt-in proves the typist OWNS the
    address; it is not a claim that the earlier consent was fabricated.
  - The honest counter-argument is that grandfathering is exactly what double
    opt-in exists to prevent, and it is a fair one. It loses here because of the
    specific fact that **there are no production rows to grandfather**: nothing
    is deployed, no address has ever been mailed, and `email_signups` is empty
    everywhere this will run. The backfill is written to be correct if that ever
    stops being true, not because it is currently doing anything.

  If this repo is ever restored from a backup that DOES contain a real list,
  make that decision again on purpose rather than inheriting this one.

`tier` gets no data migration. Its default changes from 'free' to 'pending' in
the model, which affects only rows inserted afterwards; an existing 'free' row
is already what a confirmed signup would become, and an existing 'paid' row must
not be touched by anything that is not the verified webhook.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_signups",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "email_signups",
        sa.Column("confirm_token_hash", sa.Text(), nullable=True),
    )
    # See the module docstring. `created_at` rather than now() so a grandfathered
    # row still reports when the address actually arrived.
    op.execute(
        "UPDATE email_signups SET confirmed_at = created_at "
        "WHERE confirmed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("email_signups", "confirm_token_hash")
    op.drop_column("email_signups", "confirmed_at")
