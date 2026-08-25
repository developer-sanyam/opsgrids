"""timesheet domain tables

PulseBoard's weekly time grid: timesheets (one person's week — status and snapshotted
capacity) and timesheet_entries (the recorded slots, integer minutes). The platform tables
(audit_logs, outbox, processed_events) were created by the initial migration; row-level
security is enabled in the following migration, once these tables exist.

Revision ID: a7c31f80d942
Revises: 043a262fa594
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c31f80d942"  # pragma: allowlist secret
down_revision: str | None = "043a262fa594"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "timesheet"


def _tenant_columns() -> list[sa.Column]:
    """The `TenantBase` tail every business table carries, in autogenerate order."""
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
    # --- timesheets (one person's week) ------------------------------------------------
    op.create_table(
        "timesheets",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("person_ref", sa.String(length=40), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("capacity_minutes", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timesheets")),
        sa.UniqueConstraint(
            "organization_id", "person_ref", "week_start", name="uq_timesheets_org_person_week"
        ),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_timesheet_timesheets_organization_id"), "timesheets", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_timesheets_org_week", "timesheets", ["organization_id", "week_start"], unique=False, schema=SCHEMA)
    op.create_index("ix_timesheets_org_person", "timesheets", ["organization_id", "person_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_timesheets_org_status", "timesheets", ["organization_id", "status"], unique=False, schema=SCHEMA)

    # --- timesheet_entries (the recorded slots) ----------------------------------------
    op.create_table(
        "timesheet_entries",
        sa.Column("timesheet_id", sa.UUID(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("project_ref", sa.String(length=40), nullable=True),
        sa.Column("entered_minutes", sa.Integer(), nullable=False),
        sa.Column("billable_minutes", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "billable_minutes >= 0 AND entered_minutes >= 0 "
            "AND billable_minutes <= entered_minutes",
            name=op.f("ck_timesheet_entries_billable_within_entered"),
        ),
        sa.ForeignKeyConstraint(
            ["timesheet_id"], [f"{SCHEMA}.timesheets.id"],
            name=op.f("fk_timesheet_entries_timesheet_id_timesheets"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timesheet_entries")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_timesheet_timesheet_entries_organization_id"), "timesheet_entries", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_timesheet_entries_org_sheet", "timesheet_entries", ["organization_id", "timesheet_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_timesheet_entries_org_date", "timesheet_entries", ["organization_id", "work_date"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("timesheet_entries", schema=SCHEMA)
    op.drop_table("timesheets", schema=SCHEMA)
