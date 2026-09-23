"""charge lease columns and the provider order id (D3 round 3)

Additive only — every column is nullable or carries a server default, so the upgrade is a
catalogue change on a live table and a rolling deploy can run old and new code side by side:

- `provider_order_id` — the provider Order a charge's payment is created against. Razorpay has
  no idempotency key of its own (ADR-0004 §6); its Order, created with `receipt` = the platform's
  idempotency key and looked up by it before any create, is what makes a resumed provider call
  find the payment a crashed one already made instead of debiting twice.
- `attempts`, `next_attempt_at`, `lease_until` — who may call the provider for a charge, and
  when. Claimed with a single conditional `UPDATE … RETURNING`, so two callers of the same key
  (a request and its own HTTP retry, a request and the stuck-charge sweep) can never both hold it;
  `attempts` bounds the sweep, which fails a charge after the budget and lets dunning proceed.

Plus a partial unique index on the order id and a partial index for the sweep's discovery.

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c9
Create Date: 2026-09-24 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d1"  # pragma: allowlist secret
down_revision: str | None = "d4e5f6a7b8c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"


def upgrade() -> None:
    op.add_column(
        "charges", sa.Column("provider_order_id", sa.String(length=120), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "charges",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "charges",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "charges", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.create_index(
        "uq_charges_org_provider_order_id",
        "charges",
        ["organization_id", "provider_order_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("provider_order_id IS NOT NULL"),
    )
    op.create_index(
        "ix_charges_unresolved",
        "charges",
        ["created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'created' AND provider_payment_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_charges_unresolved", table_name="charges", schema=SCHEMA)
    op.drop_index("uq_charges_org_provider_order_id", table_name="charges", schema=SCHEMA)
    op.drop_column("charges", "lease_until", schema=SCHEMA)
    op.drop_column("charges", "next_attempt_at", schema=SCHEMA)
    op.drop_column("charges", "attempts", schema=SCHEMA)
    op.drop_column("charges", "provider_order_id", schema=SCHEMA)
