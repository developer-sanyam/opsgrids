"""manufacturing: BOMs, production orders, WIP, outputs, wastage, genealogy

Inventory plan Phase 5 (ADR-0014 §7).

1. `boms`, `bom_versions`, `bom_components`, `bom_substitutes` — versioned multi-level BOMs. A
   trigger makes a version immutable once it leaves `DRAFT`: its own row (bar the
   PUBLISHED -> RETIRED step) and every component and substitute under it.
2. `production_orders`, `production_materials`, `production_picks` — the order, the BOM lines as
   the order needs them, and the stock buckets each line was reserved/issued from.
3. `production_outputs`, `production_wastage`, `production_genealogy` — append-only facts
   (UPDATE/DELETE revoked from the app role; re-applied by `migrations/env.py` after every grant
   re-application).
4. `wastage_thresholds`, `manufacturing_requests` (idempotency claims).

`item_id`, `location_id`, `lot_id`, `roll_id` columns are plain indexed-where-it-matters UUIDs with
no foreign key: lots and rolls do not exist yet in this lineage, and an item/location is checked
in the service, filtered by organization. Every tenant table gets ENABLE + FORCE RLS and the
standard `tenant_isolation` policy.

Revision ID: d3b9f2a7c614
Revises: see `down_revision` below
Create Date: 2026-10-05 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b9f2a7c614"  # pragma: allowlist secret
down_revision: str | None = "a3c5e8f1b290"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

APPEND_ONLY = ("production_outputs", "production_wastage", "production_genealogy")
TABLES_DROP_ORDER = (
    "manufacturing_requests",
    "wastage_thresholds",
    "production_genealogy",
    "production_wastage",
    "production_outputs",
    "production_picks",
    "production_materials",
    "production_orders",
    "bom_substitutes",
    "bom_components",
    "bom_versions",
    "boms",
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


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_{SCHEMA}_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def _now() -> sa.TextClause:
    return sa.text("now()")


def _ts_columns(*, soft_delete: bool = False) -> list[sa.Column]:
    cols = [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    ]
    if soft_delete:
        cols.append(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    return cols


def _id_org() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
    ]


def _append_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{SCHEMA}.{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _ck(table: str, name: str, expr: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expr, name=op.f(f"ck_{table}_{name}"))


_NON_NEG = (
    "reserved_milli >= 0 AND issued_milli >= 0 AND consumed_milli >= 0 "
    "AND returned_milli >= 0 AND wasted_milli >= 0"
)
_WIP = "issued_milli - consumed_milli - returned_milli - wasted_milli >= 0"
_WASTAGE = "'CUTTING', 'DEFECT', 'SPILLAGE', 'DAMAGE', 'PROCESS_LOSS', 'LEFTOVER', 'OTHER'"


def upgrade() -> None:
    # --- boms ----------------------------------------------------------------------------------
    op.create_table(
        "boms",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_id_org(),
        *_ts_columns(soft_delete=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boms")),
        schema=SCHEMA,
    )
    _org_index("boms")
    op.create_index(
        "uq_boms_org_code",
        "boms",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_boms_org_item",
        "boms",
        ["organization_id", "item_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _rls("boms")

    # --- bom_versions --------------------------------------------------------------------------
    op.create_table(
        "bom_versions",
        sa.Column("bom_id", sa.UUID(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("output_quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        *_id_org(),
        *_ts_columns(),
        _ck("bom_versions", "status_known", "status IN ('DRAFT', 'PUBLISHED', 'RETIRED')"),
        _ck("bom_versions", "output_positive", "output_quantity_milli > 0"),
        _ck(
            "bom_versions",
            "window_ordered",
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
        ),
        _fk("bom_versions", "bom_id", "boms"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bom_versions")),
        sa.UniqueConstraint(
            "organization_id", "bom_id", "version_no", name="uq_bom_versions_number"
        ),
        schema=SCHEMA,
    )
    _org_index("bom_versions")
    op.create_index(
        "uq_bom_versions_one_draft",
        "bom_versions",
        ["organization_id", "bom_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "ix_bom_versions_org_bom", "bom_versions", ["organization_id", "bom_id"], schema=SCHEMA
    )
    _rls("bom_versions")

    # --- bom_components ------------------------------------------------------------------------
    op.create_table(
        "bom_components",
        sa.Column("bom_version_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("component_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("scrap_bps", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(length=300), nullable=False),
        *_id_org(),
        *_ts_columns(),
        _ck("bom_components", "quantity_positive", "quantity_milli > 0"),
        _ck("bom_components", "scrap_in_range", "scrap_bps >= 0 AND scrap_bps <= 10000"),
        _ck(
            "bom_components",
            "window_ordered",
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
        ),
        _fk("bom_components", "bom_version_id", "bom_versions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bom_components")),
        sa.UniqueConstraint(
            "organization_id", "bom_version_id", "line_no", name="uq_bom_components_line"
        ),
        schema=SCHEMA,
    )
    _org_index("bom_components")
    op.create_index(
        "ix_bom_components_org_version",
        "bom_components",
        ["organization_id", "bom_version_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bom_components_org_item",
        "bom_components",
        ["organization_id", "component_item_id"],
        schema=SCHEMA,
    )
    _rls("bom_components")

    # --- bom_substitutes -----------------------------------------------------------------------
    op.create_table(
        "bom_substitutes",
        sa.Column("bom_component_id", sa.UUID(), nullable=False),
        sa.Column("substitute_item_id", sa.UUID(), nullable=False),
        sa.Column("ratio_milli", sa.BigInteger(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        *_id_org(),
        *_ts_columns(),
        _ck("bom_substitutes", "ratio_positive", "ratio_milli > 0"),
        _fk("bom_substitutes", "bom_component_id", "bom_components"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bom_substitutes")),
        sa.UniqueConstraint(
            "organization_id",
            "bom_component_id",
            "substitute_item_id",
            name="uq_bom_substitutes_item",
        ),
        schema=SCHEMA,
    )
    _org_index("bom_substitutes")
    op.create_index(
        "ix_bom_substitutes_org_component",
        "bom_substitutes",
        ["organization_id", "bom_component_id"],
        schema=SCHEMA,
    )
    _rls("bom_substitutes")

    # --- immutability: nothing under a non-draft version may change --------------------------
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.guard_bom_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'BOM version % is % and cannot be deleted', OLD.id, OLD.status
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                RETURN NEW;
            END IF;
            IF (NEW.organization_id, NEW.bom_id, NEW.version_no, NEW.output_quantity_milli,
                NEW.effective_from, NEW.effective_to, NEW.notes, NEW.published_at,
                NEW.published_by, NEW.content_hash)
               IS DISTINCT FROM
               (OLD.organization_id, OLD.bom_id, OLD.version_no, OLD.output_quantity_milli,
                OLD.effective_from, OLD.effective_to, OLD.notes, OLD.published_at,
                OLD.published_by, OLD.content_hash) THEN
                RAISE EXCEPTION 'BOM version % is % and is immutable', OLD.id, OLD.status
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status
               AND NOT (OLD.status = 'PUBLISHED' AND NEW.status = 'RETIRED') THEN
                RAISE EXCEPTION 'BOM version % cannot move % -> %', OLD.id, OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER guard_bom_version BEFORE UPDATE OR DELETE ON {SCHEMA}.bom_versions
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_bom_version()
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.guard_bom_component() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            version_id uuid;
            version_status text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                version_id := NEW.bom_version_id;
            ELSE
                version_id := OLD.bom_version_id;
            END IF;
            SELECT status INTO version_status FROM {SCHEMA}.bom_versions WHERE id = version_id;
            IF version_status IS NOT NULL AND version_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'BOM version % is % - its components are immutable',
                    version_id, version_status USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.bom_version_id <> OLD.bom_version_id THEN
                RAISE EXCEPTION 'A BOM component cannot move between versions'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER guard_bom_component BEFORE INSERT OR UPDATE OR DELETE
            ON {SCHEMA}.bom_components
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_bom_component()
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.guard_bom_substitute() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            component_id uuid;
            version_status text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                component_id := NEW.bom_component_id;
            ELSE
                component_id := OLD.bom_component_id;
            END IF;
            SELECT v.status INTO version_status
              FROM {SCHEMA}.bom_components c
              JOIN {SCHEMA}.bom_versions v ON v.id = c.bom_version_id
             WHERE c.id = component_id;
            IF version_status IS NOT NULL AND version_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'BOM version is % - its substitutes are immutable', version_status
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER guard_bom_substitute BEFORE INSERT OR UPDATE OR DELETE
            ON {SCHEMA}.bom_substitutes
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_bom_substitute()
        """
    )

    # --- production_orders ---------------------------------------------------------------------
    op.create_table(
        "production_orders",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("bom_id", sa.UUID(), nullable=False),
        sa.Column("bom_version_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("planned_quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("produced_quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("output_location_id", sa.UUID(), nullable=False),
        sa.Column("wip_location_id", sa.UUID(), nullable=True),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("resolved_on", sa.Date(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        *_id_org(),
        *_ts_columns(soft_delete=True),
        _ck(
            "production_orders",
            "status_known",
            "status IN ('DRAFT', 'APPROVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
        ),
        _ck("production_orders", "planned_positive", "planned_quantity_milli > 0"),
        _ck("production_orders", "produced_non_negative", "produced_quantity_milli >= 0"),
        _fk("production_orders", "bom_id", "boms"),
        _fk("production_orders", "bom_version_id", "bom_versions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_orders")),
        sa.UniqueConstraint("organization_id", "seq", name="uq_production_orders_seq"),
        sa.UniqueConstraint("organization_id", "number", name="uq_production_orders_number"),
        schema=SCHEMA,
    )
    _org_index("production_orders")
    op.create_index(
        "ix_production_orders_org_status",
        "production_orders",
        ["organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_production_orders_org_item",
        "production_orders",
        ["organization_id", "item_id"],
        schema=SCHEMA,
    )
    _rls("production_orders")

    # --- production_materials --------------------------------------------------------------------
    op.create_table(
        "production_materials",
        sa.Column("production_order_id", sa.UUID(), nullable=False),
        sa.Column("bom_component_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("component_item_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("bom_quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("output_milli", sa.BigInteger(), nullable=False),
        sa.Column("scrap_bps", sa.Integer(), nullable=False),
        sa.Column("ratio_milli", sa.BigInteger(), nullable=False),
        sa.Column("discrete", sa.Boolean(), nullable=False),
        sa.Column("required_milli", sa.BigInteger(), nullable=False),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column("issued_milli", sa.BigInteger(), nullable=False),
        sa.Column("consumed_milli", sa.BigInteger(), nullable=False),
        sa.Column("returned_milli", sa.BigInteger(), nullable=False),
        sa.Column("wasted_milli", sa.BigInteger(), nullable=False),
        sa.Column("threshold_alerted", sa.Boolean(), nullable=False),
        *_id_org(),
        *_ts_columns(),
        _ck("production_materials", "required_non_negative", "required_milli >= 0"),
        _ck("production_materials", "quantities_non_negative", _NON_NEG),
        _ck("production_materials", "wip_non_negative", _WIP),
        _fk("production_materials", "production_order_id", "production_orders"),
        _fk("production_materials", "bom_component_id", "bom_components"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_materials")),
        sa.UniqueConstraint(
            "organization_id",
            "production_order_id",
            "line_no",
            name="uq_production_materials_line",
        ),
        schema=SCHEMA,
    )
    _org_index("production_materials")
    op.create_index(
        "ix_production_materials_org_order",
        "production_materials",
        ["organization_id", "production_order_id"],
        schema=SCHEMA,
    )
    _rls("production_materials")

    # --- production_picks ------------------------------------------------------------------------
    op.create_table(
        "production_picks",
        sa.Column("production_order_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("source_location_id", sa.UUID(), nullable=False),
        sa.Column("wip_location_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column("issued_milli", sa.BigInteger(), nullable=False),
        sa.Column("consumed_milli", sa.BigInteger(), nullable=False),
        sa.Column("returned_milli", sa.BigInteger(), nullable=False),
        sa.Column("wasted_milli", sa.BigInteger(), nullable=False),
        *_id_org(),
        *_ts_columns(),
        _ck("production_picks", "quantities_non_negative", _NON_NEG),
        _ck("production_picks", "wip_non_negative", _WIP),
        _fk("production_picks", "production_order_id", "production_orders"),
        _fk("production_picks", "material_id", "production_materials"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_picks")),
        schema=SCHEMA,
    )
    _org_index("production_picks")
    op.create_index(
        "uq_production_picks_bucket",
        "production_picks",
        [
            "organization_id",
            "material_id",
            "source_location_id",
            "lot_id",
            "roll_id",
        ],
        unique=True,
        schema=SCHEMA,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_production_picks_org_order",
        "production_picks",
        ["organization_id", "production_order_id"],
        schema=SCHEMA,
    )
    _rls("production_picks")

    # --- production_outputs (append-only) --------------------------------------------------------
    op.create_table(
        "production_outputs",
        sa.Column("production_order_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("consume_move_id", sa.UUID(), nullable=True),
        sa.Column("receipt_move_id", sa.UUID(), nullable=True),
        *_append_columns(),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        *_id_org(),
        _ck("production_outputs", "quantity_positive", "quantity_milli > 0"),
        _ck("production_outputs", "status_known", "status IN ('AVAILABLE', 'QUALITY_HOLD')"),
        _fk("production_outputs", "production_order_id", "production_orders"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_outputs")),
        schema=SCHEMA,
    )
    _org_index("production_outputs")
    op.create_index(
        "ix_production_outputs_org_order",
        "production_outputs",
        ["organization_id", "production_order_id"],
        schema=SCHEMA,
    )
    _rls("production_outputs")

    # --- production_wastage (append-only) --------------------------------------------------------
    op.create_table(
        "production_wastage",
        sa.Column("production_order_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column("pick_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("wastage_type", sa.String(length=14), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("move_id", sa.UUID(), nullable=True),
        *_append_columns(),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        *_id_org(),
        _ck("production_wastage", "quantity_positive", "quantity_milli > 0"),
        _ck("production_wastage", "type_known", f"wastage_type IN ({_WASTAGE})"),
        _fk("production_wastage", "production_order_id", "production_orders"),
        _fk("production_wastage", "material_id", "production_materials"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_wastage")),
        schema=SCHEMA,
    )
    _org_index("production_wastage")
    op.create_index(
        "ix_production_wastage_org_order",
        "production_wastage",
        ["organization_id", "production_order_id"],
        schema=SCHEMA,
    )
    _rls("production_wastage")

    # --- production_genealogy (append-only) ------------------------------------------------------
    op.create_table(
        "production_genealogy",
        sa.Column("production_order_id", sa.UUID(), nullable=False),
        sa.Column("output_id", sa.UUID(), nullable=False),
        sa.Column("parent_item_id", sa.UUID(), nullable=False),
        sa.Column("parent_lot_id", sa.UUID(), nullable=True),
        sa.Column("parent_roll_id", sa.UUID(), nullable=True),
        sa.Column("child_item_id", sa.UUID(), nullable=False),
        sa.Column("child_lot_id", sa.UUID(), nullable=True),
        sa.Column("child_roll_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        *_append_columns(),
        *_id_org(),
        _ck("production_genealogy", "quantity_positive", "quantity_milli > 0"),
        _fk("production_genealogy", "production_order_id", "production_orders"),
        _fk("production_genealogy", "output_id", "production_outputs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_genealogy")),
        schema=SCHEMA,
    )
    _org_index("production_genealogy")
    op.create_index(
        "ix_production_genealogy_org_order",
        "production_genealogy",
        ["organization_id", "production_order_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_production_genealogy_org_output",
        "production_genealogy",
        ["organization_id", "output_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_production_genealogy_org_parent_lot",
        "production_genealogy",
        ["organization_id", "parent_lot_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("parent_lot_id IS NOT NULL"),
    )
    op.create_index(
        "ix_production_genealogy_org_child_lot",
        "production_genealogy",
        ["organization_id", "child_lot_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("child_lot_id IS NOT NULL"),
    )
    _rls("production_genealogy")

    # --- wastage_thresholds ----------------------------------------------------------------------
    op.create_table(
        "wastage_thresholds",
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("wastage_type", sa.String(length=14), nullable=True),
        sa.Column("max_bps", sa.Integer(), nullable=False),
        *_id_org(),
        *_ts_columns(soft_delete=True),
        _ck("wastage_thresholds", "max_in_range", "max_bps >= 0 AND max_bps <= 10000"),
        _ck(
            "wastage_thresholds",
            "type_known",
            f"wastage_type IS NULL OR wastage_type IN ({_WASTAGE})",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wastage_thresholds")),
        schema=SCHEMA,
    )
    _org_index("wastage_thresholds")
    op.create_index(
        "uq_wastage_thresholds_scope",
        "wastage_thresholds",
        ["organization_id", "item_id", "wastage_type"],
        unique=True,
        schema=SCHEMA,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _rls("wastage_thresholds")

    # --- manufacturing_requests ------------------------------------------------------------------
    op.create_table(
        "manufacturing_requests",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_id", sa.UUID(), nullable=True),
        *_append_columns(),
        *_id_org(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manufacturing_requests")),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_manufacturing_requests_key"
        ),
        schema=SCHEMA,
    )
    _org_index("manufacturing_requests")
    _rls("manufacturing_requests")

    for table in APPEND_ONLY:
        op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{table} FROM "{APP_ROLE}"')


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.{table} TO "{APP_ROLE}"')
    for trigger, table in (
        ("guard_bom_substitute", "bom_substitutes"),
        ("guard_bom_component", "bom_components"),
        ("guard_bom_version", "bom_versions"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {SCHEMA}.{table}")
    for function in ("guard_bom_substitute", "guard_bom_component", "guard_bom_version"):
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{function}()")
    for table in TABLES_DROP_ORDER:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.drop_table(table, schema=SCHEMA)
