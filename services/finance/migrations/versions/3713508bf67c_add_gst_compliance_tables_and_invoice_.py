"""add gst compliance tables and invoice tax fields

The structural half of the GST compliance engine: who the seller is, what each document's supply
is for tax purposes, how its tax splits across heads, and an append-only record of every attempt
to report it to the Invoice Registration Portal.

Two new tables:

* **`gst_registrations`** — the organization's own GSTIN, per state, one flagged primary. Tenant
  data (unlike `tax_rates`, which is platform vocabulary), so it is policied by the migration that
  follows this one. `state_code` is derived from the GSTIN by the service, never entered beside
  it, so the two cannot disagree. A partial unique index on `(organization_id) WHERE is_primary`
  makes "at most one primary" a database guarantee.
* **`einvoice_filings`** — one row per attempt, never mutated. The partial unique index on
  `(organization_id, irn) WHERE irn IS NOT NULL` stops the same reference being claimed twice
  within a tenant while leaving every failed attempt (which has no IRN) free to repeat.

**On the backfill.** Three of the new columns can be filled from facts the database already holds,
and three cannot:

* `invoices.supply_type` — a document is B2B iff its customer holds a GSTIN. Recorded, so derived.
* `invoices.place_of_supply` — for a B2B supply the place of supply is the recipient's state, and
  the first two characters of their GSTIN *are* that state. Recorded, so derived.
* `invoice_lines.tax_rate_pct` — before this migration every line was taxed at its document's
  rate, because there was nowhere else for a rate to come from. Recorded, so derived.
* `cgst/sgst/igst` on both tables are left at **zero**, deliberately. Which head a historical
  supply was taxed under depends on the seller's state, and no such column existed when those
  documents were frozen. Splitting them now would be inventing a legal fact rather than recording
  one, so the tax charged stays visible in `tax_minor` and simply is not attributed. An invoice
  frozen at finalize is not re-derived — that is the whole point of freezing it (ADR-0000 §9).

Every NOT NULL column is added with a server default and then has that default dropped: the
default is what lets the column land on a table with rows, and dropping it leaves the model's own
default in charge of new rows, so autogenerate stays quiet afterwards.

Revision ID: 3713508bf67c
Revises: c7e2a6b4d190
Create Date: 2026-08-25 16:31:55.898559
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "3713508bf67c"  # pragma: allowlist secret
down_revision: str | None = "c7e2a6b4d190"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"

#: (table, column, type, server default) for every NOT NULL column added to an existing table.
_ADDED_NOT_NULL: tuple[tuple[str, str, sa.types.TypeEngine, str], ...] = (
    ("invoice_lines", "tax_rate_pct", sa.Numeric(precision=5, scale=2), "0"),
    ("invoice_lines", "cgst_minor", sa.BigInteger(), "0"),
    ("invoice_lines", "sgst_minor", sa.BigInteger(), "0"),
    ("invoice_lines", "igst_minor", sa.BigInteger(), "0"),
    ("invoices", "supply_type", sa.String(length=20), "'b2b'"),
    ("invoices", "reverse_charge", sa.Boolean(), "false"),
    ("invoices", "cgst_minor", sa.BigInteger(), "0"),
    ("invoices", "sgst_minor", sa.BigInteger(), "0"),
    ("invoices", "igst_minor", sa.BigInteger(), "0"),
)


def upgrade() -> None:
    op.create_table(
        "gst_registrations",
        sa.Column("gstin", sa.String(length=15), nullable=False),
        sa.Column("state_code", sa.String(length=2), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("trade_name", sa.String(length=200), nullable=False),
        sa.Column("registration_type", sa.String(length=20), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
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
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gst_registrations")),
        sa.UniqueConstraint("organization_id", "gstin", name="uq_gst_registrations_org_gstin"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_gst_registrations_organization_id"),
        "gst_registrations",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_gst_registrations_org_state",
        "gst_registrations",
        ["organization_id", "state_code"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_gst_registrations_org_primary",
        "gst_registrations",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "einvoice_filings",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("irn", sa.String(length=64), nullable=True),
        sa.Column("ack_no", sa.String(length=40), nullable=True),
        sa.Column("ack_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qr_payload", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["finance.invoices.id"],
            name=op.f("fk_einvoice_filings_invoice_id_invoices"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_einvoice_filings")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_einvoice_filings_org_attempted",
        "einvoice_filings",
        ["organization_id", "attempted_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_einvoice_filings_org_invoice",
        "einvoice_filings",
        ["organization_id", "invoice_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_einvoice_filings_organization_id"),
        "einvoice_filings",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_einvoice_filings_org_irn",
        "einvoice_filings",
        ["organization_id", "irn"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("irn IS NOT NULL"),
    )

    # Nullable additions need no default: a document raised before this slice genuinely has no
    # recorded place of supply, and null is what "not recorded" looks like.
    op.add_column(
        "invoice_lines", sa.Column("hsn_sac", sa.String(length=8), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "invoices", sa.Column("place_of_supply", sa.String(length=2), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "invoices", sa.Column("seller_gstin", sa.String(length=15), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "invoices", sa.Column("buyer_gstin", sa.String(length=15), nullable=True), schema=SCHEMA
    )

    for table, column, type_, default in _ADDED_NOT_NULL:
        op.add_column(
            table,
            sa.Column(column, type_, nullable=False, server_default=sa.text(default)),
            schema=SCHEMA,
        )

    op.create_index(
        "ix_invoices_org_supply_type",
        "invoices",
        ["organization_id", "supply_type"],
        unique=False,
        schema=SCHEMA,
    )

    _backfill()

    # Drop the server defaults now the columns exist and are filled: the model carries no
    # server_default, so leaving them would show up as drift on the next autogenerate.
    for table, column, _type, _default in _ADDED_NOT_NULL:
        op.alter_column(table, column, server_default=None, schema=SCHEMA)


def _backfill() -> None:
    """Fill what the database already knew. See the module docstring for what is left alone."""
    # A document is B2B iff its customer holds a GSTIN — recorded, not guessed.
    op.execute(
        f"""
        UPDATE {SCHEMA}.invoices AS i
           SET supply_type = CASE
                   WHEN c.gstin IS NOT NULL AND c.gstin <> '' THEN 'b2b'
                   ELSE 'b2c'
               END,
               buyer_gstin = nullif(c.gstin, '')
          FROM {SCHEMA}.customers AS c
         WHERE c.id = i.customer_id
        """
    )
    # For a B2B supply the place of supply is the recipient's state, and the first two characters
    # of a GSTIN are that state's code. Guarded by the pattern so a malformed legacy value is left
    # null rather than truncated into a plausible-looking wrong answer.
    op.execute(
        f"""
        UPDATE {SCHEMA}.invoices AS i
           SET place_of_supply = substring(c.gstin from 1 for 2)
          FROM {SCHEMA}.customers AS c
         WHERE c.id = i.customer_id
           AND c.gstin ~ '^[0-9]{{2}}'
        """
    )
    # Before this migration a line had no rate of its own; it was taxed at its document's.
    op.execute(
        f"""
        UPDATE {SCHEMA}.invoice_lines AS l
           SET tax_rate_pct = i.tax_rate_pct
          FROM {SCHEMA}.invoices AS i
         WHERE i.id = l.invoice_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_org_supply_type", table_name="invoices", schema=SCHEMA)
    for column in (
        "igst_minor",
        "sgst_minor",
        "cgst_minor",
        "buyer_gstin",
        "seller_gstin",
        "reverse_charge",
        "supply_type",
        "place_of_supply",
    ):
        op.drop_column("invoices", column, schema=SCHEMA)
    for column in ("igst_minor", "sgst_minor", "cgst_minor", "tax_rate_pct", "hsn_sac"):
        op.drop_column("invoice_lines", column, schema=SCHEMA)

    op.drop_index(
        "uq_einvoice_filings_org_irn",
        table_name="einvoice_filings",
        schema=SCHEMA,
        postgresql_where=sa.text("irn IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_finance_einvoice_filings_organization_id"),
        table_name="einvoice_filings",
        schema=SCHEMA,
    )
    op.drop_index("ix_einvoice_filings_org_invoice", table_name="einvoice_filings", schema=SCHEMA)
    op.drop_index("ix_einvoice_filings_org_attempted", table_name="einvoice_filings", schema=SCHEMA)
    op.drop_table("einvoice_filings", schema=SCHEMA)

    op.drop_index(
        "uq_gst_registrations_org_primary",
        table_name="gst_registrations",
        schema=SCHEMA,
        postgresql_where=sa.text("is_primary"),
    )
    op.drop_index("ix_gst_registrations_org_state", table_name="gst_registrations", schema=SCHEMA)
    op.drop_index(
        op.f("ix_finance_gst_registrations_organization_id"),
        table_name="gst_registrations",
        schema=SCHEMA,
    )
    op.drop_table("gst_registrations", schema=SCHEMA)
