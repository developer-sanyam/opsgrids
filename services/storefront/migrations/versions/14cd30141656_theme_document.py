"""theme document

ADR-0011 §3's reserved `/api/v1/storefront/theme`. One row per organization — a `StoreDoc`
minus the brand, which ADR-0011 already moved onto `storefronts`. `tokens` and `pages` are
opaque JSONB to this service; the envelope (`theme_key`, `mode`, size) is validated by
`domain.validate_theme_document`, never a section's internal shape.

RLS is enabled, forced and policied in this same migration rather than a separate one, unlike
`c71a5e0f4b82`: that migration policied four tables that already existed together at the point
row-level security was switched on for the service. A single new table added afterwards carries
its own policy from the moment it exists — there is never a window where it holds a live row and
no policy, which a separate follow-up migration would open.

Revision ID: 14cd30141656
Revises: af1b5d14f90d
Create Date: 2026-09-21 20:04:32.767223
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '14cd30141656'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = 'af1b5d14f90d'  # pragma: allowlist secret — an alembic revision id
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"
APP_ROLE = "opsgrids_storefront"
TABLE = "theme_documents"


def upgrade() -> None:
    op.create_table(
        'theme_documents',
        sa.Column('theme_key', sa.String(length=40), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('tokens', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('pages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('trade', 'retail')", name=op.f('ck_theme_documents_mode')),
        sa.CheckConstraint('length(theme_key) > 0', name=op.f('ck_theme_documents_theme_key_not_blank')),
        sa.CheckConstraint('version > 0', name=op.f('ck_theme_documents_version_positive')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_theme_documents')),
        schema=SCHEMA,
    )
    op.create_index(
        op.f('ix_storefront_theme_documents_organization_id'),
        'theme_documents', ['organization_id'], unique=False, schema=SCHEMA,
    )
    # Partial: a soft-deleted document ("Remove this store") must not block a fresh adopt.
    op.create_index(
        'uq_theme_documents_organization_id_live', 'theme_documents', ['organization_id'],
        unique=True, schema=SCHEMA, postgresql_where=sa.text('deleted_at IS NULL'),
    )

    # RLS: ENABLE + FORCE, USING and WITH CHECK both, matching every table `c71a5e0f4b82` set up
    # — `storefront.current_org()` already exists from that migration.
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
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        'uq_theme_documents_organization_id_live',
        table_name='theme_documents', schema=SCHEMA,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.drop_index(
        op.f('ix_storefront_theme_documents_organization_id'),
        table_name='theme_documents', schema=SCHEMA,
    )
    op.drop_table('theme_documents', schema=SCHEMA)
