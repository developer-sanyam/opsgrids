"""enquiries

Gap 6. This product's order-like flow — a buyer's line list, sent as an enquiry. **Append-only
content**: only `status` ever changes after creation.

`email_masked`/`email_enc` and `phone_masked`/`phone_enc` are the sealed-envelope pair
`services/customer` uses for a contact's phone (`services/customer/app/models.py:157`):
`<field>_masked` is the non-sensitive hint an ordinary read returns, `<field>_enc` is the
AES-256-GCM envelope `platform_core.pii.seal` produces, and no raw contact value is ever
stored in the clear. `name` and `company` are not classified sensitive — a buyer's own words
about their business, matching the front end's form.

Revision ID: c3fcc6691235
Revises: fe3e5aa93a5a
Create Date: 2026-09-21 20:07:30.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3fcc6691235'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = 'fe3e5aa93a5a'  # pragma: allowlist secret — an alembic revision id
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"
APP_ROLE = "opsgrids_storefront"
TABLE = "enquiries"


def upgrade() -> None:
    op.create_table(
        'enquiries',
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('company', sa.String(length=160), nullable=False),
        sa.Column('email_masked', sa.String(length=254), nullable=False),
        sa.Column('email_enc', sa.LargeBinary(), nullable=True),
        sa.Column('phone_masked', sa.String(length=40), nullable=False),
        sa.Column('phone_enc', sa.LargeBinary(), nullable=True),
        sa.Column('gstin', sa.String(length=15), nullable=True),
        sa.Column('requirement', sa.Text(), nullable=False),
        sa.Column('quantity', sa.String(length=40), nullable=False),
        sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('new', 'contacted', 'won', 'lost')", name=op.f('ck_enquiries_status')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_enquiries')),
        schema=SCHEMA,
    )
    op.create_index('ix_enquiries_org_created', 'enquiries', ['organization_id', 'created_at'], unique=False, schema=SCHEMA)
    op.create_index('ix_enquiries_org_status', 'enquiries', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_storefront_enquiries_organization_id'), 'enquiries', ['organization_id'], unique=False, schema=SCHEMA)

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
    op.drop_index(op.f('ix_storefront_enquiries_organization_id'), table_name='enquiries', schema=SCHEMA)
    op.drop_index('ix_enquiries_org_status', table_name='enquiries', schema=SCHEMA)
    op.drop_index('ix_enquiries_org_created', table_name='enquiries', schema=SCHEMA)
    op.drop_table('enquiries', schema=SCHEMA)
