"""online payment fact: the collection that paid an order (ADR-0024 A4, Fulfilment F10)

`orders.paid_via_charge_id` — the `services/payment` collection whose **verified** capture set
the order's `paid_at` (recorded from `payment.collections.payment.captured`). A plain UUID, never
a cross-schema foreign key (ADR-0000 §10). NULL for every existing order, which is exactly right:
none was paid online. It is what lets a later full refund of that same collection reverse the
paid fact (and a refund that then fails restore it) without ever touching a `paid_at` that the
counter or carrier operator attested.

Additive and nullable: a catalogue change, no rewrite, no backfill. The downgrade drops it.

Revision ID: c8e2a4f6b1d3
Revises: c8a2f4e6b1d3
Create Date: 2026-09-29 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a4f6b1d3"  # pragma: allowlist secret
down_revision: str | None = "c8a2f4e6b1d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("paid_via_charge_id", sa.UUID(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("orders", "paid_via_charge_id", schema=SCHEMA)
