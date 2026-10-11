"""mrp: runs, the demand they planned from, shortages and their pegging

Inventory plan Phase 7.

1. `mrp_runs` — one planning run: what was asked (`params`), who asked, how it ended
   (`QUEUED` for the worker -> `COMPLETED` | `FAILED`), and a hash of its result.
2. `mrp_demands` — the demand the run planned from, frozen at execution.
3. `mrp_shortages` — one row per item the run could not cover, with the whole netting arithmetic;
   `requisition_request_id` / `requisition_id` are plain UUIDs (purchase requisitions are built
   elsewhere and wired in later), never foreign keys.
4. `mrp_shortage_lines` — pegging: which demand, through which produced parent.

`item_id` and every id that names something outside MRP are plain UUIDs with no foreign key; the
only foreign keys are between MRP's own tables. Every table carries `organization_id` with
ENABLE + FORCE RLS and the standard `tenant_isolation` policy. Idempotency claims reuse
`manufacturing_requests`.

Down: drops all four tables — destructive by design, they are new in this revision.

Revision ID: e7a4c1d9b352
Revises: d3b9f2a7c614
Create Date: 2026-10-05 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a4c1d9b352"  # pragma: allowlist secret
down_revision: str | None = "d3b9f2a7c614"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

TABLES_DROP_ORDER = ("mrp_shortage_lines", "mrp_shortages", "mrp_demands", "mrp_runs")

_SOURCES = "'SALES_ORDER', 'PRODUCTION_ORDER', 'MANUAL'"


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


def _id_org() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{SCHEMA}.{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="CASCADE",
    )


def _ck(table: str, name: str, expr: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expr, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    # --- mrp_runs ------------------------------------------------------------------------------
    op.create_table(
        "mrp_runs",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("execution", sa.String(length=6), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.String(length=300), nullable=False),
        sa.Column("demand_count", sa.Integer(), nullable=False),
        sa.Column("shortage_count", sa.Integer(), nullable=False),
        sa.Column("items_planned", sa.Integer(), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_id_org(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        _ck("mrp_runs", "status_known", "status IN ('QUEUED', 'COMPLETED', 'FAILED')"),
        _ck("mrp_runs", "execution_known", "execution IN ('SYNC', 'WORKER')"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mrp_runs")),
        sa.UniqueConstraint("organization_id", "seq", name="uq_mrp_runs_seq"),
        sa.UniqueConstraint("organization_id", "number", name="uq_mrp_runs_number"),
        schema=SCHEMA,
    )
    _org_index("mrp_runs")
    op.create_index(
        "ix_mrp_runs_org_created", "mrp_runs", ["organization_id", "created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_mrp_runs_queued",
        "mrp_runs",
        ["status", "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    _rls("mrp_runs")

    # --- mrp_demands ---------------------------------------------------------------------------
    op.create_table(
        "mrp_demands",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_ref", sa.String(length=60), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("requested_milli", sa.BigInteger(), nullable=False),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=False),
        *_id_org(),
        _ck("mrp_demands", "source_known", f"source_type IN ({_SOURCES})"),
        _ck(
            "mrp_demands",
            "quantities_consistent",
            "requested_milli > 0 AND reserved_milli >= 0 AND quantity_milli > 0 "
            "AND quantity_milli = requested_milli - reserved_milli",
        ),
        _fk("mrp_demands", "run_id", "mrp_runs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mrp_demands")),
        schema=SCHEMA,
    )
    _org_index("mrp_demands")
    op.create_index(
        "ix_mrp_demands_org_run", "mrp_demands", ["organization_id", "run_id"], schema=SCHEMA
    )
    _rls("mrp_demands")

    # --- mrp_shortages -------------------------------------------------------------------------
    op.create_table(
        "mrp_shortages",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=4), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("gross_milli", sa.BigInteger(), nullable=False),
        sa.Column("on_hand_milli", sa.BigInteger(), nullable=False),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column("open_po_milli", sa.BigInteger(), nullable=False),
        sa.Column("open_production_milli", sa.BigInteger(), nullable=False),
        sa.Column("shortage_milli", sa.BigInteger(), nullable=False),
        sa.Column("need_by", sa.Date(), nullable=True),
        sa.Column("requisition_request_id", sa.UUID(), nullable=True),
        sa.Column("requisition_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requisition_id", sa.UUID(), nullable=True),
        *_id_org(),
        _ck("mrp_shortages", "action_known", "action IN ('BUY', 'MAKE')"),
        _ck(
            "mrp_shortages",
            "quantities_sane",
            "gross_milli > 0 AND shortage_milli > 0 AND on_hand_milli >= 0 AND reserved_milli >= 0 "
            "AND open_po_milli >= 0 AND open_production_milli >= 0 AND level >= 0",
        ),
        _fk("mrp_shortages", "run_id", "mrp_runs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mrp_shortages")),
        sa.UniqueConstraint("organization_id", "run_id", "item_id", name="uq_mrp_shortages_item"),
        schema=SCHEMA,
    )
    _org_index("mrp_shortages")
    op.create_index(
        "ix_mrp_shortages_org_run", "mrp_shortages", ["organization_id", "run_id"], schema=SCHEMA
    )
    _rls("mrp_shortages")

    # --- mrp_shortage_lines --------------------------------------------------------------------
    op.create_table(
        "mrp_shortage_lines",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("shortage_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_ref", sa.String(length=60), nullable=False),
        sa.Column("parent_item_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        *_id_org(),
        _ck("mrp_shortage_lines", "source_known", f"source_type IN ({_SOURCES})"),
        _ck("mrp_shortage_lines", "quantity_positive", "quantity_milli > 0"),
        _fk("mrp_shortage_lines", "run_id", "mrp_runs"),
        _fk("mrp_shortage_lines", "shortage_id", "mrp_shortages"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mrp_shortage_lines")),
        schema=SCHEMA,
    )
    _org_index("mrp_shortage_lines")
    op.create_index(
        "ix_mrp_shortage_lines_org_shortage",
        "mrp_shortage_lines",
        ["organization_id", "shortage_id"],
        schema=SCHEMA,
    )
    _rls("mrp_shortage_lines")


def downgrade() -> None:
    for table in TABLES_DROP_ORDER:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.drop_table(table, schema=SCHEMA)
