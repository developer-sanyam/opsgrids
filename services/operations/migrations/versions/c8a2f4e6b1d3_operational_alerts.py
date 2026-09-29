"""operational alerts, fulfilment_settings SLA and contact columns

Fulfilment plan §4; F12 — operational automation.

1. `fulfilment_settings` gains `task_sla_minutes` (default 60 — how long a pick task may stay
   open, or a shipment sit non-terminal, before `app.alerts`'s sweep opens a
   `task_delayed`/`shipment_delayed` alert for it) and `alert_email`/`alert_phone` (nullable — the
   address an alert event's `payload.alert_contact` carries; null until an organization opts in,
   the same "existing org unaffected until it opts in" shape `require_pickup_code` already used).
2. `operational_alerts` — one open row per (kind, subject), opened by the sweep, refreshed while
   the condition holds, closed automatically once it clears (`InventoryDiscrepancy`'s own shape:
   detect and record, never repair). ENABLE + FORCE RLS and the standard `tenant_isolation`
   policy, the same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: c8a2f4e6b1d3
Revises: c9a2f1e6b7d4
Create Date: 2026-09-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8a2f4e6b1d3"  # pragma: allowlist secret
down_revision: str | None = "c9a2f1e6b7d4"  # pragma: allowlist secret
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


def upgrade() -> None:
    # --- fulfilment_settings: SLA and alert contact -------------------------------------------
    op.add_column(
        "fulfilment_settings",
        sa.Column("task_sla_minutes", sa.Integer(), nullable=False, server_default="60"),
        schema=SCHEMA,
    )
    op.alter_column("fulfilment_settings", "task_sla_minutes", server_default=None, schema=SCHEMA)
    op.add_column(
        "fulfilment_settings",
        sa.Column("alert_email", sa.String(length=254), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "fulfilment_settings",
        sa.Column("alert_phone", sa.String(length=20), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_fulfilment_settings_task_sla_minutes_positive"),
        "fulfilment_settings",
        "task_sla_minutes > 0",
        schema=SCHEMA,
    )

    # --- operational_alerts ---------------------------------------------------------------------
    op.create_table(
        "operational_alerts",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('low_stock', 'task_delayed', 'shipment_delayed')",
            name=op.f("ck_operational_alerts_kind_known"),
        ),
        sa.CheckConstraint(
            "subject_type IN ('inventory_item', 'pick_task', 'shipment')",
            name=op.f("ck_operational_alerts_subject_type_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_alerts")),
        schema=SCHEMA,
    )
    _org_index("operational_alerts")
    op.create_index(
        "uq_operational_alerts_open_subject",
        "operational_alerts",
        ["organization_id", "kind", "subject_type", "subject_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_operational_alerts_org_detected",
        "operational_alerts",
        ["organization_id", "detected_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_operational_alerts_org_warehouse",
        "operational_alerts",
        ["organization_id", "warehouse_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_operational_alerts_org_kind_open",
        "operational_alerts",
        ["organization_id", "kind", "resolved_at"],
        schema=SCHEMA,
    )
    _rls("operational_alerts")


def downgrade() -> None:
    _unrls("operational_alerts")
    op.drop_table("operational_alerts", schema=SCHEMA)

    op.drop_constraint(
        op.f("ck_fulfilment_settings_task_sla_minutes_positive"),
        "fulfilment_settings",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("fulfilment_settings", "alert_phone", schema=SCHEMA)
    op.drop_column("fulfilment_settings", "alert_email", schema=SCHEMA)
    op.drop_column("fulfilment_settings", "task_sla_minutes", schema=SCHEMA)
