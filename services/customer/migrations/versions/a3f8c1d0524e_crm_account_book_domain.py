"""crm account book domain

The CRM's account book: the rep directory (owners), companies and contacts. Builds on the
scaffold revision, which already created the platform tables (audit_logs, outbox,
processed_events). Row-level security is enabled in the following migration, once the
tables exist.

Revision ID: a3f8c1d0524e
Revises: 190a9e6257ba
Create Date: 2026-08-25 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3f8c1d0524e'  # pragma: allowlist secret
down_revision: str | None = '190a9e6257ba'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"


def upgrade() -> None:
    # --- owners (the rep directory) ------------------------------------------------------
    op.create_table(
        'owners',
        sa.Column('full_name', sa.String(length=160), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('role', sa.String(length=120), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_owners')),
        sa.UniqueConstraint('organization_id', 'email', name='uq_owners_org_email'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_customer_owners_organization_id'), 'owners', ['organization_id'], unique=False, schema=SCHEMA)

    # --- companies -----------------------------------------------------------------------
    op.create_table(
        'companies',
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('domain', sa.String(length=120), nullable=False),
        sa.Column('industry', sa.String(length=80), nullable=False),
        sa.Column('size_band', sa.String(length=20), nullable=False),
        sa.Column('city', sa.String(length=120), nullable=False),
        sa.Column('country', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('annual_revenue_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_companies')),
        sa.UniqueConstraint('organization_id', 'name', name='uq_companies_org_name'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_customer_companies_organization_id'), 'companies', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_companies_org_status', 'companies', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_companies_org_owner', 'companies', ['organization_id', 'owner_id'], unique=False, schema=SCHEMA)

    # --- contacts ------------------------------------------------------------------------
    op.create_table(
        'contacts',
        sa.Column('full_name', sa.String(length=160), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['company_id'], [f'{SCHEMA}.companies.id'],
            name=op.f('fk_contacts_company_id_companies'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts')),
        sa.UniqueConstraint('organization_id', 'email', name='uq_contacts_org_email'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_customer_contacts_organization_id'), 'contacts', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_contacts_org_company', 'contacts', ['organization_id', 'company_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_contacts_org_status', 'contacts', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_contacts_org_owner', 'contacts', ['organization_id', 'owner_id'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('contacts', schema=SCHEMA)
    op.drop_table('companies', schema=SCHEMA)
    op.drop_table('owners', schema=SCHEMA)
