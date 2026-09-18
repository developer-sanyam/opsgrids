"""notification domain schema

Templates, automation rules, and the record of what was sent. The five tables the service
actually reasons about; the initial migration laid down the three every service has.

Two shapes are worth reading twice. `deliveries` is append-only by intent, so it carries no
mutable outcome column an operator could correct — a second attempt is a second row.
`messages.dedupe_key` is unique per organization and nullable, which is exactly the semantics
wanted: a direct send carries no key and is never de-duplicated, while a rule-driven send always
carries one and is.

Revision ID: a1b2c3d4e5f6
Revises: bbb40f8503b3
Create Date: 2026-09-18 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = 'a1b2c3d4e5f6'  # pragma: allowlist secret
down_revision: str | None = 'bbb40f8503b3'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def _tenant_columns() -> list[sa.Column]:
    """The standard row shape — `TenantBase`, written out once rather than five times."""
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        'templates',
        *_tenant_columns(),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_templates')),
        sa.UniqueConstraint('organization_id', 'code', 'channel', name='uq_templates_org_code_chan'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_notification_templates_organization_id'), 'templates', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_templates_org_code', 'templates', ['organization_id', 'code'], unique=False, schema=SCHEMA)

    op.create_table(
        'rules',
        *_tenant_columns(),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('event_type', sa.String(length=120), nullable=False),
        sa.Column('template_code', sa.String(length=80), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('recipient_path', sa.String(length=120), nullable=True),
        sa.Column('recipient_literal', sa.String(length=254), nullable=True),
        sa.Column('dedupe_key_path', sa.String(length=120), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rules')),
        sa.UniqueConstraint('organization_id', 'code', name='uq_rules_org_code'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_notification_rules_organization_id'), 'rules', ['organization_id'], unique=False, schema=SCHEMA)
    # The consumer's hot path: "which rules watch this event type?", asked once per event.
    op.create_index('ix_rules_org_event', 'rules', ['organization_id', 'event_type', 'active'], unique=False, schema=SCHEMA)

    op.create_table(
        'messages',
        *_tenant_columns(),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('to_address', sa.String(length=254), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('rule_code', sa.String(length=80), nullable=True),
        sa.Column('event_type', sa.String(length=120), nullable=True),
        sa.Column('source_event_id', sa.UUID(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=200), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_message_id', sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
        # Nullable and unique together: a direct send carries no key and is never
        # de-duplicated; a rule-driven send always carries one and is.
        sa.UniqueConstraint('organization_id', 'dedupe_key', name='uq_messages_org_dedupe'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_notification_messages_organization_id'), 'messages', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_messages_org_state', 'messages', ['organization_id', 'state', 'created_at'], unique=False, schema=SCHEMA)
    op.create_index('ix_messages_org_channel', 'messages', ['organization_id', 'channel'], unique=False, schema=SCHEMA)

    op.create_table(
        'deliveries',
        *_tenant_columns(),
        sa.Column('message_id', sa.UUID(), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        # Inside the schema, so allowed: only cross-schema foreign keys are banned.
        sa.ForeignKeyConstraint(['message_id'], [f'{SCHEMA}.messages.id'], name=op.f('fk_deliveries_message_id_messages'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_deliveries')),
        sa.UniqueConstraint('message_id', 'attempt_no', name='uq_deliveries_message_attempt'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_notification_deliveries_organization_id'), 'deliveries', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_deliveries_org_message', 'deliveries', ['organization_id', 'message_id'], unique=False, schema=SCHEMA)

    op.create_table(
        'preferences',
        *_tenant_columns(),
        sa.Column('recipient', sa.String(length=254), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('suppressed', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_preferences')),
        sa.UniqueConstraint('organization_id', 'recipient', 'channel', 'category', name='uq_preferences_org_recipient'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_notification_preferences_organization_id'), 'preferences', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_preferences_org_recipient', 'preferences', ['organization_id', 'recipient', 'channel'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    # Children before parents: `deliveries` holds the only foreign key in the schema.
    for table in ('preferences', 'deliveries', 'messages', 'rules', 'templates'):
        op.drop_table(table, schema=SCHEMA)
