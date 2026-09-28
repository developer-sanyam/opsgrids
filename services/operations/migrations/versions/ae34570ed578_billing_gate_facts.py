"""billing gate facts: order invoice/paid/pickup-code, settings pickup/tax defaults

ADR-0025 §1's `READY -> HANDED_OVER` billing gate; `docs/fulfilment/plan.md` F4.

1. `orders` gains four columns, all facts written by an event consumer or by the READY/handover
   write paths — never a second status (`Order`'s own docstring): `invoice_id` (recorded from
   finance's `finance.orders.invoice.issued`), `paid_at` (recorded by the counter handover's own
   operator attestation today — no tenant payment-collections module exists yet, ADR-0024 A4),
   `pickup_code` / `pickup_code_verified_at` (issued at READY, verified at handover).
2. `fulfilment_settings` gains `require_pickup_code` (default `False`, so an existing
   organization's counter flow is unaffected until it opts in) and `default_tax_rate_pct`
   (`Numeric(5, 2)`, default `0` — the same shape `services/finance`'s own `tax_rate_pct` columns
   use), both snapshotted onto `Order.settings_snapshot` at accept the same way
   `invoice_timing`/`payment_defaults` already are.

Revision ID: ae34570ed578
Revises: 2610a9e71ea5
Create Date: 2026-09-28 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ae34570ed578"  # pragma: allowlist secret
down_revision: str | None = "2610a9e71ea5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


def upgrade() -> None:
    op.add_column("orders", sa.Column("invoice_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column(
        "orders",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "orders", sa.Column("pickup_code", sa.String(length=20), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "orders",
        sa.Column("pickup_code_verified_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    op.add_column(
        "fulfilment_settings",
        sa.Column("require_pickup_code", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.alter_column(
        "fulfilment_settings", "require_pickup_code", server_default=None, schema=SCHEMA
    )
    op.add_column(
        "fulfilment_settings",
        sa.Column(
            "default_tax_rate_pct",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    op.alter_column(
        "fulfilment_settings", "default_tax_rate_pct", server_default=None, schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_fulfilment_settings_default_tax_rate_pct_in_range"),
        "fulfilment_settings",
        "default_tax_rate_pct >= 0 AND default_tax_rate_pct <= 100",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_fulfilment_settings_default_tax_rate_pct_in_range"),
        "fulfilment_settings",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("fulfilment_settings", "default_tax_rate_pct", schema=SCHEMA)
    op.drop_column("fulfilment_settings", "require_pickup_code", schema=SCHEMA)

    op.drop_column("orders", "pickup_code_verified_at", schema=SCHEMA)
    op.drop_column("orders", "pickup_code", schema=SCHEMA)
    op.drop_column("orders", "paid_at", schema=SCHEMA)
    op.drop_column("orders", "invoice_id", schema=SCHEMA)
