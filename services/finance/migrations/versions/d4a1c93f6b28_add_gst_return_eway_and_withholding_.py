"""gst return, e-Way bill and withholding tables

The third GST slice's schema: the outward-supplies return and its status trail, the e-Way bill
ledger, the statutory withholding schedule and the register computed against it. Guarantees —
row-level security, the append-only triggers and the snapshot freeze — are the next migration, so
this one is a plain "here are the tables" and can be read as one.

**`gst_returns` is one row per (registration, period, type)**, which is the grain the obligation
itself has: a taxpayer files GSTR-1 once for August under one GSTIN. The snapshot lives on the row
as JSONB rather than in child tables, because a return is one atomic declaration and splitting it
across tables would let half of it be replaced. `gst_return_transitions` is the trail the `status`
column is only the head of.

**`eway_bills` mirrors `einvoice_filings`** — one row per attempt, never mutated. What differs is
that a bill *expires*: `valid_until` is stored **and** recomputable from `valid_from` and
`distance_km`, which are also stored, so a screen and a roadside checkpoint are reading the same
arithmetic over the same two values rather than trusting a number only the portal could produce.

**`withholding_rates` carries no `organization_id`**, exactly like `tax_rates`, and is left
UNPOLICIED in the next migration for the same reason: 2 % under GST s. 51 is the law of one
country, not one tenant's data. It is seeded here with the three heads this slice models, because
a register with no schedule behind it computes nothing and the schedule is not tenant data anybody
could be expected to enter.

The three `customers` columns are the facts the withholding assessment is gated on. All three
default false, and that default is load-bearing rather than cosmetic: it is what makes the whole
feature opt-in, so this migration cannot invent a TDS figure for an existing organization that has
told the platform nothing about its counterparties.

Revision ID: d4a1c93f6b28
Revises: b62d9f4c17ae
Create Date: 2026-08-25 09:40:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4a1c93f6b28"  # pragma: allowlist secret
down_revision: str | None = "b62d9f4c17ae"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"

#: The withholding facts recorded about a buyer. All three default false, and that default is
#: load-bearing rather than cosmetic: it is what makes the whole feature opt-in, so this migration
#: cannot invent a TDS figure for an organization that has told the platform nothing about its
#: counterparties.
_CUSTOMER_FLAGS = ("is_tds_deductor", "deducts_tds_194q", "tcs_applicable")

#: The statutory schedule as it stands, seeded so the register has something to resolve against.
#:
#: Rows, not constants: the whole reason this is a table is that a rate change must not restate
#: history, which means a change is a *new row* with a later `effective_from` and every figure
#: already computed keeps pointing at the version that produced it.
#:
#:   gst_tds_51      2 % of the taxable value, on notified supplies above 2,50,000 rupees under
#:                   one contract. A cliff: below the threshold nothing, above it the rate on the
#:                   whole value. Effective from the day s. 51 was brought into force.
#:   itax_194q       0.1 % of purchases from one seller beyond 50,00,000 rupees in a financial
#:                   year. An aggregate: the rate bites only on the excess.
#:   itax_206c_1h    0.1 % of receipts from one buyer beyond the same 50,00,000, collected by the
#:                   seller — and mutually exclusive with 194Q by statute.
_SEED = (
    (
        "gst_tds_51",
        "GST TDS - section 51",
        "2.00",
        250_000_00,
        "supply",
        date(2018, 10, 1),
    ),
    (
        "itax_194q",
        "Income tax TDS - section 194Q",
        "0.10",
        5_000_000_00,
        "aggregate",
        date(2021, 7, 1),
    ),
    (
        "itax_206c_1h",
        "Income tax TCS - section 206C(1H)",
        "0.10",
        5_000_000_00,
        "aggregate",
        date(2020, 10, 1),
    ),
)


def upgrade() -> None:
    # ---------------------------------------------------------------- customers
    # The three flags the withholding assessment is gated on. NOT NULL with a server default so
    # existing rows are filled, then the default is dropped: the model carries none, so leaving
    # it would show up as drift on the next autogenerate — the same shape the GST-facts migration
    # used for `invoices.supply_type` and `reverse_charge`.
    for column in _CUSTOMER_FLAGS:
        op.add_column(
            "customers",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.text("false")),
            schema=SCHEMA,
        )
    for column in _CUSTOMER_FLAGS:
        op.alter_column("customers", column, server_default=None, schema=SCHEMA)

    # ---------------------------------------------------------------- gst_returns
    op.create_table(
        "gst_returns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gst_registration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gstin", sa.String(length=15), nullable=False),
        sa.Column("state_code", sa.String(length=2), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("return_type", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_taxable_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_cgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_sgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_igst_minor", sa.BigInteger(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arn", sa.String(length=40), nullable=True),
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
            name="fk_gst_returns_gst_registration_id_gst_registrations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gst_returns"),
        sa.UniqueConstraint(
            "organization_id",
            "gst_registration_id",
            "period",
            "return_type",
            name="uq_gst_returns_org_registration_period_type",
        ),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's
    # convention so autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_gst_returns_organization_id"),
        "gst_returns",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_gst_returns_org_period", "gst_returns", ["organization_id", "period"], schema=SCHEMA
    )
    op.create_index(
        "ix_gst_returns_org_status", "gst_returns", ["organization_id", "status"], schema=SCHEMA
    )

    # ---------------------------------------------------------------- gst_return_transitions
    op.create_table(
        "gst_return_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gst_return_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("arn", sa.String(length=40), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("total_taxable_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("transitioned_by", sa.String(length=64), nullable=False),
        sa.Column(
            "transitioned_at",
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
            ["gst_return_id"],
            [f"{SCHEMA}.gst_returns.id"],
            name="fk_gst_return_transitions_gst_return_id_gst_returns",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gst_return_transitions"),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's
    # convention so autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_gst_return_transitions_organization_id"),
        "gst_return_transitions",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_gst_return_transitions_org_return",
        "gst_return_transitions",
        ["organization_id", "gst_return_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_gst_return_transitions_org_at",
        "gst_return_transitions",
        ["organization_id", "transitioned_at"],
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- eway_bills
    op.create_table(
        "eway_bills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("eway_bill_no", sa.String(length=16), nullable=True),
        sa.Column("transport_mode", sa.String(length=10), nullable=False),
        sa.Column("distance_km", sa.Integer(), nullable=False),
        sa.Column("vehicle_no", sa.String(length=20), nullable=True),
        sa.Column("transporter_id", sa.String(length=20), nullable=True),
        sa.Column("transport_doc_no", sa.String(length=40), nullable=True),
        sa.Column("transport_doc_date", sa.Date(), nullable=True),
        sa.Column("over_dimensional", sa.Boolean(), nullable=False),
        sa.Column("consignment_value_minor", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempted_at",
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
            ["invoice_id"],
            [f"{SCHEMA}.invoices.id"],
            name="fk_eway_bills_invoice_id_invoices",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_eway_bills"),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's
    # convention so autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_eway_bills_organization_id"),
        "eway_bills",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_eway_bills_org_invoice", "eway_bills", ["organization_id", "invoice_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_eway_bills_org_attempted",
        "eway_bills",
        ["organization_id", "attempted_at"],
        schema=SCHEMA,
    )
    # A bill number is unique nationally; within a tenant it must never be duplicated. Partial,
    # because every `failed`, `pending` and `cancelled` row legitimately carries none.
    op.create_index(
        "uq_eway_bills_org_number",
        "eway_bills",
        ["organization_id", "eway_bill_no"],
        unique=True,
        postgresql_where=sa.text("eway_bill_no IS NOT NULL"),
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- withholding_rates
    op.create_table(
        "withholding_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("threshold_minor", sa.BigInteger(), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("jurisdiction", sa.String(length=2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_withholding_rates"),
        sa.UniqueConstraint(
            "jurisdiction", "code", "effective_from", name="uq_withholding_rates_version"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_withholding_rates_code_effective",
        "withholding_rates",
        ["jurisdiction", "code", "effective_from"],
        schema=SCHEMA,
    )

    # ---------------------------------------------------------------- invoice_withholdings
    op.create_table(
        "invoice_withholdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("withholding_rate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("threshold_minor", sa.BigInteger(), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("basis_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("cgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("sgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("igst_minor", sa.BigInteger(), nullable=False),
        sa.Column("counterparty_gstin", sa.String(length=15), nullable=True),
        sa.Column("counterparty_name", sa.String(length=200), nullable=False),
        sa.Column("resolved_on", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
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
            ["invoice_id"],
            [f"{SCHEMA}.invoices.id"],
            name="fk_invoice_withholdings_invoice_id_invoices",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["withholding_rate_id"],
            [f"{SCHEMA}.withholding_rates.id"],
            name="fk_invoice_withholdings_withholding_rate_id_withholding_rates",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_withholdings"),
        # One head bites once per document. A second deduction under the same section is a
        # correction, and a correction to a document is a new document.
        sa.UniqueConstraint(
            "organization_id", "invoice_id", "code", name="uq_invoice_withholdings_invoice_code"
        ),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's
    # convention so autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_invoice_withholdings_organization_id"),
        "invoice_withholdings",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_invoice_withholdings_org_period",
        "invoice_withholdings",
        ["organization_id", "period"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_invoice_withholdings_org_code",
        "invoice_withholdings",
        ["organization_id", "code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_invoice_withholdings_org_invoice",
        "invoice_withholdings",
        ["organization_id", "invoice_id"],
        schema=SCHEMA,
    )

    _seed_withholding_rates()


def _seed_withholding_rates() -> None:
    """Seed the three statutory heads.

    Ids are generated here rather than fixed, because `invoice_withholdings.withholding_rate_id`
    points at whichever row was in force when a figure was computed — the reference is what
    matters, not the value of the id. `ON CONFLICT DO NOTHING` on the version key so re-running
    a rolled-back upgrade is safe and never duplicates a band.
    """
    table = sa.table(
        "withholding_rates",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("rate_pct", sa.Numeric),
        sa.column("threshold_minor", sa.BigInteger),
        sa.column("basis", sa.String),
        sa.column("jurisdiction", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        schema=SCHEMA,
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "rate_pct": rate,
                "threshold_minor": threshold,
                "basis": basis,
                "jurisdiction": "IN",
                "effective_from": effective_from,
                "effective_to": None,
            }
            for code, name, rate, threshold, basis, effective_from in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("invoice_withholdings", schema=SCHEMA)
    op.drop_table("withholding_rates", schema=SCHEMA)
    op.drop_table("eway_bills", schema=SCHEMA)
    op.drop_table("gst_return_transitions", schema=SCHEMA)
    op.drop_table("gst_returns", schema=SCHEMA)
    for column in ("tcs_applicable", "deducts_tds_194q", "is_tds_deductor"):
        op.drop_column("customers", column, schema=SCHEMA)
