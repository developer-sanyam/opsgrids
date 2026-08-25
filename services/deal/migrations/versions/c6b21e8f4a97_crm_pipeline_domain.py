"""crm pipeline domain

The CRM's pipeline: deals moving through the six stages, and the append-only activity
timeline. Builds on the scaffold revision, which already created the platform tables
(audit_logs, outbox, processed_events). Row-level security — and the trigger that makes
the timeline append-only — is enabled in the following migration, once the tables exist.

Revision ID: c6b21e8f4a97
Revises: b55a9cdb778b
Create Date: 2026-08-25 09:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c6b21e8f4a97'  # pragma: allowlist secret
down_revision: str | None = 'b55a9cdb778b'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "deal"


def upgrade() -> None:
    # --- deals ---------------------------------------------------------------------------
    op.create_table(
        'deals',
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('primary_contact_id', sa.UUID(), nullable=True),
        sa.Column('value_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('stage', sa.String(length=20), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('probability', sa.Integer(), nullable=False),
        sa.Column('close_date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(length=60), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_deals')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_deal_deals_organization_id'), 'deals', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_deals_org_stage', 'deals', ['organization_id', 'stage'], unique=False, schema=SCHEMA)
    op.create_index('ix_deals_org_owner', 'deals', ['organization_id', 'owner_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_deals_org_company', 'deals', ['organization_id', 'company_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_deals_org_close', 'deals', ['organization_id', 'close_date'], unique=False, schema=SCHEMA)

    # --- activities (append-only) --------------------------------------------------------
    op.create_table(
        'activities',
        sa.Column('activity_type', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('deal_id', sa.UUID(), nullable=True),
        sa.Column('company_id', sa.UUID(), nullable=True),
        sa.Column('contact_id', sa.UUID(), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['deal_id'], [f'{SCHEMA}.deals.id'],
            name=op.f('fk_activities_deal_id_deals'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_activities')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_deal_activities_organization_id'), 'activities', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_activities_org_deal', 'activities', ['organization_id', 'deal_id', 'occurred_at'], unique=False, schema=SCHEMA)
    op.create_index('ix_activities_org_contact', 'activities', ['organization_id', 'contact_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_activities_org_company', 'activities', ['organization_id', 'company_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_activities_org_occurred', 'activities', ['organization_id', 'occurred_at'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('activities', schema=SCHEMA)
    op.drop_table('deals', schema=SCHEMA)
