"""projects domain tables

The delivery portfolio: members (the people), projects, their milestones and the workflow-board
tasks. The platform tables (audit_logs, outbox, processed_events) were created by the initial
migration; row-level security is enabled in the following migration, once these tables exist.

Revision ID: a3f1c9d24e17
Revises: 155157965283
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f1c9d24e17"  # pragma: allowlist secret
down_revision: str | None = "155157965283"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "projects"


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
    # --- members (the delivery-org people) ---------------------------------------------
    op.create_table(
        "members",
        sa.Column("ref", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=False),
        sa.Column("directory_id", sa.UUID(), nullable=True),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_members")),
        sa.UniqueConstraint("organization_id", "ref", name="uq_members_org_ref"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_projects_members_organization_id"), "members", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_members_org_directory", "members", ["organization_id", "directory_id"], unique=False, schema=SCHEMA)

    # --- projects ----------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("health", sa.String(length=20), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("budget_minor", sa.BigInteger(), nullable=False),
        sa.Column("spent_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("team_size", sa.Integer(), nullable=False),
        sa.Column("open_tasks", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"], [f"{SCHEMA}.members.id"],
            name=op.f("fk_projects_owner_id_members"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("organization_id", "code", name="uq_projects_org_code"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_projects_projects_organization_id"), "projects", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_health", "projects", ["organization_id", "health"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_phase", "projects", ["organization_id", "phase"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_owner", "projects", ["organization_id", "owner_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_projects_org_target", "projects", ["organization_id", "target_date"], unique=False, schema=SCHEMA)

    # --- milestones --------------------------------------------------------------------
    op.create_table(
        "milestones",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"], [f"{SCHEMA}.members.id"],
            name=op.f("fk_milestones_owner_id_members"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{SCHEMA}.projects.id"],
            name=op.f("fk_milestones_project_id_projects"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_milestones")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_projects_milestones_organization_id"), "milestones", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_milestones_org_project", "milestones", ["organization_id", "project_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_milestones_org_status", "milestones", ["organization_id", "status"], unique=False, schema=SCHEMA)
    op.create_index("ix_milestones_org_due", "milestones", ["organization_id", "due_date"], unique=False, schema=SCHEMA)
    op.create_index("ix_milestones_org_owner", "milestones", ["organization_id", "owner_id"], unique=False, schema=SCHEMA)

    # --- tasks (the workflow board) ----------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["assignee_id"], [f"{SCHEMA}.members.id"],
            name=op.f("fk_tasks_assignee_id_members"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{SCHEMA}.projects.id"],
            name=op.f("fk_tasks_project_id_projects"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_projects_tasks_organization_id"), "tasks", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_project", "tasks", ["organization_id", "project_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_stage", "tasks", ["organization_id", "stage"], unique=False, schema=SCHEMA)
    op.create_index("ix_tasks_org_assignee", "tasks", ["organization_id", "assignee_id"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("tasks", schema=SCHEMA)
    op.drop_table("milestones", schema=SCHEMA)
    op.drop_table("projects", schema=SCHEMA)
    op.drop_table("members", schema=SCHEMA)
