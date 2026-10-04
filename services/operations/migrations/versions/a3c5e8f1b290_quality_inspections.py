"""quality: QC templates, inspections and inspection results

Inventory plan Phase 4 (`docs/inventory/quality.md`).

1. `qc_templates` + `qc_template_params` — reusable checklists of measurable parameters.
2. `inspections` — one inspection of a quantity of stock, with the status state machine
   (`app.quality.domain.INSPECTION_TRANSITIONS`) and the ids of the kernel moves that held,
   decided and disposed of that stock.
3. `inspection_results` — the measured values, with the parameter's bounds snapshotted.

`item_id`, `location_id`, `lot_id`, `roll_id`, `template_id` and `supplier_id` are plain indexed
UUIDs, deliberately **not** foreign keys: lots and rolls live in the Phase 1 tables, and
an inspection is a record about stock at a moment that must outlive (and never block) a rename or
retirement of what it points at. The only foreign keys are within this schema's own parent/child
pairs (`template -> params`, `inspection -> results`).

All four tables get ENABLE + FORCE RLS and the standard `tenant_isolation` policy. None is
append-only: an inspection is a workflow row that moves through states. The stock it governs is
still only ever changed through `ledger.post_move`.

Revision ID: a3c5e8f1b290
Revises: see `down_revision` below (the single place to re-parent)
Create Date: 2026-10-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e8f1b290"  # pragma: allowlist secret
down_revision: str | None = "d9a4c6e1b3f7"  # pragma: allowlist secret  # RE-PARENT HERE
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

_KINDS = "'incoming', 'in-process', 'finished'"
_STATUSES = "'PENDING', 'PASSED', 'FAILED', 'HOLD', 'REWORK', 'PARTIAL_PASS'"


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


def _base_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # --- qc_templates -----------------------------------------------------------------------------
    op.create_table(
        "qc_templates",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("create_key", sa.String(length=128), nullable=True),
        sa.Column("create_hash", sa.String(length=64), nullable=True),
        *_base_columns(),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name=op.f("ck_qc_templates_kind_known")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_templates")),
        schema=SCHEMA,
    )
    _org_index("qc_templates")
    op.create_index(
        "uq_qc_templates_org_code",
        "qc_templates",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_qc_templates_org_create_key",
        "qc_templates",
        ["organization_id", "create_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("create_key IS NOT NULL"),
    )
    op.create_index(
        "ix_qc_templates_org_kind", "qc_templates", ["organization_id", "kind"], schema=SCHEMA
    )
    _rls("qc_templates")

    # --- qc_template_params -----------------------------------------------------------------------
    op.create_table(
        "qc_template_params",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("min_milli", sa.BigInteger(), nullable=True),
        sa.Column("max_milli", sa.BigInteger(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        *_base_columns(),
        sa.CheckConstraint(
            "kind IN ('numeric', 'pass_fail')", name=op.f("ck_qc_template_params_kind_known")
        ),
        sa.CheckConstraint(
            "min_milli IS NULL OR max_milli IS NULL OR min_milli <= max_milli",
            name=op.f("ck_qc_template_params_bounds_ordered"),
        ),
        sa.CheckConstraint(
            "kind = 'numeric' OR (min_milli IS NULL AND max_milli IS NULL)",
            name=op.f("ck_qc_template_params_pass_fail_has_no_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            [f"{SCHEMA}.qc_templates.id"],
            name=op.f("fk_qc_template_params_template_id_qc_templates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_template_params")),
        schema=SCHEMA,
    )
    _org_index("qc_template_params")
    op.create_index(
        "uq_qc_template_params_template_name",
        "qc_template_params",
        ["organization_id", "template_id", "name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_qc_template_params_org_template",
        "qc_template_params",
        ["organization_id", "template_id"],
        schema=SCHEMA,
    )
    _rls("qc_template_params")

    # --- inspections ------------------------------------------------------------------------------
    op.create_table(
        "inspections",
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("supplier_id", sa.UUID(), nullable=True),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("accepted_milli", sa.BigInteger(), nullable=True),
        sa.Column("rejected_milli", sa.BigInteger(), nullable=True),
        sa.Column("rejected_bucket", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("hold_move_id", sa.UUID(), nullable=True),
        sa.Column("decision_move_id", sa.UUID(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=False),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column("disposition_move_id", sa.UUID(), nullable=True),
        sa.Column("disposition_note", sa.String(length=500), nullable=False),
        sa.Column("disposition_reference", sa.String(length=80), nullable=False),
        sa.Column("disposed_by", sa.String(length=64), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("create_key", sa.String(length=128), nullable=True),
        sa.Column("create_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_key", sa.String(length=128), nullable=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=True),
        sa.Column("disposition_key", sa.String(length=128), nullable=True),
        sa.Column("disposition_hash", sa.String(length=64), nullable=True),
        *_base_columns(),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name=op.f("ck_inspections_kind_known")),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name=op.f("ck_inspections_status_known")),
        sa.CheckConstraint("quantity_milli > 0", name=op.f("ck_inspections_quantity_positive")),
        sa.CheckConstraint(
            "accepted_milli IS NULL OR (accepted_milli >= 0 AND accepted_milli <= quantity_milli)",
            name=op.f("ck_inspections_accepted_within_quantity"),
        ),
        sa.CheckConstraint(
            "rejected_milli IS NULL OR (rejected_milli >= 0 AND rejected_milli <= quantity_milli)",
            name=op.f("ck_inspections_rejected_within_quantity"),
        ),
        sa.CheckConstraint(
            "rejected_bucket IS NULL OR rejected_bucket IN ('REJECTED', 'QUARANTINE')",
            name=op.f("ck_inspections_rejected_bucket_known"),
        ),
        sa.CheckConstraint(
            "disposition IN ('NONE', 'RETURN_TO_VENDOR', 'SCRAP')",
            name=op.f("ck_inspections_disposition_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inspections")),
        schema=SCHEMA,
    )
    _org_index("inspections")
    for name, cols in (
        ("ix_inspections_org_status", ["organization_id", "status"]),
        ("ix_inspections_org_item", ["organization_id", "item_id"]),
        ("ix_inspections_org_lot", ["organization_id", "lot_id"]),
        ("ix_inspections_org_roll", ["organization_id", "roll_id"]),
        ("ix_inspections_org_location", ["organization_id", "location_id"]),
    ):
        op.create_index(name, "inspections", cols, schema=SCHEMA)
    op.create_index(
        "uq_inspections_org_create_key",
        "inspections",
        ["organization_id", "create_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("create_key IS NOT NULL"),
    )
    _rls("inspections")

    # --- inspection_results -----------------------------------------------------------------------
    op.create_table(
        "inspection_results",
        sa.Column("inspection_id", sa.UUID(), nullable=False),
        sa.Column("param_id", sa.UUID(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("param_name", sa.String(length=80), nullable=False),
        sa.Column("param_kind", sa.String(length=10), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("min_milli", sa.BigInteger(), nullable=True),
        sa.Column("max_milli", sa.BigInteger(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column("value_milli", sa.BigInteger(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=True),
        *_base_columns(),
        sa.CheckConstraint(
            "param_kind IN ('numeric', 'pass_fail')",
            name=op.f("ck_inspection_results_param_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            [f"{SCHEMA}.inspections.id"],
            name=op.f("fk_inspection_results_inspection_id_inspections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inspection_results")),
        schema=SCHEMA,
    )
    _org_index("inspection_results")
    op.create_index(
        "uq_inspection_results_inspection_param",
        "inspection_results",
        ["organization_id", "inspection_id", "param_name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_inspection_results_org_inspection",
        "inspection_results",
        ["organization_id", "inspection_id"],
        schema=SCHEMA,
    )
    _rls("inspection_results")


def downgrade() -> None:
    for table in ("inspection_results", "inspections", "qc_template_params", "qc_templates"):
        _unrls(table)
        op.drop_table(table, schema=SCHEMA)
