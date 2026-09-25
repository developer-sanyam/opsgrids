"""private objects table

ADR-0009 §2's named-but-unbuilt private half, decided in the ADR's Q1 addendum: a bucket with
no anonymous policy, and its own row per object — a payslip, a signed contract, an ID scan.

Table and row-level security land in **one** migration here, unlike `assets`' own two-step
(`e266c48a08c6` then `b4a1f7c92d5e`). That split existed so a policy migration's downgrade
could be exact without touching `CREATE FUNCTION`; `document.current_org()` already exists
from the earlier migration, so there is no function-creation ordering hazard to isolate a
second time, and the two writes belong to the same reviewable unit: a table with no isolation
for even one deploy is a worse failure mode than a slightly larger migration.

Every column this table needs is declared here, at slice 1, including
`ix_private_objects_org_owner_service_created_at` — there is no cross-tenant sweep reading this
table yet (unlike `assets`' purge-sweep index, added in its own later migration once the sweep
existed to read it), so there is nothing to defer.

Revision ID: d7c4a1f0e3b6
Revises: ba3a482fd724
Create Date: 2026-09-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7c4a1f0e3b6'  # pragma: allowlist secret
down_revision: str | None = 'ba3a482fd724'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "document"
APP_ROLE = "opsgrids_document"


def upgrade() -> None:
    op.create_table(
        'private_objects',
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('purpose', sa.String(length=128), nullable=False),
        sa.Column('owner_service', sa.String(length=64), nullable=False),
        sa.Column('owner_aggregate_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('declared_content_type', sa.String(length=255), nullable=False),
        sa.Column('declared_size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('uploaded_by_user_id', sa.UUID(), nullable=True),
        sa.Column('owner_subject_ref', sa.UUID(), nullable=True),
        sa.Column('upload_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'stored')", name=op.f('ck_private_objects_status')),
        sa.CheckConstraint('declared_size_bytes > 0', name=op.f('ck_private_objects_declared_size_bytes_positive')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_private_objects')),
        schema='document',
    )
    op.create_index(
        'ix_private_objects_org_owner_service_created_at',
        'private_objects',
        ['organization_id', 'owner_service', sa.literal_column('created_at DESC')],
        unique=False,
        schema='document',
    )
    op.create_index(
        op.f('ix_document_private_objects_organization_id'),
        'private_objects',
        ['organization_id'],
        unique=False,
        schema='document',
    )

    # Row-level security, same shape as `b4a1f7c92d5e` for `assets`: `ENABLE` and `FORCE`,
    # `USING` and `WITH CHECK`, reusing `document.current_org()` rather than redefining it.
    op.execute(f"ALTER TABLE {SCHEMA}.private_objects ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.private_objects FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.private_objects
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.private_objects")
    op.execute(f"ALTER TABLE {SCHEMA}.private_objects NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.private_objects DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        op.f('ix_document_private_objects_organization_id'),
        table_name='private_objects',
        schema='document',
    )
    op.drop_index(
        'ix_private_objects_org_owner_service_created_at',
        table_name='private_objects',
        schema='document',
    )
    op.drop_table('private_objects', schema='document')
