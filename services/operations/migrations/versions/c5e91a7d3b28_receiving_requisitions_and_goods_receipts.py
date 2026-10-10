"""receiving: purchase requisitions, goods receipts, receipt tolerance

Inventory Phase 2 (docs/inventory/receiving.md).

1. `purchase_requisitions` + `purchase_requisition_lines` — an internal request to buy, with a
   DRAFT -> SUBMITTED -> APPROVED -> CONVERTED state machine (REJECTED / CANCELLED on the side);
   conversion creates a draft purchase order and stores its id.
2. `goods_receipts` + `goods_receipt_lines` — one posted delivery against a purchase order, with the
   ordered / previously-received / cumulative figures and the item's tolerance snapshotted on
   every line. **Immutable**: `UPDATE`/`DELETE` are revoked from the app role here and re-revoked
   in `migrations/env.py` after every grant re-application. `(organization_id, idempotency_key)`
   is unique where set, which is what makes a retried POST a replay.
3. `purchase_order_lines.received_milli` (exact cumulative receipt, milli-units of the stock UOM;
   backfilled from the whole-unit `received_qty`) and `short_closed`.
4. `inventory_items.receipt_tolerance_bp` (basis points above the ordered quantity a receipt may
   reach; 0 = exact, the default for every existing item).

Every new table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy. No
cross-schema foreign keys; the `lot_id` on a receipt line is the same organization-scoped
composite foreign key the ledger uses.

Revision ID: c5e91a7d3b28
Revises: d7a3c9e5b184
Create Date: 2026-10-05 01:02:22.675974
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5e91a7d3b28"  # pragma: allowlist secret
down_revision: str | None = "d7a3c9e5b184"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
NEW_TABLES = (
    "purchase_requisitions",
    "purchase_requisition_lines",
    "goods_receipts",
    "goods_receipt_lines",
)
APPEND_ONLY = ("goods_receipts", "goods_receipt_lines")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def upgrade() -> None:
    op.create_table(
        "goods_receipts",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("purchase_order_id", sa.UUID(), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("delivery_note", sa.String(length=80), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("status IN ('POSTED')", name=op.f("ck_goods_receipts_status_known")),
        sa.CheckConstraint(
            "(idempotency_key IS NULL) = (request_hash IS NULL)",
            name=op.f("ck_goods_receipts_key_matches_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["operations.purchase_orders.id"],
            name=op.f("fk_goods_receipts_purchase_order_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["operations.suppliers.id"],
            name=op.f("fk_goods_receipts_supplier_id_suppliers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goods_receipts")),
        sa.UniqueConstraint("organization_id", "number", name="uq_goods_receipts_org_number"),
        schema="operations",
    )
    op.create_index(
        "ix_goods_receipts_org_created",
        "goods_receipts",
        ["organization_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_goods_receipts_org_po",
        "goods_receipts",
        ["organization_id", "purchase_order_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_goods_receipts_org_supplier",
        "goods_receipts",
        ["organization_id", "supplier_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_goods_receipts_organization_id"),
        "goods_receipts",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_goods_receipts_org_key",
        "goods_receipts",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "purchase_requisitions",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("requested_by_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.String(length=120), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=True),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("converted_po_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "(status = 'CONVERTED') = (converted_po_id IS NOT NULL)",
            name=op.f("ck_purchase_requisitions_converted_matches_po"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'CONVERTED', 'CANCELLED')",
            name=op.f("ck_purchase_requisitions_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["converted_po_id"],
            ["operations.purchase_orders.id"],
            name=op.f("fk_purchase_requisitions_converted_po_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["operations.warehouses.id"],
            name=op.f("fk_purchase_requisitions_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_requisitions")),
        sa.UniqueConstraint(
            "organization_id", "number", name="uq_purchase_requisitions_org_number"
        ),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_purchase_requisitions_organization_id"),
        "purchase_requisitions",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_purchase_requisitions_org_created",
        "purchase_requisitions",
        ["organization_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_purchase_requisitions_org_requester",
        "purchase_requisitions",
        ["organization_id", "requested_by_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_purchase_requisitions_org_status",
        "purchase_requisitions",
        ["organization_id", "status"],
        unique=False,
        schema="operations",
    )
    op.create_table(
        "purchase_requisition_lines",
        sa.Column("requisition_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("suggested_supplier_id", sa.UUID(), nullable=True),
        sa.Column("est_unit_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
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
        sa.CheckConstraint(
            "est_unit_price_minor IS NULL OR est_unit_price_minor >= 0",
            name=op.f("ck_purchase_requisition_lines_price_non_negative"),
        ),
        sa.CheckConstraint(
            "quantity_milli > 0", name=op.f("ck_purchase_requisition_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["operations.inventory_items.id"],
            name=op.f("fk_purchase_requisition_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requisition_id"],
            ["operations.purchase_requisitions.id"],
            name=op.f("fk_purchase_requisition_lines_requisition_id_purchase_requisitions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suggested_supplier_id"],
            ["operations.suppliers.id"],
            name=op.f("fk_purchase_requisition_lines_suggested_supplier_id_suppliers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_requisition_lines")),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_purchase_requisition_lines_organization_id"),
        "purchase_requisition_lines",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_purchase_requisition_lines_org_req",
        "purchase_requisition_lines",
        ["organization_id", "requisition_id", "line_no"],
        unique=False,
        schema="operations",
    )
    op.create_table(
        "goods_receipt_lines",
        sa.Column("goods_receipt_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("purchase_order_line_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=True),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("stock_status", sa.String(length=20), nullable=True),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("supplier_lot_no", sa.String(length=60), nullable=True),
        sa.Column(
            "roll_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("entered_milli", sa.BigInteger(), nullable=False),
        sa.Column("entered_uom", sa.String(length=20), nullable=False),
        sa.Column("received_milli", sa.BigInteger(), nullable=False),
        sa.Column("ordered_milli", sa.BigInteger(), nullable=False),
        sa.Column("prior_received_milli", sa.BigInteger(), nullable=False),
        sa.Column("cumulative_milli", sa.BigInteger(), nullable=False),
        sa.Column("tolerance_bp", sa.Integer(), nullable=False),
        sa.Column("close_short", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("unit_cost_minor", sa.BigInteger(), nullable=True),
        sa.Column("unit_cost_currency", sa.String(length=3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('PARTIAL', 'EXACT', 'SHORT', 'OVER')",
            name=op.f("ck_goods_receipt_lines_outcome_known"),
        ),
        sa.CheckConstraint(
            "entered_milli >= 0", name=op.f("ck_goods_receipt_lines_entered_non_negative")
        ),
        sa.CheckConstraint(
            "received_milli >= 0", name=op.f("ck_goods_receipt_lines_received_non_negative")
        ),
        sa.CheckConstraint(
            "tolerance_bp BETWEEN 0 AND 10000", name=op.f("ck_goods_receipt_lines_tolerance_range")
        ),
        sa.ForeignKeyConstraint(
            ["goods_receipt_id"],
            ["operations.goods_receipts.id"],
            name=op.f("fk_goods_receipt_lines_goods_receipt_id_goods_receipts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["operations.inventory_items.id"],
            name=op.f("fk_goods_receipt_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["operations.locations.id"],
            name=op.f("fk_goods_receipt_lines_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_goods_receipt_lines_org_lot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_line_id"],
            ["operations.purchase_order_lines.id"],
            name=op.f("fk_goods_receipt_lines_purchase_order_line_id_purchase_order_lines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goods_receipt_lines")),
        schema="operations",
    )
    op.create_index(
        "ix_goods_receipt_lines_org_po_line",
        "goods_receipt_lines",
        ["organization_id", "purchase_order_line_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_goods_receipt_lines_org_receipt",
        "goods_receipt_lines",
        ["organization_id", "goods_receipt_id", "line_no"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_goods_receipt_lines_organization_id"),
        "goods_receipt_lines",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.add_column(
        "inventory_items",
        sa.Column("receipt_tolerance_bp", sa.Integer(), server_default="0", nullable=False),
        schema=SCHEMA,
    )
    # The default stays: bulk loaders that insert items directly get "exactly the ordered quantity".
    op.create_check_constraint(
        op.f("ck_inventory_items_receipt_tolerance_bp_range"),
        "inventory_items",
        "receipt_tolerance_bp BETWEEN 0 AND 10000",
        schema="operations",
    )
    op.add_column(
        "purchase_order_lines",
        sa.Column("received_milli", sa.BigInteger(), server_default="0", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "purchase_order_lines",
        sa.Column("short_closed", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=SCHEMA,
    )
    # Existing lines carried whole units only; one unit is 1000 milli-units (ADR-0014 §2).
    op.execute(
        f"UPDATE {SCHEMA}.purchase_order_lines SET received_milli = received_qty::bigint * 1000"  # noqa: S608
    )
    op.alter_column("purchase_order_lines", "received_milli", server_default=None, schema=SCHEMA)
    op.alter_column("purchase_order_lines", "short_closed", server_default=None, schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_purchase_order_lines_received_milli_non_negative"),
        "purchase_order_lines",
        "received_milli >= 0",
        schema="operations",
    )

    for table in NEW_TABLES:
        _rls(table)
    for table in APPEND_ONLY:
        op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{table} FROM "{APP_ROLE}"')


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.{table} TO "{APP_ROLE}"')
    for table in NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
    op.drop_constraint(
        op.f("ck_purchase_order_lines_received_milli_non_negative"),
        "purchase_order_lines",
        schema="operations",
        type_="check",
    )
    op.drop_column("purchase_order_lines", "short_closed", schema="operations")
    op.drop_column("purchase_order_lines", "received_milli", schema="operations")
    op.drop_constraint(
        op.f("ck_inventory_items_receipt_tolerance_bp_range"),
        "inventory_items",
        schema="operations",
        type_="check",
    )
    op.drop_column("inventory_items", "receipt_tolerance_bp", schema="operations")
    op.drop_index(
        op.f("ix_operations_goods_receipt_lines_organization_id"),
        table_name="goods_receipt_lines",
        schema="operations",
    )
    op.drop_index(
        "ix_goods_receipt_lines_org_receipt", table_name="goods_receipt_lines", schema="operations"
    )
    op.drop_index(
        "ix_goods_receipt_lines_org_po_line", table_name="goods_receipt_lines", schema="operations"
    )
    op.drop_table("goods_receipt_lines", schema="operations")
    op.drop_index(
        "ix_purchase_requisition_lines_org_req",
        table_name="purchase_requisition_lines",
        schema="operations",
    )
    op.drop_index(
        op.f("ix_operations_purchase_requisition_lines_organization_id"),
        table_name="purchase_requisition_lines",
        schema="operations",
    )
    op.drop_table("purchase_requisition_lines", schema="operations")
    op.drop_index(
        "ix_purchase_requisitions_org_status",
        table_name="purchase_requisitions",
        schema="operations",
    )
    op.drop_index(
        "ix_purchase_requisitions_org_requester",
        table_name="purchase_requisitions",
        schema="operations",
    )
    op.drop_index(
        "ix_purchase_requisitions_org_created",
        table_name="purchase_requisitions",
        schema="operations",
    )
    op.drop_index(
        op.f("ix_operations_purchase_requisitions_organization_id"),
        table_name="purchase_requisitions",
        schema="operations",
    )
    op.drop_table("purchase_requisitions", schema="operations")
    op.drop_index(
        "uq_goods_receipts_org_key",
        table_name="goods_receipts",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_goods_receipts_organization_id"),
        table_name="goods_receipts",
        schema="operations",
    )
    op.drop_index(
        "ix_goods_receipts_org_supplier", table_name="goods_receipts", schema="operations"
    )
    op.drop_index("ix_goods_receipts_org_po", table_name="goods_receipts", schema="operations")
    op.drop_index("ix_goods_receipts_org_created", table_name="goods_receipts", schema="operations")
    op.drop_table("goods_receipts", schema="operations")
