"""job work: orders, materials, receipts and challans (Inventory plan Phase 6)

Four tables, no foreign key to anything outside this migration's own tables — `supplier_id`,
`item_id`, `lot_id`, `roll_id`, `location_id`, `source_warehouse_id` are plain indexed UUIDs
(lots and rolls ship from another branch; ADR-0000 §10 keeps cross-entity links soft).

* `job_work_orders` — the document and its state machine (`app.jobwork.domain`).
* `job_work_materials` — planned/sent quantities per bucket, and the running received /
  rejected / shortage figures. A CHECK keeps `received + rejected + shortage <= sent`.
* `job_work_receipts` — one row per material per receipt posting. Append-only (UPDATE/DELETE
  revoked from the app role in `env.py`, re-applied after every grant refresh).
* `challans` — the delivery challan. Immutable once issued: a trigger refuses any change to the
  number, order, supplier, goods snapshot or value, and any delete. Only the e-way-bill facts
  and the transport fields that feed them can still change.

Every table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy. Reversible: the
downgrade drops the trigger, the policies and the tables, in dependency order.

Revision ID: d7b3e5a19f62
Revises: see `down_revision` below
Create Date: 2026-10-05 00:28:41.955778
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "d7b3e5a19f62"  # pragma: allowlist secret
down_revision: str | None = "e7a4c1d9b352"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
TABLES = ("job_work_orders", "job_work_materials", "job_work_receipts", "challans")


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


#: Every column that makes a challan *this* challan. The e-way-bill facts, the transport fields
#: that feed them, `updated_at` and `updated_by` are deliberately absent: they are the only
#: things that may change after issue.
_CHALLAN_FROZEN = (
    "id",
    "organization_id",
    "order_id",
    "challan_number",
    "kind",
    "issued_at",
    "supplier_id",
    "supplier_name",
    "process",
    "lines",
    "total_value_minor",
    "currency",
    "move_id",
    "idempotency_key",
    "request_hash",
    "created_at",
    "created_by",
    "deleted_at",
)


def _challan_trigger() -> None:
    changed = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in _CHALLAN_FROZEN)
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.challans_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a delivery challan cannot be deleted (%)', OLD.challan_number
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF {changed} THEN
                RAISE EXCEPTION 'a delivery challan is immutable once issued (%)', OLD.challan_number
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER challans_immutable BEFORE UPDATE OR DELETE ON {SCHEMA}.challans
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.challans_immutable()
        """
    )


