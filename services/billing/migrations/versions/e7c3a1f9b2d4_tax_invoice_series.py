"""billing.tax_invoice_series: the supplier's GST tax-invoice counter (ADR-0004 Q1)

One row per `(series, financial_year)`, allocated atomically inside the finalize transaction by
`services.allocate_tax_invoice_number`. Groundwork only: nothing writes it until a later slice
wires tax invoices into finalize, so existing invoices are untouched.

A new, empty table — expand-only, nothing the previous release reads changes. It has no
`organization_id` and no row-level security policy on purpose: the series belongs to the supplier,
not a tenant (an explicit exemption beside `tax_rates`, ADR-0002 §5). DELETE is withheld from the
app role by env.py (`_REVOKE_DELETE_ONLY`), after `apply_schema_grants`, on every run.

Revision ID: e7c3a1f9b2d4
Revises: b5e2c8d4f1a9
Create Date: 2026-10-12 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c3a1f9b2d4"  # pragma: allowlist secret
down_revision: str | None = "b5e2c8d4f1a9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"
TABLE = "tax_invoice_series"

#: Creating a table locks nothing existing, but fail fast rather than queue if the catalog is busy.
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.create_table(
        TABLE,
        sa.Column("series", sa.String(length=20), nullable=False),
        sa.Column("financial_year", sa.String(length=7), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "series IN ('invoice', 'credit_note')", name=op.f("ck_tax_invoice_series_series_known")
        ),
        sa.CheckConstraint("last_seq >= 1", name=op.f("ck_tax_invoice_series_last_seq_positive")),
        sa.PrimaryKeyConstraint("series", "financial_year", name=op.f("pk_tax_invoice_series")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_table(TABLE, schema=SCHEMA)
