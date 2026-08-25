"""standup domain tables

PulseBoard's daily check-ins: standup_entries — one row per person per day, blockers null when
nothing blocks. The platform tables (audit_logs, outbox, processed_events) were created by the
initial migration; row-level security is enabled in the following migration, once this table
exists.

Revision ID: f3a8c60e91b7
Revises: da2d0c3d19e7
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c60e91b7"  # pragma: allowlist secret
down_revision: str | None = "da2d0c3d19e7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "standup"


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
    op.create_table(
        "standup_entries",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("person_ref", sa.String(length=40), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("yesterday", sa.Text(), nullable=False),
        sa.Column("today", sa.Text(), nullable=False),
        sa.Column("blockers", sa.Text(), nullable=True),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_standup_entries")),
        sa.UniqueConstraint(
            "organization_id",
            "person_ref",
            "entry_date",
            name="uq_standup_entries_org_person_day",
        ),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_standup_standup_entries_organization_id"), "standup_entries", ["organization_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_standup_entries_org_date", "standup_entries", ["organization_id", "entry_date"], unique=False, schema=SCHEMA)
    op.create_index("ix_standup_entries_org_person", "standup_entries", ["organization_id", "person_id"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("standup_entries", schema=SCHEMA)
