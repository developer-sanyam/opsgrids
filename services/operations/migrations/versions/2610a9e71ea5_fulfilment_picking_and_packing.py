"""fulfilment: workers, pick tasks, offers, exceptions, scans, packing

ADR-0024 §7, ADR-0025 §2/§3; `docs/fulfilment/plan.md` F3 — picking and packing.

1. `workers`, `worker_skills`, `worker_zone_access`, `worker_presence`, `worker_position_samples`
   (schema only, no writer yet — the plan marks live position tracking optional/future).
2. `pick_tasks` + `pick_task_lines` (derived from an accepted order's lines, sequenced by the
   source location's `pick_sequence`, F1), `assignment_offers` (ADR-0025 §3's exact row shape,
   with the partial unique index that enforces "at most one live offer per task"),
   `pick_exceptions`, `scan_events` (append-only — joins `APPEND_ONLY_TABLES` in
   `migrations/env.py`).
3. `packages` + `package_lines` + `packaging_materials`.

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy, the
same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: 2610a9e71ea5
Revises: d2185b2fec98
Create Date: 2026-09-28 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2610a9e71ea5"  # pragma: allowlist secret
down_revision: str | None = "d2185b2fec98"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"


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


def _unrls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_{SCHEMA}_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def _ts_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # --- workers --------------------------------------------------------------------------------
    op.create_table(
        "workers",
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_workers_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workers")),
        schema=SCHEMA,
    )
    _org_index("workers")
    op.create_index(
        "uq_workers_org_employee",
        "workers",
        ["organization_id", "employee_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_workers_org_warehouse", "workers", ["organization_id", "warehouse_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_workers_org_active", "workers", ["organization_id", "is_active"], schema=SCHEMA
    )
    _rls("workers")

    # --- worker_skills ----------------------------------------------------------------------------
    op.create_table(
        "worker_skills",
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("skill", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_worker_skills_worker_id_workers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_skills")),
        schema=SCHEMA,
    )
    _org_index("worker_skills")
    op.create_index(
        "uq_worker_skills_org_worker_skill",
        "worker_skills",
        ["organization_id", "worker_id", "skill"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("worker_skills")

    # --- worker_zone_access -----------------------------------------------------------------------
    op.create_table(
        "worker_zone_access",
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_worker_zone_access_worker_id_workers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_worker_zone_access_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_zone_access")),
        schema=SCHEMA,
    )
    _org_index("worker_zone_access")
    op.create_index(
        "uq_worker_zone_access_org_worker_location",
        "worker_zone_access",
        ["organization_id", "worker_id", "location_id"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("worker_zone_access")

    # --- worker_presence --------------------------------------------------------------------------
    op.create_table(
        "worker_presence",
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('online', 'offline')", name=op.f("ck_worker_presence_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_worker_presence_worker_id_workers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_presence")),
        schema=SCHEMA,
    )
    _org_index("worker_presence")
    op.create_index(
        "uq_worker_presence_org_worker",
        "worker_presence",
        ["organization_id", "worker_id"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("worker_presence")

    # --- worker_position_samples (schema only) ------------------------------------------------
    op.create_table(
        "worker_position_samples",
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("lat_micro", sa.BigInteger(), nullable=True),
        sa.Column("lng_micro", sa.BigInteger(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_worker_position_samples_worker_id_workers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_worker_position_samples_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_position_samples")),
        schema=SCHEMA,
    )
    _org_index("worker_position_samples")
    op.create_index(
        "ix_worker_position_samples_org_worker_recorded",
        "worker_position_samples",
        ["organization_id", "worker_id", "recorded_at"],
        schema=SCHEMA,
    )
    _rls("worker_position_samples")

    # --- pick_tasks --------------------------------------------------------------------------------
    op.create_table(
        "pick_tasks",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("zone_location_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("offer_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint("round >= 0", name=op.f("ck_pick_tasks_round_non_negative")),
        sa.CheckConstraint("max_rounds > 0", name=op.f("ck_pick_tasks_max_rounds_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_pick_tasks_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_pick_tasks_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["zone_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_pick_tasks_zone_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pick_tasks")),
        schema=SCHEMA,
    )
    _org_index("pick_tasks")
    op.create_index(
        "ix_pick_tasks_org_order", "pick_tasks", ["organization_id", "order_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_pick_tasks_org_status", "pick_tasks", ["organization_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "ix_pick_tasks_org_warehouse",
        "pick_tasks",
        ["organization_id", "warehouse_id"],
        schema=SCHEMA,
    )
    _rls("pick_tasks")

    # --- pick_task_lines ---------------------------------------------------------------------------
    op.create_table(
        "pick_task_lines",
        sa.Column("pick_task_id", sa.UUID(), nullable=False),
        sa.Column("order_line_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("source_location_id", sa.UUID(), nullable=False),
        sa.Column("destination_location_id", sa.UUID(), nullable=False),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("picked_milli", sa.BigInteger(), nullable=False),
        sa.Column("short_milli", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint("quantity_milli > 0", name=op.f("ck_pick_task_lines_quantity_positive")),
        sa.CheckConstraint(
            "picked_milli >= 0 AND short_milli >= 0 AND picked_milli + short_milli <= quantity_milli",
            name=op.f("ck_pick_task_lines_picked_within"),
        ),
        sa.ForeignKeyConstraint(
            ["pick_task_id"],
            [f"{SCHEMA}.pick_tasks.id"],
            name=op.f("fk_pick_task_lines_pick_task_id_pick_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_line_id"],
            [f"{SCHEMA}.order_lines.id"],
            name=op.f("fk_pick_task_lines_order_line_id_order_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_pick_task_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_pick_task_lines_source_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_pick_task_lines_destination_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            [f"{SCHEMA}.inventory_reservations.id"],
            name=op.f("fk_pick_task_lines_reservation_id_inventory_reservations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pick_task_lines")),
        schema=SCHEMA,
    )
    _org_index("pick_task_lines")
    op.create_index(
        "ix_pick_task_lines_org_task",
        "pick_task_lines",
        ["organization_id", "pick_task_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pick_task_lines_org_order_line",
        "pick_task_lines",
        ["organization_id", "order_line_id"],
        schema=SCHEMA,
    )
    _rls("pick_task_lines")

    # --- assignment_offers -------------------------------------------------------------------------
    op.create_table(
        "assignment_offers",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "offered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "state IN ('OFFERED', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'WITHDRAWN', 'RELEASED')",
            name=op.f("ck_assignment_offers_state_known"),
        ),
        sa.CheckConstraint("round > 0", name=op.f("ck_assignment_offers_round_positive")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            [f"{SCHEMA}.pick_tasks.id"],
            name=op.f("fk_assignment_offers_task_id_pick_tasks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_assignment_offers_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment_offers")),
        schema=SCHEMA,
    )
    _org_index("assignment_offers")
    op.create_index(
        "uq_assignment_offers_org_task_live",
        "assignment_offers",
        ["organization_id", "task_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("state IN ('OFFERED', 'ACCEPTED')"),
    )
    op.create_index(
        "ix_assignment_offers_org_task",
        "assignment_offers",
        ["organization_id", "task_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_assignment_offers_org_worker_state",
        "assignment_offers",
        ["organization_id", "worker_id", "state"],
        schema=SCHEMA,
    )
    _rls("assignment_offers")

    # --- pick_exceptions ---------------------------------------------------------------------------
    op.create_table(
        "pick_exceptions",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("task_line_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("raised_by", sa.String(length=64), nullable=True),
        sa.Column(
            "raised_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('short_pick', 'damaged', 'not_found', 'other')",
            name=op.f("ck_pick_exceptions_reason_known"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')", name=op.f("ck_pick_exceptions_status_known")
        ),
        sa.CheckConstraint("quantity_milli > 0", name=op.f("ck_pick_exceptions_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            [f"{SCHEMA}.pick_tasks.id"],
            name=op.f("fk_pick_exceptions_task_id_pick_tasks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_line_id"],
            [f"{SCHEMA}.pick_task_lines.id"],
            name=op.f("fk_pick_exceptions_task_line_id_pick_task_lines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pick_exceptions")),
        schema=SCHEMA,
    )
    _org_index("pick_exceptions")
    op.create_index(
        "ix_pick_exceptions_org_task",
        "pick_exceptions",
        ["organization_id", "task_id"],
        schema=SCHEMA,
    )
    _rls("pick_exceptions")

    # --- scan_events (append-only) -----------------------------------------------------------------
    op.create_table(
        "scan_events",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("inventory_item_id", sa.UUID(), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            [f"{SCHEMA}.pick_tasks.id"],
            name=op.f("fk_scan_events_task_id_pick_tasks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_scan_events_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_scan_events_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_scan_events_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_events")),
        schema=SCHEMA,
    )
    _org_index("scan_events")
    op.create_index(
        "ix_scan_events_org_task",
        "scan_events",
        ["organization_id", "task_id", "scanned_at"],
        schema=SCHEMA,
    )
    _rls("scan_events")
    # Append-only by privilege (re-revoked in `migrations/env.py` after every grant
    # re-application) — `scan_events` joins `APPEND_ONLY_TABLES` there.
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.scan_events FROM "{APP_ROLE}"')

    # --- packaging_materials -----------------------------------------------------------------------
    op.create_table(
        "packaging_materials",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("length_mm", sa.Integer(), nullable=True),
        sa.Column("width_mm", sa.Integer(), nullable=True),
        sa.Column("height_mm", sa.Integer(), nullable=True),
        sa.Column("max_weight_grams", sa.BigInteger(), nullable=True),
        sa.Column("tare_weight_grams", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packaging_materials")),
        schema=SCHEMA,
    )
    _org_index("packaging_materials")
    op.create_index(
        "uq_packaging_materials_org_code",
        "packaging_materials",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _rls("packaging_materials")

    # --- packages ------------------------------------------------------------------------------------
    op.create_table(
        "packages",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("packaging_material_id", sa.UUID(), nullable=True),
        sa.Column("destination_location_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("weight_grams", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint("status IN ('OPEN', 'PACKED')", name=op.f("ck_packages_status_known")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_packages_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_material_id"],
            [f"{SCHEMA}.packaging_materials.id"],
            name=op.f("fk_packages_packaging_material_id_packaging_materials"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_packages_destination_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packages")),
        schema=SCHEMA,
    )
    _org_index("packages")
    op.create_index(
        "ix_packages_org_order", "packages", ["organization_id", "order_id"], schema=SCHEMA
    )
    _rls("packages")

    # --- package_lines -------------------------------------------------------------------------------
    op.create_table(
        "package_lines",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column("pick_task_line_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint("quantity_milli > 0", name=op.f("ck_package_lines_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["package_id"],
            [f"{SCHEMA}.packages.id"],
            name=op.f("fk_package_lines_package_id_packages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pick_task_line_id"],
            [f"{SCHEMA}.pick_task_lines.id"],
            name=op.f("fk_package_lines_pick_task_line_id_pick_task_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_package_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_lines")),
        schema=SCHEMA,
    )
    _org_index("package_lines")
    op.create_index(
        "ix_package_lines_org_package",
        "package_lines",
        ["organization_id", "package_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_package_lines_org_task_line",
        "package_lines",
        ["organization_id", "pick_task_line_id"],
        schema=SCHEMA,
    )
    _rls("package_lines")


def downgrade() -> None:
    _unrls("package_lines")
    op.drop_table("package_lines", schema=SCHEMA)

    _unrls("packages")
    op.drop_table("packages", schema=SCHEMA)

    _unrls("packaging_materials")
    op.drop_table("packaging_materials", schema=SCHEMA)

    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.scan_events TO "{APP_ROLE}"')
    _unrls("scan_events")
    op.drop_table("scan_events", schema=SCHEMA)

    _unrls("pick_exceptions")
    op.drop_table("pick_exceptions", schema=SCHEMA)

    _unrls("assignment_offers")
    op.drop_table("assignment_offers", schema=SCHEMA)

    _unrls("pick_task_lines")
    op.drop_table("pick_task_lines", schema=SCHEMA)

    _unrls("pick_tasks")
    op.drop_table("pick_tasks", schema=SCHEMA)

    _unrls("worker_position_samples")
    op.drop_table("worker_position_samples", schema=SCHEMA)

    _unrls("worker_presence")
    op.drop_table("worker_presence", schema=SCHEMA)

    _unrls("worker_zone_access")
    op.drop_table("worker_zone_access", schema=SCHEMA)

    _unrls("worker_skills")
    op.drop_table("worker_skills", schema=SCHEMA)

    _unrls("workers")
    op.drop_table("workers", schema=SCHEMA)
