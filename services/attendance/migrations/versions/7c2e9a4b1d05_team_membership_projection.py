"""team membership projection — the read model behind TEAM scope

Attendance has never answered a TEAM-scoped read: a manager's reports live in employee-service
(`employees.reporting_manager_id`), this service may not read that schema, and a synchronous call
on the read path was rejected. `team_membership` is the copy attendance keeps instead — one row
per employee, the manager they report to now, fed by the `hrms.people.employee.*` events and by
`python -m app.backfill` for organizations that had employees before this table existed
(`app/models.py::TeamMembership` carries the reasoning).

Additive only: one new table, one partial index, no change to any existing table, so the old code
runs safely against this schema for the length of a deploy. Until the table is populated a
TEAM-scoped caller matches **nobody** (an inner join on an empty table), which is the safe way to
be wrong — and the answer such a caller got before this migration was a 403.

**Policied in the same migration that creates the table**, with `FORCE`: a business table with no
policy for even one deploy is a table row-level security is not protecting yet.
`attendance.current_org()` already exists (`a1b2c3d4e5f6`).

Revision ID: 7c2e9a4b1d05
Revises: f4c1a9b3d2e7
Create Date: 2026-09-24 09:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e9a4b1d05"  # pragma: allowlist secret
down_revision: str | None = "f4c1a9b3d2e7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"
TABLE = "team_membership"
APP_ROLE = "opsgrids_attendance"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("manager_employee_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "manager_employee_id is null or manager_employee_id <> employee_id",
            name=op.f("ck_team_membership_manager_not_self"),
        ),
        sa.CheckConstraint(
            "source_version >= 0", name=op.f("ck_team_membership_source_version_non_negative")
        ),
        sa.CheckConstraint(
            "status in ('probation','active','on_notice','exited')",
            name=op.f("ck_team_membership_status"),
        ),
        sa.PrimaryKeyConstraint("organization_id", "employee_id", name=op.f("pk_team_membership")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_team_membership_org_manager",
        TABLE,
        ["organization_id", "manager_employee_id", "employee_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status <> 'exited'"),
    )

    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{TABLE}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.drop_index(
        "ix_team_membership_org_manager",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("status <> 'exited'"),
    )
    op.drop_table(TABLE, schema=SCHEMA)

