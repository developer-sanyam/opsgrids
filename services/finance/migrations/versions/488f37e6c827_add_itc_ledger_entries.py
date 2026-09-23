"""add itc_ledger_entries

The input-tax-credit ledger's schema: one row per posting, append-only. Guarantees — row-level
security and the append-only trigger — are the next migration, following the same two-step shape
`d4a1c93f6b28` / `f0b7e51a2c94` used for the return and withholding tables, so each migration can
be read as one thing.

**One table, three kinds of row** (`domain.ITC_POSTING_KINDS`): `accrual` when an IMS document is
accepted, `reversal` when an accepted document's status later moves away from `accepted`, and
`utilisation` when a `gstr3b` is filed and consumes credit against output liability. The balance is
never a column — it is `SUM(amount_minor)` per (registration, tax head, period), carried forward —
so `amount_minor` is signed: positive on an accrual, negative on a reversal or a utilisation.

**`source_ref` is the idempotency key.** A unique index on `(organization_id, kind, source_ref)`
means a retried post collides with the row it would otherwise duplicate, the same shape
`ims_documents.source_ref` gives the sync it dedupes.

**`period` and `source_period` are deliberately two columns.** `period` is the return period a
posting is *attributed to* — the one `selectors.itc_period_movement` filters on — and
`source_period` is the period the underlying document or return actually belongs to, kept apart
purely for audit once a late decision pushes a posting past its own period into the next open one.

Four CHECK constraints hold the vocabulary and the sign convention `amount_minor`'s docstring
states at the database, rather than trusting every future caller to remember either.

Revision ID: 488f37e6c827
Revises: b391f0c47549
Create Date: 2026-09-23 09:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "488f37e6c827"  # pragma: allowlist secret
down_revision: str | None = "b391f0c47549"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.create_table(
        "itc_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gst_registration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("source_period", sa.String(length=7), nullable=False),
        sa.Column("tax_head", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("posted_by", sa.String(length=64), nullable=False),
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["gst_registration_id"],
            [f"{SCHEMA}.gst_registrations.id"],
            name="fk_itc_ledger_entries_gst_registration_id_gst_registrations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_itc_ledger_entries"),
        sa.UniqueConstraint(
            "organization_id", "kind", "source_ref", name="uq_itc_ledger_entries_org_kind_source"
        ),
        sa.CheckConstraint(
            "tax_head IN ('igst', 'cgst', 'sgst', 'cess')",
            name=op.f("ck_itc_ledger_entries_tax_head"),
        ),
        sa.CheckConstraint(
            "kind IN ('accrual', 'reversal', 'utilisation')",
            name=op.f("ck_itc_ledger_entries_kind"),
        ),
        sa.CheckConstraint(
            "source_type IN ('ims_decision', 'gst_return')",
            name=op.f("ck_itc_ledger_entries_source_type"),
        ),
        sa.CheckConstraint(
            "(kind = 'accrual' AND amount_minor > 0) OR "
            "(kind IN ('reversal', 'utilisation') AND amount_minor < 0)",
            name=op.f("ck_itc_ledger_entries_amount_sign"),
        ),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's convention
    # so autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_itc_ledger_entries_organization_id"),
        "itc_ledger_entries",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_itc_ledger_entries_org_registration_period",
        "itc_ledger_entries",
        ["organization_id", "gst_registration_id", "period"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_itc_ledger_entries_org_kind",
        "itc_ledger_entries",
        ["organization_id", "kind"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("itc_ledger_entries", schema=SCHEMA)
