"""initial itsm schema

The IT service desk and CMDB: assets, incidents (with an append-only worklog thread), the
service catalog and the requests raised against it, plus the platform tables every service has
(audit_logs, outbox, processed_events). Row-level security is enabled in the following
migration, once the tables exist.

Revision ID: f9d8b1c1351b
Revises:
Create Date: 2026-08-24 23:32:22.715615
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f9d8b1c1351b'  # pragma: allowlist secret
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "itsm"


def upgrade() -> None:
    # --- platform tables ---------------------------------------------------------------
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('acting_as', sa.String(length=120), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity', sa.String(length=80), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('ip', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs')),
        schema=SCHEMA,
    )
    op.create_index('ix_audit_logs_org_action', 'audit_logs', ['organization_id', 'action'], unique=False, schema=SCHEMA)
    op.create_index('ix_audit_logs_org_actor', 'audit_logs', ['organization_id', 'actor_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_audit_logs_org_at', 'audit_logs', ['organization_id', sa.literal_column('at DESC')], unique=False, schema=SCHEMA)
    op.create_index('ix_audit_logs_org_entity', 'audit_logs', ['organization_id', 'entity', 'entity_id'], unique=False, schema=SCHEMA)

    op.create_table(
        'outbox',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=120), nullable=False),
        sa.Column('event_version', sa.Integer(), nullable=False),
        sa.Column('aggregate_type', sa.String(length=80), nullable=False),
        sa.Column('aggregate_id', sa.UUID(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('causation_id', sa.UUID(), nullable=True),
        sa.Column('actor', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_outbox_created_at'), 'outbox', ['created_at'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_itsm_outbox_event_type'), 'outbox', ['event_type'], unique=False, schema=SCHEMA)

    op.create_table(
        'processed_events',
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=120), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('event_id', name=op.f('pk_processed_events')),
        schema=SCHEMA,
    )

    # --- assets (the CMDB) -------------------------------------------------------------
    op.create_table(
        'assets',
        sa.Column('tag', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('asset_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=True),
        sa.Column('owner', sa.String(length=120), nullable=True),
        sa.Column('location', sa.String(length=160), nullable=False),
        sa.Column('serial', sa.String(length=120), nullable=False),
        sa.Column('purchased_at', sa.Date(), nullable=True),
        sa.Column('warranty_expires_at', sa.Date(), nullable=True),
        sa.Column('cost_minor', sa.BigInteger(), nullable=False),
        sa.Column('cost_currency', sa.String(length=3), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assets')),
        sa.UniqueConstraint('organization_id', 'tag', name='uq_assets_org_tag'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_assets_organization_id'), 'assets', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_assets_org_status', 'assets', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_assets_org_type', 'assets', ['organization_id', 'asset_type'], unique=False, schema=SCHEMA)
    op.create_index('ix_assets_org_owner', 'assets', ['organization_id', 'owner_id'], unique=False, schema=SCHEMA)

    # --- incidents ---------------------------------------------------------------------
    op.create_table(
        'incidents',
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('summary', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('requester_id', sa.UUID(), nullable=True),
        sa.Column('requester', sa.String(length=120), nullable=False),
        sa.Column('assignee_id', sa.UUID(), nullable=True),
        sa.Column('assignee', sa.String(length=120), nullable=False),
        sa.Column('affected_asset_id', sa.UUID(), nullable=True),
        sa.Column('sla_state', sa.String(length=20), nullable=False),
        sa.Column('sla_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sla_breached', sa.Boolean(), nullable=False),
        sa.Column('sla_minutes_remaining', sa.Integer(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['affected_asset_id'], [f'{SCHEMA}.assets.id'],
            name=op.f('fk_incidents_affected_asset_id_assets'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents')),
        sa.UniqueConstraint('organization_id', 'number', name='uq_incidents_org_number'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_incidents_organization_id'), 'incidents', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_status', 'incidents', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_priority', 'incidents', ['organization_id', 'priority'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_category', 'incidents', ['organization_id', 'category'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_asset', 'incidents', ['organization_id', 'affected_asset_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_requester', 'incidents', ['organization_id', 'requester_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_incidents_org_updated', 'incidents', ['organization_id', 'updated_at'], unique=False, schema=SCHEMA)

    # --- incident worklogs (append-only) -----------------------------------------------
    op.create_table(
        'incident_worklogs',
        sa.Column('incident_id', sa.UUID(), nullable=False),
        sa.Column('author', sa.String(length=120), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['incident_id'], [f'{SCHEMA}.incidents.id'],
            name=op.f('fk_incident_worklogs_incident_id_incidents'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_worklogs')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_incident_worklogs_organization_id'), 'incident_worklogs', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_incident_worklogs_org_incident', 'incident_worklogs', ['organization_id', 'incident_id', 'at'], unique=False, schema=SCHEMA)

    # --- catalog items -----------------------------------------------------------------
    op.create_table(
        'catalog_items',
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('sla_days', sa.Integer(), nullable=False),
        sa.Column('requests_30d', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_catalog_items')),
        sa.UniqueConstraint('organization_id', 'code', name='uq_catalog_items_org_code'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_catalog_items_organization_id'), 'catalog_items', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_catalog_items_org_category', 'catalog_items', ['organization_id', 'category'], unique=False, schema=SCHEMA)

    # --- service requests --------------------------------------------------------------
    op.create_table(
        'requests',
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('catalog_item_code', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('requester_id', sa.UUID(), nullable=True),
        sa.Column('requester', sa.String(length=120), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('assignee', sa.String(length=120), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_requests')),
        sa.UniqueConstraint('organization_id', 'number', name='uq_requests_org_number'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_itsm_requests_organization_id'), 'requests', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_requests_org_status', 'requests', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_requests_org_catalog', 'requests', ['organization_id', 'catalog_item_code'], unique=False, schema=SCHEMA)
    op.create_index('ix_requests_org_requester', 'requests', ['organization_id', 'requester_id'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('requests', schema=SCHEMA)
    op.drop_table('catalog_items', schema=SCHEMA)
    op.drop_table('incident_worklogs', schema=SCHEMA)
    op.drop_table('incidents', schema=SCHEMA)
    op.drop_table('assets', schema=SCHEMA)
    op.drop_table('processed_events', schema=SCHEMA)
    op.drop_table('outbox', schema=SCHEMA)
    op.drop_table('audit_logs', schema=SCHEMA)
