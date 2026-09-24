"""add attempt and retry_at to eway_bills

The two facts an automatic e-Way retry (C4) needs a row to *say*, so that a screen — and the reclaim
sweep — can read them off the newest row instead of reconstructing them from the whole history.

* `attempt` — which attempt of its generation chain wrote the row (1-based). Set on the
  `submitting` claim, the `pending` outage row and the terminal `failed` row of a generation, so
  the read model can say "attempt 3 of 8" from one row. Null on everything written before this
  migration and on withdrawals, which have no chain of attempts to number.
* `retry_at` — on a `pending` row, when the scheduled retry is due: the countdown the finance
  screen shows and the instant a retry that never fired is measured against. The retry itself is a
  scheduled event (ADR-0015); this is the same instant written where a person can read it.

Both are nullable and carry no default, so this is additive in the strict sense: no existing row is
touched, and nothing that reads the table before the deploy ships needs to know they exist.
`eway_bills` is append-only by trigger (`f0b7e51a2c94`), and that trigger refuses UPDATE and DELETE
of rows — it has no bearing on `ALTER TABLE ADD COLUMN`, which rewrites nothing.

Revision ID: 4c1d8e5f7a92
Revises: e7d648663241
Create Date: 2026-09-24 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c1d8e5f7a92"  # pragma: allowlist secret
down_revision: str | None = "e7d648663241"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.add_column("eway_bills", sa.Column("attempt", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column(
        "eway_bills",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("eway_bills", "retry_at", schema=SCHEMA)
    op.drop_column("eway_bills", "attempt", schema=SCHEMA)
