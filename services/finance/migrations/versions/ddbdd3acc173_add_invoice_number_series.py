"""add invoice number series

The server-owned invoice numbering counter (`app.models.InvoiceNumberSeries`). Before this,
`InvoiceCreate.number` was accepted verbatim from whoever called `services.create_invoice` — fine
for the demo seed, which replays exact historical numbers to match the approved SPA fixtures, but
not for a real caller: two people issuing an invoice at the same moment could race for the same
number, and the only defence was the `(organization_id, number)` unique constraint answering with
a 409 *after* the fact.

`POST /finance/invoices`, the principal route, now allocates the number itself
(`services.allocate_invoice_number`) instead of accepting one — one row per
(organization, series, financial year), incremented with a single atomic
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING next_seq`. Two requests racing for the same
organization serialize on that row's own lock rather than needing an advisory lock this table
would otherwise have to remember to take. `series` separates `invoice` from `credit_note` so a
credit note never contends with an invoice for the next number.

Mutable by design, unlike most of this schema: this is a counter, not evidence, so it carries no
append-only trigger and none of the RLS exemptions `tax_rates` or `withholding_rates` get either
— it is tenant data like every other table here.

Revision ID: ddbdd3acc173
Revises: f0b7e51a2c94
Create Date: 2026-09-21 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "ddbdd3acc173"  # pragma: allowlist secret
down_revision: str | None = "f0b7e51a2c94"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"


def upgrade() -> None:
    op.create_table(
        "invoice_number_series",
        sa.Column("series", sa.String(length=20), nullable=False),
        sa.Column("financial_year", sa.String(length=7), nullable=False),
        sa.Column("next_seq", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_number_series")),
        sa.UniqueConstraint(
            "organization_id",
            "series",
            "financial_year",
            name="uq_invoice_number_series_org_series_fy",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_invoice_number_series_organization_id"),
        "invoice_number_series",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    # Row-level security, the same policy shape every tenant-scoped table in this schema gets.
    # `current_org()` already exists (the first RLS migration created it).
    op.execute(f"ALTER TABLE {SCHEMA}.invoice_number_series ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.invoice_number_series FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.invoice_number_series
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.invoice_number_series"
    )
    op.drop_index(
        op.f("ix_finance_invoice_number_series_organization_id"),
        table_name="invoice_number_series",
        schema=SCHEMA,
    )
    op.drop_table("invoice_number_series", schema=SCHEMA)
