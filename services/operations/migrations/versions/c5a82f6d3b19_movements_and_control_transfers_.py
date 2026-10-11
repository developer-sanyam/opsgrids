"""movements and control: transfers, adjustment requests, reservation expiry

Inventory Phase 3 (ADR-0014 §6, docs/inventory/movements.md). One migration, reversible.

1. `inventory_control_settings` — one row per organization: the quantity thresholds above which a
   transfer or an adjustment is routed through the approval service (ADR-0006), and the document
   number counters. No row means no threshold.
2. `stock_transfers`, `stock_transfer_lines`, `stock_transfer_transitions` — the inter-warehouse
   transfer document (DRAFT -> REQUESTED -> APPROVED -> IN_TRANSIT -> RECEIVED, or CANCELLED /
   REJECTED). The transitions table is append-only.
3. `adjustment_requests` — every adjustment made with a reason and evidence, applied at once or
   held for approval.
4. `inventory_reservations` gains `expires_at` / `expired_at` / `note`, and the indexes the
   expiry sweep and the reservation list need.

Every new table carries `organization_id` with ENABLE + FORCE RLS and the standard
`tenant_isolation` policy; foreign keys stay inside this schema, and lot / roll references are
the organization-scoped composite keys Phase 1 introduced. Nothing here touches existing rows:
the reservation columns are nullable or defaulted.

Downgrade drops the new tables and columns. It **refuses** while any transfer holds stock in
transit (a dispatched transfer not yet fully received) rather than stranding `IN_TRANSIT`
balances that nothing could then receive.

Revision ID: c5a82f6d3b19
Revises: c5e91a7d3b28
Create Date: 2026-10-05 01:02:23.519079
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "c5a82f6d3b19"  # pragma: allowlist secret
down_revision: str | None = "c5e91a7d3b28"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

#: Written out here, not imported from `app.models`: a migration is a snapshot of the vocabulary at
#: this revision and must not move when the model's does.
TRANSFER_STATUS_SQL = (
    "'DRAFT', 'REQUESTED', 'APPROVED', 'IN_TRANSIT', 'RECEIVED', 'CANCELLED', 'REJECTED'"
)
STOCK_STATUS_SQL = (
    "'AVAILABLE', 'QUALITY_HOLD', 'QUARANTINE', 'DAMAGED', 'REJECTED', 'WIP', "
    "'IN_TRANSIT', 'AT_JOB_WORKER', 'BLOCKED'"
)

NEW_TABLES = (
    "inventory_control_settings",
    "stock_transfers",
    "stock_transfer_lines",
    "stock_transfer_transitions",
    "adjustment_requests",
)


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
        "inventory_control_settings",
        sa.Column("transfer_approval_threshold_milli", sa.BigInteger(), nullable=True),
        sa.Column("adjustment_approval_threshold_milli", sa.BigInteger(), nullable=True),
        sa.Column("next_transfer_no", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "next_adjustment_no", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "adjustment_approval_threshold_milli IS NULL "
            "OR adjustment_approval_threshold_milli > 0",
            name=op.f("ck_inventory_control_settings_adjustment_threshold_positive"),
        ),
        sa.CheckConstraint(
            "next_transfer_no >= 1 AND next_adjustment_no >= 1",
            name=op.f("ck_inventory_control_settings_counters_positive"),
        ),
        sa.CheckConstraint(
            "transfer_approval_threshold_milli IS NULL OR transfer_approval_threshold_milli > 0",
            name=op.f("ck_inventory_control_settings_transfer_threshold_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_control_settings")),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_inventory_control_settings_organization_id"),
        "inventory_control_settings",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_inventory_control_settings_org",
        "inventory_control_settings",
        ["organization_id"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "stock_transfers",
        sa.Column("number", sa.String(length=30), nullable=False),
        sa.Column("from_warehouse_id", sa.UUID(), nullable=False),
        sa.Column("to_warehouse_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("approval_route", sa.String(length=8), nullable=True),
        sa.Column("approval_request_id", sa.UUID(), nullable=True),
        sa.Column("approver_name", sa.String(length=200), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("requested_by_employee_id", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=500), nullable=False),
        sa.Column("dispatched_by", sa.String(length=64), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.String(length=64), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
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
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "approval_route IS NULL OR approval_route IN ('AUTO', 'DIRECT', 'SERVICE')",
            name=op.f("ck_stock_transfers_approval_route_known"),
        ),
        sa.CheckConstraint(
            f"status IN ({TRANSFER_STATUS_SQL})",
            name=op.f("ck_stock_transfers_status_known"),
        ),
        sa.CheckConstraint(
            "from_warehouse_id <> to_warehouse_id",
            name=op.f("ck_stock_transfers_warehouses_differ"),
        ),
        sa.ForeignKeyConstraint(
            ["from_warehouse_id"],
            ["operations.warehouses.id"],
            name=op.f("fk_stock_transfers_from_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id"],
            ["operations.warehouses.id"],
            name=op.f("fk_stock_transfers_to_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfers")),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_stock_transfers_organization_id"),
        "stock_transfers",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_stock_transfers_org_approval_request",
        "stock_transfers",
        ["organization_id", "approval_request_id"],
        unique=False,
        schema="operations",
        postgresql_where=sa.text("approval_request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_stock_transfers_org_created",
        "stock_transfers",
        ["organization_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_stock_transfers_org_status",
        "stock_transfers",
        ["organization_id", "status"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_stock_transfers_org_idempotency_key",
        "stock_transfers",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_stock_transfers_org_number",
        "stock_transfers",
        ["organization_id", "number"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "stock_transfer_transitions",
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.String(length=12), nullable=True),
        sa.Column("to_status", sa.String(length=12), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            f"to_status IN ({TRANSFER_STATUS_SQL})",
            name=op.f("ck_stock_transfer_transitions_to_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["operations.stock_transfers.id"],
            name=op.f("fk_stock_transfer_transitions_transfer_id_stock_transfers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfer_transitions")),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_stock_transfer_transitions_organization_id"),
        "stock_transfer_transitions",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_stock_transfer_transitions_org_transfer",
        "stock_transfer_transitions",
        ["organization_id", "transfer_id", "created_at"],
        unique=False,
        schema="operations",
    )
    op.create_table(
        "adjustment_requests",
        sa.Column("number", sa.String(length=30), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("stock_status", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("quantity_delta_milli", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("evidence_document_id", sa.UUID(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=True),
        sa.Column("approver_name", sa.String(length=200), nullable=True),
        sa.Column("decided_by_name", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=500), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_by_employee_id", sa.UUID(), nullable=True),
        sa.Column("applied_move_id", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=False),
        sa.Column("cancelled_by", sa.String(length=64), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
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
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('damaged', 'lost', 'found', 'consumed', 'returned', 'other')",
            name=op.f("ck_adjustment_requests_reason_known"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL', 'APPLIED', 'REJECTED', 'CANCELLED', 'FAILED')",
            name=op.f("ck_adjustment_requests_status_known"),
        ),
        sa.CheckConstraint(
            f"stock_status IN ({STOCK_STATUS_SQL})",
            name=op.f("ck_adjustment_requests_stock_status_known"),
        ),
        sa.CheckConstraint(
            "NOT approval_required OR evidence_document_id IS NOT NULL",
            name=op.f("ck_adjustment_requests_approval_needs_evidence"),
        ),
        sa.CheckConstraint(
            "quantity_delta_milli <> 0", name=op.f("ck_adjustment_requests_delta_non_zero")
        ),
        sa.ForeignKeyConstraint(
            ["applied_move_id"],
            ["operations.inventory_moves.id"],
            name=op.f("fk_adjustment_requests_applied_move_id_inventory_moves"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["operations.inventory_items.id"],
            name=op.f("fk_adjustment_requests_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["operations.locations.id"],
            name=op.f("fk_adjustment_requests_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_adjustment_requests_org_lot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_adjustment_requests_org_roll",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustment_requests")),
        schema="operations",
    )
    op.create_index(
        "ix_adjustment_requests_org_approval_request",
        "adjustment_requests",
        ["organization_id", "approval_request_id"],
        unique=False,
        schema="operations",
        postgresql_where=sa.text("approval_request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_adjustment_requests_org_created",
        "adjustment_requests",
        ["organization_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_adjustment_requests_org_status",
        "adjustment_requests",
        ["organization_id", "status"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_adjustment_requests_organization_id"),
        "adjustment_requests",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_adjustment_requests_org_idempotency_key",
        "adjustment_requests",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_adjustment_requests_org_number",
        "adjustment_requests",
        ["organization_id", "number"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "stock_transfer_lines",
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("from_location_id", sa.UUID(), nullable=False),
        sa.Column("in_transit_location_id", sa.UUID(), nullable=True),
        sa.Column("to_location_id", sa.UUID(), nullable=True),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("dispatched_milli", sa.BigInteger(), nullable=False),
        sa.Column("received_milli", sa.BigInteger(), nullable=False),
        sa.Column("shortage_milli", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "dispatched_milli >= 0 AND received_milli >= 0 AND shortage_milli >= 0 "
            "AND dispatched_milli <= quantity_milli "
            "AND received_milli + shortage_milli <= dispatched_milli",
            name=op.f("ck_stock_transfer_lines_quantities_within"),
        ),
        sa.CheckConstraint(
            "quantity_milli > 0", name=op.f("ck_stock_transfer_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["from_location_id"],
            ["operations.locations.id"],
            name=op.f("fk_stock_transfer_lines_from_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["in_transit_location_id"],
            ["operations.locations.id"],
            name=op.f("fk_stock_transfer_lines_in_transit_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["operations.inventory_items.id"],
            name=op.f("fk_stock_transfer_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_stock_transfer_lines_org_lot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_stock_transfer_lines_org_roll",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"],
            ["operations.locations.id"],
            name=op.f("fk_stock_transfer_lines_to_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["operations.stock_transfers.id"],
            name=op.f("fk_stock_transfer_lines_transfer_id_stock_transfers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfer_lines")),
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_stock_transfer_lines_organization_id"),
        "stock_transfer_lines",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_stock_transfer_lines_org_item",
        "stock_transfer_lines",
        ["organization_id", "inventory_item_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_stock_transfer_lines_org_transfer",
        "stock_transfer_lines",
        ["organization_id", "transfer_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_stock_transfer_lines_transfer_no",
        "stock_transfer_lines",
        ["transfer_id", "line_no"],
        unique=True,
        schema="operations",
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="operations",
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        schema="operations",
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("note", sa.String(length=500), server_default="", nullable=False),
        schema="operations",
    )
    # The two reservation indexes are built CONCURRENTLY by `e1b6d4a8c273`.

    for table in NEW_TABLES:
        _rls(table)


def downgrade() -> None:
    stranded = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT count(*) FROM {SCHEMA}.stock_transfer_lines "  # noqa: S608
                "WHERE dispatched_milli > received_milli + shortage_milli"
            )
        )
        .scalar()
    )
    if stranded:
        raise RuntimeError(
            f"{stranded} transfer line(s) still hold stock in transit; receive or write off "
            "those transfers before downgrading, or their IN_TRANSIT balances would be stranded"
        )
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.stock_transfer_transitions TO "{APP_ROLE}"')
    for table in NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
    op.drop_index(
        "ix_inventory_reservations_org_expires_active",
        table_name="inventory_reservations",
        schema="operations",
        postgresql_where=sa.text("status = 'active' AND expires_at IS NOT NULL"),
        if_exists=True,
    )
    op.drop_index(
        "ix_inventory_reservations_org_created",
        table_name="inventory_reservations",
        schema="operations",
        if_exists=True,
    )
    op.drop_column("inventory_reservations", "note", schema="operations")
    op.drop_column("inventory_reservations", "expired_at", schema="operations")
    op.drop_column("inventory_reservations", "expires_at", schema="operations")
    op.drop_index(
        "uq_stock_transfer_lines_transfer_no",
        table_name="stock_transfer_lines",
        schema="operations",
    )
    op.drop_index(
        "ix_stock_transfer_lines_org_transfer",
        table_name="stock_transfer_lines",
        schema="operations",
    )
    op.drop_index(
        "ix_stock_transfer_lines_org_item", table_name="stock_transfer_lines", schema="operations"
    )
    op.drop_index(
        op.f("ix_operations_stock_transfer_lines_organization_id"),
        table_name="stock_transfer_lines",
        schema="operations",
    )
    op.drop_table("stock_transfer_lines", schema="operations")
    op.drop_index(
        "uq_adjustment_requests_org_number", table_name="adjustment_requests", schema="operations"
    )
    op.drop_index(
        "uq_adjustment_requests_org_idempotency_key",
        table_name="adjustment_requests",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_adjustment_requests_organization_id"),
        table_name="adjustment_requests",
        schema="operations",
    )
    op.drop_index(
        "ix_adjustment_requests_org_status", table_name="adjustment_requests", schema="operations"
    )
    op.drop_index(
        "ix_adjustment_requests_org_created", table_name="adjustment_requests", schema="operations"
    )
    op.drop_index(
        "ix_adjustment_requests_org_approval_request",
        table_name="adjustment_requests",
        schema="operations",
        postgresql_where=sa.text("approval_request_id IS NOT NULL"),
    )
    op.drop_table("adjustment_requests", schema="operations")
    op.drop_index(
        "ix_stock_transfer_transitions_org_transfer",
        table_name="stock_transfer_transitions",
        schema="operations",
    )
    op.drop_index(
        op.f("ix_operations_stock_transfer_transitions_organization_id"),
        table_name="stock_transfer_transitions",
        schema="operations",
    )
    op.drop_table("stock_transfer_transitions", schema="operations")
    op.drop_index(
        "uq_stock_transfers_org_number", table_name="stock_transfers", schema="operations"
    )
    op.drop_index(
        "uq_stock_transfers_org_idempotency_key",
        table_name="stock_transfers",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        "ix_stock_transfers_org_status", table_name="stock_transfers", schema="operations"
    )
    op.drop_index(
        "ix_stock_transfers_org_created", table_name="stock_transfers", schema="operations"
    )
    op.drop_index(
        "ix_stock_transfers_org_approval_request",
        table_name="stock_transfers",
        schema="operations",
        postgresql_where=sa.text("approval_request_id IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_stock_transfers_organization_id"),
        table_name="stock_transfers",
        schema="operations",
    )
    op.drop_table("stock_transfers", schema="operations")
    op.drop_index(
        "uq_inventory_control_settings_org",
        table_name="inventory_control_settings",
        schema="operations",
    )
    op.drop_index(
        op.f("ix_operations_inventory_control_settings_organization_id"),
        table_name="inventory_control_settings",
        schema="operations",
    )
    op.drop_table("inventory_control_settings", schema="operations")
