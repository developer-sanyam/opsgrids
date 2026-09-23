"""scheduled_jobs

ADR-0015: the one business table this service owns. See `app/models.py:ScheduledJob` for the
column-by-column rationale.

Revision ID: 13ef09d48a2b
Revises: 9706c43e423e
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '13ef09d48a2b'  # pragma: allowlist secret
down_revision: str | None = '9706c43e423e'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'scheduled_jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('idempotency_key', sa.String(length=200), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('target_event_type', sa.String(length=120), nullable=False),
        sa.Column('target_aggregate_type', sa.String(length=80), nullable=False),
        sa.Column('target_aggregate_id', sa.UUID(), nullable=False),
        sa.Column('target_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('target_actor', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('requested_event_id', sa.UUID(), nullable=False),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('fired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fired_event_id', sa.UUID(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduled_jobs')),
        sa.UniqueConstraint(
            'organization_id', 'idempotency_key', name='uq_scheduled_jobs_org_idempotency'
        ),
        schema='scheduler',
    )
    op.create_index(
        op.f('ix_scheduler_scheduled_jobs_organization_id'),
        'scheduled_jobs',
        ['organization_id'],
        unique=False,
        schema='scheduler',
    )
    op.create_index(
        'ix_scheduled_jobs_pending_due',
        'scheduled_jobs',
        ['due_at'],
        unique=False,
        schema='scheduler',
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        'ix_scheduled_jobs_fired_at',
        'scheduled_jobs',
        ['fired_at'],
        unique=False,
        schema='scheduler',
        postgresql_where=sa.text("status = 'fired'"),
    )


def downgrade() -> None:
    op.drop_index('ix_scheduled_jobs_fired_at', table_name='scheduled_jobs', schema='scheduler')
    op.drop_index('ix_scheduled_jobs_pending_due', table_name='scheduled_jobs', schema='scheduler')
    op.drop_index(
        op.f('ix_scheduler_scheduled_jobs_organization_id'),
        table_name='scheduled_jobs',
        schema='scheduler',
    )
    op.drop_table('scheduled_jobs', schema='scheduler')
