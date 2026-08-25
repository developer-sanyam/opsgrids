"""project domain tables

PulseBoard's engagements and board: people (the delivery-team roster), projects (the client
engagements) and tasks (the board cards). The platform tables (audit_logs, outbox,
processed_events) were created by the initial migration; row-level security is enabled in the
following migration, once these tables exist.

Revision ID: b6d4a91c25e8
Revises: f9e05243f88c
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6d4a91c25e8"  # pragma: allowlist secret
down_revision: str | None = "f9e05243f88c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "project"


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
    # --- people (the delivery-team roster) ---------------------------------------------
    op.create_table(
        "people",
        sa.Column("ref", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=False),
        sa.Column("capacity_hours", sa.Integer(), nullable=False),
        sa.Column("directory_id", sa.UUID(), nullable=True),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
        sa.UniqueConstraint("organization_id", "ref", name="uq_people_org_ref"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_project_people_organization_id"), "people", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_people_org_directory", "people", ["organization_id", "directory_id"], unique=False, schema=SCHEMA)

    # --- projects (the engagements) ----------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("budget_minor", sa.BigInteger(), nullable=False),
        sa.Column("spent_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("billable", sa.Boolean(), nullable=False),
        sa.Column("open_tasks", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"], [f"{SCHEMA}.people.id"],
            name=op.f("fk_projects_owner_id_people"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("organization_id", "code", name="uq_projects_org_code"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_project_projects_organization_id"), "projects", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_status", "projects", ["organization_id", "status"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_owner", "projects", ["organization_id", "owner_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_due", "projects", ["organization_id", "due_date"], unique=False, schema=SCHEMA)

    # --- tasks (the board cards) -------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("estimate_hours", sa.Integer(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=60)), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["assignee_id"], [f"{SCHEMA}.people.id"],
            name=op.f("fk_tasks_assignee_id_people"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{SCHEMA}.projects.id"],
            name=op.f("fk_tasks_project_id_projects"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("organization_id", "key", name="uq_tasks_org_key"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_project_tasks_organization_id"), "tasks", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_project", "tasks", ["organization_id", "project_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_stage", "tasks", ["organization_id", "stage"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_assignee", "tasks", ["organization_id", "assignee_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_due", "tasks", ["organization_id", "due_date"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("tasks", schema=SCHEMA)
    op.drop_table("projects", schema=SCHEMA)
    op.drop_table("people", schema=SCHEMA)