def upgrade() -> None:
    op.create_table(
        "job_work_orders",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("process", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("source_warehouse_id", sa.UUID(), nullable=True),
        sa.Column("receive_location_id", sa.UUID(), nullable=True),
        sa.Column("qc_required", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "transition_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'ISSUED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CLOSED', 'CANCELLED')",
            name=op.f("ck_job_work_orders_status_known"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_job_work_orders_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_work_orders")),
        schema="operations",
    )
    op.create_index(
        "ix_job_work_orders_org_due_open",
        "job_work_orders",
        ["organization_id", "due_date"],
        unique=False,
        schema="operations",
        postgresql_where=sa.text("status IN ('ISSUED', 'PARTIALLY_RECEIVED')"),
    )
    op.create_index(
        "ix_job_work_orders_org_status",
        "job_work_orders",
        ["organization_id", "status"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_job_work_orders_org_supplier",
        "job_work_orders",
        ["organization_id", "supplier_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_job_work_orders_organization_id"),
        "job_work_orders",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_job_work_orders_org_idempotency_key",
        "job_work_orders",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_job_work_orders_org_number",
        "job_work_orders",
        ["organization_id", "number"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "challans",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("challan_number", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=False),
        sa.Column("process", sa.String(length=120), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_value_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("vehicle_no", sa.String(length=20), nullable=True),
        sa.Column("transporter_id", sa.String(length=30), nullable=True),
        sa.Column("distance_km", sa.Integer(), nullable=True),
        sa.Column("eway_status", sa.String(length=14), nullable=False),
        sa.Column("eway_bill_no", sa.String(length=20), nullable=True),
        sa.Column("eway_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("move_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
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
            "eway_status IN ('NOT_REQUIRED', 'REQUESTED', 'GENERATED', 'FAILED')",
            name=op.f("ck_challans_eway_status_known"),
        ),
        sa.CheckConstraint("kind IN ('DELIVERY')", name=op.f("ck_challans_kind_known")),
        sa.CheckConstraint(
            "distance_km IS NULL OR distance_km >= 0",
            name=op.f("ck_challans_distance_non_negative"),
        ),
        sa.CheckConstraint("total_value_minor >= 0", name=op.f("ck_challans_value_non_negative")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["operations.job_work_orders.id"],
            name=op.f("fk_challans_order_id_job_work_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_challans")),
        schema="operations",
    )
    op.create_index(
        "ix_challans_org_order",
        "challans",
        ["organization_id", "order_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_challans_organization_id"),
        "challans",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_challans_org_idempotency_key",
        "challans",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_challans_org_number",
        "challans",
        ["organization_id", "challan_number"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "job_work_materials",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("sku", sa.String(length=60), nullable=False),
        sa.Column("uom", sa.String(length=20), nullable=False),
        sa.Column("unit_value_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("sent_milli", sa.BigInteger(), nullable=False),
        sa.Column("received_milli", sa.BigInteger(), nullable=False),
        sa.Column("rejected_milli", sa.BigInteger(), nullable=False),
        sa.Column("shortage_milli", sa.BigInteger(), nullable=False),
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
            "quantity_milli > 0", name=op.f("ck_job_work_materials_quantity_positive")
        ),
        sa.CheckConstraint(
            "received_milli + rejected_milli + shortage_milli <= sent_milli",
            name=op.f("ck_job_work_materials_accounted_within_sent"),
        ),
        sa.CheckConstraint(
            "sent_milli = 0 OR sent_milli = quantity_milli",
            name=op.f("ck_job_work_materials_sent_is_planned"),
        ),
        sa.CheckConstraint(
            "sent_milli >= 0 AND received_milli >= 0 AND rejected_milli >= 0 AND shortage_milli >= 0",
            name=op.f("ck_job_work_materials_figures_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["operations.job_work_orders.id"],
            name=op.f("fk_job_work_materials_order_id_job_work_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_work_materials")),
        schema="operations",
    )
    op.create_index(
        "ix_job_work_materials_org_item",
        "job_work_materials",
        ["organization_id", "item_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_job_work_materials_organization_id"),
        "job_work_materials",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_job_work_materials_bucket",
        "job_work_materials",
        ["organization_id", "order_id", "item_id", "location_id", "lot_id", "roll_id"],
        unique=True,
        schema="operations",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_job_work_materials_line",
        "job_work_materials",
        ["organization_id", "order_id", "line_no"],
        unique=True,
        schema="operations",
    )
    op.create_table(
        "job_work_receipts",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column("receipt_no", sa.String(length=40), nullable=False),
        sa.Column("received_milli", sa.BigInteger(), nullable=False),
        sa.Column("rejected_milli", sa.BigInteger(), nullable=False),
        sa.Column("shortage_milli", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("good_status", sa.String(length=20), nullable=False),
        sa.Column("qc_handoff", sa.Boolean(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("return_move_id", sa.UUID(), nullable=True),
        sa.Column("shortage_move_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "received_milli + rejected_milli + shortage_milli > 0",
            name=op.f("ck_job_work_receipts_moves_something"),
        ),
        sa.CheckConstraint(
            "received_milli >= 0 AND rejected_milli >= 0 AND shortage_milli >= 0",
            name=op.f("ck_job_work_receipts_figures_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["operations.job_work_materials.id"],
            name=op.f("fk_job_work_receipts_material_id_job_work_materials"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["operations.job_work_orders.id"],
            name=op.f("fk_job_work_receipts_order_id_job_work_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_work_receipts")),
        schema="operations",
    )
    op.create_index(
        "ix_job_work_receipts_org_material",
        "job_work_receipts",
        ["organization_id", "material_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "ix_job_work_receipts_org_order",
        "job_work_receipts",
        ["organization_id", "order_id", "created_at"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        op.f("ix_operations_job_work_receipts_organization_id"),
        "job_work_receipts",
        ["organization_id"],
        unique=False,
        schema="operations",
    )
    op.create_index(
        "uq_job_work_receipts_org_key_material",
        "job_work_receipts",
        ["organization_id", "idempotency_key", "material_id"],
        unique=True,
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    for table in TABLES:
        _rls(table)
    _challan_trigger()


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS challans_immutable ON {SCHEMA}.challans")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.challans_immutable()")
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
    op.drop_index(
        "uq_job_work_receipts_org_key_material",
        table_name="job_work_receipts",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_job_work_receipts_organization_id"),
        table_name="job_work_receipts",
        schema="operations",
    )
    op.drop_index(
        "ix_job_work_receipts_org_order", table_name="job_work_receipts", schema="operations"
    )
    op.drop_index(
        "ix_job_work_receipts_org_material", table_name="job_work_receipts", schema="operations"
    )
    op.drop_table("job_work_receipts", schema="operations")
    op.drop_index(
        "uq_job_work_materials_line", table_name="job_work_materials", schema="operations"
    )
    op.drop_index(
        "uq_job_work_materials_bucket",
        table_name="job_work_materials",
        schema="operations",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        op.f("ix_operations_job_work_materials_organization_id"),
        table_name="job_work_materials",
        schema="operations",
    )
    op.drop_index(
        "ix_job_work_materials_org_item", table_name="job_work_materials", schema="operations"
    )
    op.drop_table("job_work_materials", schema="operations")
    op.drop_index("uq_challans_org_number", table_name="challans", schema="operations")
    op.drop_index(
        "uq_challans_org_idempotency_key",
        table_name="challans",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_challans_organization_id"), table_name="challans", schema="operations"
    )
    op.drop_index("ix_challans_org_order", table_name="challans", schema="operations")
    op.drop_table("challans", schema="operations")
    op.drop_index(
        "uq_job_work_orders_org_number", table_name="job_work_orders", schema="operations"
    )
    op.drop_index(
        "uq_job_work_orders_org_idempotency_key",
        table_name="job_work_orders",
        schema="operations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_operations_job_work_orders_organization_id"),
        table_name="job_work_orders",
        schema="operations",
    )
    op.drop_index(
        "ix_job_work_orders_org_supplier", table_name="job_work_orders", schema="operations"
    )
    op.drop_index(
        "ix_job_work_orders_org_status", table_name="job_work_orders", schema="operations"
    )
    op.drop_index(
        "ix_job_work_orders_org_due_open",
        table_name="job_work_orders",
        schema="operations",
        postgresql_where=sa.text("status IN ('ISSUED', 'PARTIALLY_RECEIVED')"),
    )
    op.drop_table("job_work_orders", schema="operations")
