"""add ims reconciliation and purchase tables

The structural half of the second compliance slice: what suppliers filed *about* this business,
what this business recorded buying, what the platform makes of the difference, and what a person
decided about it.

Four new tables, and the interesting decisions are all about identity.

* **`purchase_documents`** — the local counterpart a supplier's filing is matched against.
  Deliberately thin: this slice needs the facts a match is made on, not an accounts-payable
  lifecycle. `supplier_gstin` is NOT NULL, unlike `customers.gstin`, because it is the join key —
  a purchase from an unregistered supplier can never appear in IMS at all. The unique constraint
  is `(organization_id, supplier_gstin, document_type, number)`: a document number is unique per
  supplier per kind, not globally, and two suppliers numbering their invoices `INV-1` is the
  ordinary case rather than a collision.

* **`ims_documents`** — one row per document the portal shows. The unique index is on
  `(organization_id, source_ref)`, the **portal's own row reference**, and that choice is the
  whole reconciliation. Re-syncing a period must create nothing; a supplier who filed the same
  invoice twice must still produce two rows, because that duplicate is precisely what an operator
  needs to see and reject. A unique constraint on the supplier's document number would have made
  the `duplicate` reason unreachable by making duplicates unwritable.

* **`ims_reconciliations`** — the computed suggestion, appended per run so a changed answer can be
  explained. Derived data, not evidence: no append-only trigger, and the following migration says
  why in full.

* **`ims_decisions`** — who accepted or rejected what, and when. This is the row that decides
  money, so it is evidence, and the following migration makes it append-only at the database.

`ims_decisions.ims_document_id` is `ON DELETE RESTRICT` while `ims_reconciliations` cascades: a
document nobody has judged can be removed with its suggestions, and one somebody has judged
cannot be removed at all. That asymmetry is the point — a decision without the document it was
about is not evidence of anything.

Every foreign key here stays **inside the `finance` schema**, which is where they are allowed
(ADR-0000 §10). `organization_id` remains a plain indexed UUID on all four.

Revision ID: ebbf416aa968
Revises: 9c41d0b7e5a2
Create Date: 2026-08-25 17:48:31.972339
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "ebbf416aa968"  # pragma: allowlist secret
down_revision: str | None = "9c41d0b7e5a2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.create_table(
        "ims_documents",
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("supplier_gstin", sa.String(length=15), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=False),
        sa.Column("document_number", sa.String(length=40), nullable=False),
        sa.Column("document_type", sa.String(length=20), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("taxable_minor", sa.BigInteger(), nullable=False),
        sa.Column("cgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("sgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("igst_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("irn", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "fetched_at",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ims_documents")),
        # The portal's row, not the supplier's document — see the module docstring.
        sa.UniqueConstraint("organization_id", "source_ref", name="uq_ims_documents_org_source_ref"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_ims_documents_organization_id"),
        "ims_documents",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    # Every index leads with `organization_id`: under RLS the policy qual is ANDed into every
    # query, so a non-tenant-first index would scan the platform's rows (ADR-0002).
    op.create_index(
        "ix_ims_documents_org_period",
        "ims_documents",
        ["organization_id", "period"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_documents_org_status",
        "ims_documents",
        ["organization_id", "status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_documents_org_supplier",
        "ims_documents",
        ["organization_id", "supplier_gstin"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "purchase_documents",
        sa.Column("supplier_gstin", sa.String(length=15), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("document_type", sa.String(length=20), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("place_of_supply", sa.String(length=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("taxable_minor", sa.BigInteger(), nullable=False),
        sa.Column("tax_rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("cgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("sgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("igst_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_documents")),
        sa.UniqueConstraint(
            "organization_id",
            "supplier_gstin",
            "document_type",
            "number",
            name="uq_purchase_documents_org_supplier_number",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_purchase_documents_organization_id"),
        "purchase_documents",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_purchase_documents_org_date",
        "purchase_documents",
        ["organization_id", "document_date"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_purchase_documents_org_supplier",
        "purchase_documents",
        ["organization_id", "supplier_gstin"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "ims_decisions",
        sa.Column("ims_document_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("suggested_action", sa.String(length=20), nullable=True),
        sa.Column("suggestion_reason", sa.String(length=40), nullable=True),
        sa.Column("reconciliation_id", sa.UUID(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=False),
        sa.Column(
            "decided_at",
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
        # RESTRICT, not CASCADE: a document somebody has judged cannot be removed, because a
        # decision without the document it was about is not evidence of anything.
        sa.ForeignKeyConstraint(
            ["ims_document_id"],
            ["finance.ims_documents.id"],
            name=op.f("fk_ims_decisions_ims_document_id_ims_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ims_decisions")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_ims_decisions_organization_id"),
        "ims_decisions",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_decisions_org_decided",
        "ims_decisions",
        ["organization_id", "decided_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_decisions_org_document",
        "ims_decisions",
        ["organization_id", "ims_document_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "ims_reconciliations",
        sa.Column("ims_document_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("suggested_action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("delta_minor", sa.BigInteger(), nullable=False),
        sa.Column("matched_purchase_id", sa.UUID(), nullable=True),
        sa.Column("duplicate_of_id", sa.UUID(), nullable=True),
        sa.Column(
            "computed_at",
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
        # SET NULL on both references: the counterpart evidence disappearing must not take the
        # suggestion with it, because "matched a document that has since been removed" is itself
        # something an operator needs to see.
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["finance.ims_documents.id"],
            name=op.f("fk_ims_reconciliations_duplicate_of_id_ims_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ims_document_id"],
            ["finance.ims_documents.id"],
            name=op.f("fk_ims_reconciliations_ims_document_id_ims_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matched_purchase_id"],
            ["finance.purchase_documents.id"],
            name=op.f("fk_ims_reconciliations_matched_purchase_id_purchase_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ims_reconciliations")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_ims_reconciliations_organization_id"),
        "ims_reconciliations",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_reconciliations_org_computed",
        "ims_reconciliations",
        ["organization_id", "computed_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_reconciliations_org_document",
        "ims_reconciliations",
        ["organization_id", "ims_document_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_reconciliations_org_run",
        "ims_reconciliations",
        ["organization_id", "run_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ims_reconciliations_org_run", table_name="ims_reconciliations", schema=SCHEMA)
    op.drop_index(
        "ix_ims_reconciliations_org_document", table_name="ims_reconciliations", schema=SCHEMA
    )
    op.drop_index(
        "ix_ims_reconciliations_org_computed", table_name="ims_reconciliations", schema=SCHEMA
    )
    op.drop_index(
        op.f("ix_finance_ims_reconciliations_organization_id"),
        table_name="ims_reconciliations",
        schema=SCHEMA,
    )
    op.drop_table("ims_reconciliations", schema=SCHEMA)

    op.drop_index("ix_ims_decisions_org_document", table_name="ims_decisions", schema=SCHEMA)
    op.drop_index("ix_ims_decisions_org_decided", table_name="ims_decisions", schema=SCHEMA)
    op.drop_index(
        op.f("ix_finance_ims_decisions_organization_id"),
        table_name="ims_decisions",
        schema=SCHEMA,
    )
    op.drop_table("ims_decisions", schema=SCHEMA)

    op.drop_index(
        "ix_purchase_documents_org_supplier", table_name="purchase_documents", schema=SCHEMA
    )
    op.drop_index("ix_purchase_documents_org_date", table_name="purchase_documents", schema=SCHEMA)
    op.drop_index(
        op.f("ix_finance_purchase_documents_organization_id"),
        table_name="purchase_documents",
        schema=SCHEMA,
    )
    op.drop_table("purchase_documents", schema=SCHEMA)

    op.drop_index("ix_ims_documents_org_supplier", table_name="ims_documents", schema=SCHEMA)
    op.drop_index("ix_ims_documents_org_status", table_name="ims_documents", schema=SCHEMA)
    op.drop_index("ix_ims_documents_org_period", table_name="ims_documents", schema=SCHEMA)
    op.drop_index(
        op.f("ix_finance_ims_documents_organization_id"),
        table_name="ims_documents",
        schema=SCHEMA,
    )
    op.drop_table("ims_documents", schema=SCHEMA)
