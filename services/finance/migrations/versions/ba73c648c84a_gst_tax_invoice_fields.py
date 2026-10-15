"""gst tax invoice fields

Makes the customer invoice a complete CGST Rule 46 tax invoice. Expand-only: every new column is
nullable or carries a server default, so the finance API still running the previous build keeps
inserting rows between `migrate` and `up`, and nothing existing is rewritten.

* `gst_registrations` gains the supplier's principal place of business (address lines, city,
  PIN code). The state is the GSTIN's, already stored as `state_code`.
* `supplier_profiles` is new: PAN and the bank account a customer pays into, one live row per
  organization. The account number is sealed by the application (`platform_core.pii`) and stored
  as `bank_account_number_enc` beside its mask. Tenant data, so it gets the same RLS policy every
  tenant table in this schema has.
* `customers` gains a billing address and the GST state code of that address.
* `invoices` gains `round_off_minor` (part of `total_minor`) and the party snapshots a document
  prints: supplier legal name and address, buyer name, address and state, and a delivery address.
* `invoice_lines` gains the unit quantity code, the discount, and the inventory item (a plain
  UUID into another schema, never a foreign key) the line was picked from, with its SKU.

Downgrade drops exactly what upgrade added. It is lossless for documents issued before this
revision; for documents issued after it, downgrading discards their snapshots, units, discounts
and round-off — totals stay consistent only while `round_off_minor` is zero, so do not downgrade
once invoices have been issued with rounding.

Revision ID: ba73c648c84a
Revises: faf3ff81d039
Create Date: 2026-10-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ba73c648c84a"  # pragma: allowlist secret
down_revision: str | None = "faf3ff81d039"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"


def _text(name: str, length: int) -> sa.Column:
    return sa.Column(name, sa.String(length=length), nullable=False, server_default="")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    for column in (
        _text("address_line1", 200),
        _text("address_line2", 200),
        _text("city", 120),
        _text("pincode", 6),
    ):
        op.add_column("gst_registrations", column, schema=SCHEMA)

    for column in (
        _text("address_line1", 200),
        _text("address_line2", 200),
        _text("pincode", 6),
        sa.Column("state_code", sa.String(length=2), nullable=True),
    ):
        op.add_column("customers", column, schema=SCHEMA)

    for column in (
        sa.Column("round_off_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("seller_legal_name", sa.String(length=200), nullable=True),
        sa.Column("seller_address", sa.Text(), nullable=True),
        sa.Column("buyer_name", sa.String(length=200), nullable=True),
        sa.Column("buyer_address", sa.Text(), nullable=True),
        sa.Column("buyer_state_code", sa.String(length=2), nullable=True),
        sa.Column("ship_to_address", sa.Text(), nullable=True),
    ):
        op.add_column("invoices", column, schema=SCHEMA)

    for column in (
        sa.Column("unit", sa.String(length=3), nullable=True),
        sa.Column("discount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("item_sku", sa.String(length=60), nullable=True),
    ):
        op.add_column("invoice_lines", column, schema=SCHEMA)

    op.create_table(
        "supplier_profiles",
        sa.Column("pan", sa.String(length=10), nullable=True),
        _text("bank_account_name", 200),
        sa.Column("bank_account_number_enc", sa.LargeBinary(), nullable=True),
        _text("bank_account_number_masked", 40),
        _text("bank_ifsc", 11),
        _text("bank_name", 120),
        _text("bank_branch", 120),
        _text("upi_id", 100),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_profiles")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_finance_supplier_profiles_organization_id"),
        "supplier_profiles",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_supplier_profiles_org",
        "supplier_profiles",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Row-level security, the same policy shape every tenant-scoped table in this schema gets.
    op.execute(f"ALTER TABLE {SCHEMA}.supplier_profiles ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.supplier_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.supplier_profiles
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.supplier_profiles")
    op.drop_index(
        "uq_supplier_profiles_org",
        table_name="supplier_profiles",
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        op.f("ix_finance_supplier_profiles_organization_id"),
        table_name="supplier_profiles",
        schema=SCHEMA,
    )
    op.drop_table("supplier_profiles", schema=SCHEMA)

    for name in ("item_sku", "item_id", "discount_minor", "unit"):
        op.drop_column("invoice_lines", name, schema=SCHEMA)
    for name in (
        "ship_to_address",
        "buyer_state_code",
        "buyer_address",
        "buyer_name",
        "seller_address",
        "seller_legal_name",
        "round_off_minor",
    ):
        op.drop_column("invoices", name, schema=SCHEMA)
    for name in ("state_code", "pincode", "address_line2", "address_line1"):
        op.drop_column("customers", name, schema=SCHEMA)
    for name in ("pincode", "city", "address_line2", "address_line1"):
        op.drop_column("gst_registrations", name, schema=SCHEMA)
