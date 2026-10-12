"""fulfilment_settings.auto_invoice_orders: order invoicing's per-organization opt-in

INVENTORY-01. Expand-only: one nullable-free boolean with `server_default false`, so every existing
organization stays opted out and old code (which never names the column) keeps inserting rows.

Revision ID: d4e1a7c3b952
Revises: a3d8e5b2c917
Create Date: 2026-10-12 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e1a7c3b952"  # pragma: allowlist secret
down_revision: str | None = "a3d8e5b2c917"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
TABLE = "fulfilment_settings"
COLUMN = "auto_invoice_orders"

#: `ALTER TABLE` takes an ACCESS EXCLUSIVE lock; fail fast rather than queue every settings read
#: behind a long transaction. Local to the migration's own transaction.
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
