"""catalogue: categories, products, product_variants

Gap 3. Three tables, field-for-field against `frontend/product-storefront/src/api/types.ts`
`Category` / `Product` / `ProductVariant` — see `app/models.py` for what deliberately differs
(no `currency` column; `image_path` root-relative rather than the front end's fixture
`image_url`).

`categories.key` and `products.code` are unique **among live rows only** — a partial index,
`WHERE deleted_at IS NULL` — so a merchant can delete "seasonal" and reuse the key later. `
products.category_id` is an in-schema `RESTRICT` foreign key (allowed; only cross-schema keys
are banned): the service layer refuses a delete naming the live-product count first, and the
constraint is the net under that check. `product_variants.product_id` is `CASCADE`: a variant
has no identity a buyer addresses independently of its product.

RLS in the same migration as each table, exactly as `14cd30141656` did for `theme_documents`.

Revision ID: fe3e5aa93a5a
Revises: 14cd30141656
Create Date: 2026-09-21 20:06:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'fe3e5aa93a5a'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = '14cd30141656'  # pragma: allowlist secret — an alembic revision id
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"
APP_ROLE = "opsgrids_storefront"
TABLES = ("categories", "products", "product_variants")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def upgrade() -> None:
    op.create_table(
        'categories',
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('blurb', sa.String(length=200), nullable=True),
        sa.Column('image_path', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("key ~ '^[a-z0-9][a-z0-9-]*$'", name=op.f('ck_categories_key_shape')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
        schema=SCHEMA,
    )
    op.create_index('ix_categories_org_active', 'categories', ['organization_id', 'is_active'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_storefront_categories_organization_id'), 'categories', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('uq_categories_org_key_live', 'categories', ['organization_id', 'key'], unique=True, schema=SCHEMA, postgresql_where=sa.text('deleted_at IS NULL'))

    op.create_table(
        'products',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('brand', sa.String(length=80), nullable=False),
        sa.Column('category_id', sa.UUID(), nullable=False),
        sa.Column('spec', sa.String(length=200), nullable=False),
        sa.Column('unit', sa.String(length=40), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price_min_minor', sa.BigInteger(), nullable=False),
        sa.Column('price_max_minor', sa.BigInteger(), nullable=False),
        sa.Column('mrp_minor', sa.BigInteger(), nullable=True),
        sa.Column('gst_inclusive', sa.Boolean(), nullable=False),
        sa.Column('moq', sa.Integer(), nullable=False),
        sa.Column('moq_unit', sa.String(length=24), nullable=False),
        sa.Column('in_stock', sa.Boolean(), nullable=False),
        sa.Column('lead_time_days', sa.Integer(), nullable=False),
        sa.Column('delivery_eta_minutes', sa.Integer(), nullable=True),
        sa.Column('featured', sa.Boolean(), nullable=False),
        sa.Column('image_path', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name=op.f('ck_products_status')),
        sa.CheckConstraint('lead_time_days >= 0', name=op.f('ck_products_lead_time_not_negative')),
        sa.CheckConstraint('moq >= 1', name=op.f('ck_products_moq_positive')),
        sa.CheckConstraint('mrp_minor IS NULL OR mrp_minor >= price_min_minor', name=op.f('ck_products_mrp_not_below_price')),
        sa.CheckConstraint('price_min_minor >= 0 AND price_max_minor >= price_min_minor', name=op.f('ck_products_price_band_is_coherent')),
        sa.ForeignKeyConstraint(['category_id'], ['storefront.categories.id'], name=op.f('fk_products_category_id_categories'), ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_products')),
        schema=SCHEMA,
    )
    op.create_index('ix_products_org_category', 'products', ['organization_id', 'category_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_products_org_status', 'products', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_storefront_products_organization_id'), 'products', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('uq_products_org_code_live', 'products', ['organization_id', 'code'], unique=True, schema=SCHEMA, postgresql_where=sa.text('deleted_at IS NULL'))

    op.create_table(
        'product_variants',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('label', sa.String(length=40), nullable=False),
        sa.Column('price_min_minor', sa.BigInteger(), nullable=False),
        sa.Column('price_max_minor', sa.BigInteger(), nullable=False),
        sa.Column('mrp_minor', sa.BigInteger(), nullable=True),
        sa.Column('in_stock', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('mrp_minor IS NULL OR mrp_minor >= price_min_minor', name=op.f('ck_product_variants_mrp_not_below_price')),
        sa.CheckConstraint('price_min_minor >= 0 AND price_max_minor >= price_min_minor', name=op.f('ck_product_variants_price_band_is_coherent')),
        sa.ForeignKeyConstraint(['product_id'], ['storefront.products.id'], name=op.f('fk_product_variants_product_id_products'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_product_variants')),
        schema=SCHEMA,
    )
    op.create_index('ix_product_variants_org_product', 'product_variants', ['organization_id', 'product_id'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_storefront_product_variants_organization_id'), 'product_variants', ['organization_id'], unique=False, schema=SCHEMA)

    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(op.f('ix_storefront_product_variants_organization_id'), table_name='product_variants', schema=SCHEMA)
    op.drop_index('ix_product_variants_org_product', table_name='product_variants', schema=SCHEMA)
    op.drop_table('product_variants', schema=SCHEMA)

    op.drop_index('uq_products_org_code_live', table_name='products', schema=SCHEMA, postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_storefront_products_organization_id'), table_name='products', schema=SCHEMA)
    op.drop_index('ix_products_org_status', table_name='products', schema=SCHEMA)
    op.drop_index('ix_products_org_category', table_name='products', schema=SCHEMA)
    op.drop_table('products', schema=SCHEMA)

    op.drop_index('uq_categories_org_key_live', table_name='categories', schema=SCHEMA, postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_storefront_categories_organization_id'), table_name='categories', schema=SCHEMA)
    op.drop_index('ix_categories_org_active', table_name='categories', schema=SCHEMA)
    op.drop_table('categories', schema=SCHEMA)
