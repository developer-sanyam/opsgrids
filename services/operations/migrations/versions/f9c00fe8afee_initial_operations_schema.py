"""initial operations schema

Procurement and inventory: suppliers, warehouses, purchase orders (with their line items, which
foot to the derived order total), and inventory on hand, plus the platform tables every service
has (audit_logs, outbox, processed_events). Row-level security is enabled in the following
migration, once the tables exist.

Revision ID: f9c00fe8afee
Revises:
Create Date: 2026-08-24 23:32:14.803797
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f9c00fe8afee'  # pragma: allowlist secret
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


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
    op.create_index(op.f('ix_operations_outbox_created_at'), 'outbox', ['created_at'], unique=False, schema=SCHEMA)
    op.create_index(op.f('ix_operations_outbox_event_type'), 'outbox', ['event_type'], unique=False, schema=SCHEMA)

    op.create_table(
        'processed_events',
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=120), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('event_id', name=op.f('pk_processed_events')),
        schema=SCHEMA,
    )

    # --- suppliers ---------------------------------------------------------------------
    op.create_table(
        'suppliers',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('lead_time_days', sa.Integer(), nullable=False),
        sa.Column('on_time_pct', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('spend_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('open_pos', sa.Integer(), nullable=False),
        sa.Column('contact_name', sa.String(length=120), nullable=False),
        sa.Column('contact_email', sa.String(length=200), nullable=False),
        sa.Column('location', sa.String(length=160), nullable=False),
        sa.Column('since', sa.Date(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_suppliers')),
        sa.UniqueConstraint('organization_id', 'code', name='uq_suppliers_org_code'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_operations_suppliers_organization_id'), 'suppliers', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_suppliers_org_status', 'suppliers', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_suppliers_org_category', 'suppliers', ['organization_id', 'category'], unique=False, schema=SCHEMA)

    # --- warehouses --------------------------------------------------------------------
    op.create_table(
        'warehouses',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('location', sa.String(length=160), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_warehouses')),
        sa.UniqueConstraint('organization_id', 'code', name='uq_warehouses_org_code'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_operations_warehouses_organization_id'), 'warehouses', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_warehouses_org_name', 'warehouses', ['organization_id', 'name'], unique=False, schema=SCHEMA)

    # --- purchase orders ---------------------------------------------------------------
    op.create_table(
        'purchase_orders',
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('supplier_name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('warehouse', sa.String(length=120), nullable=False),
        sa.Column('raised_by', sa.String(length=120), nullable=False),
        sa.Column('raised_by_id', sa.UUID(), nullable=True),
        sa.Column('created_date', sa.Date(), nullable=False),
        sa.Column('expected_date', sa.Date(), nullable=False),
        sa.Column('received_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['supplier_id'], [f'{SCHEMA}.suppliers.id'],
            name=op.f('fk_purchase_orders_supplier_id_suppliers'), ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_orders')),
        sa.UniqueConstraint('organization_id', 'number', name='uq_purchase_orders_org_number'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_operations_purchase_orders_organization_id'), 'purchase_orders', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_orders_org_status', 'purchase_orders', ['organization_id', 'status'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_orders_org_supplier', 'purchase_orders', ['organization_id', 'supplier_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_orders_org_category', 'purchase_orders', ['organization_id', 'category'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_orders_org_raised_by', 'purchase_orders', ['organization_id', 'raised_by_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_orders_org_expected', 'purchase_orders', ['organization_id', 'expected_date'], unique=False, schema=SCHEMA)

    # --- purchase order lines ----------------------------------------------------------
    op.create_table(
        'purchase_order_lines',
        sa.Column('purchase_order_id', sa.UUID(), nullable=False),
        sa.Column('sku', sa.String(length=60), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('uom', sa.String(length=20), nullable=False),
        sa.Column('unit_price_minor', sa.BigInteger(), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('received_qty', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['purchase_order_id'], [f'{SCHEMA}.purchase_orders.id'],
            name=op.f('fk_purchase_order_lines_purchase_order_id_purchase_orders'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_order_lines')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_operations_purchase_order_lines_organization_id'), 'purchase_order_lines', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_purchase_order_lines_org_po', 'purchase_order_lines', ['organization_id', 'purchase_order_id'], unique=False, schema=SCHEMA)

    # --- inventory items ---------------------------------------------------------------
    op.create_table(
        'inventory_items',
        sa.Column('sku', sa.String(length=60), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('warehouse', sa.String(length=120), nullable=False),
        sa.Column('on_hand', sa.Integer(), nullable=False),
        sa.Column('reorder_point', sa.Integer(), nullable=False),
        sa.Column('uom', sa.String(length=20), nullable=False),
        sa.Column('unit_value_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('stocked_at', sa.Date(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_items')),
        sa.UniqueConstraint('organization_id', 'sku', name='uq_inventory_items_org_sku'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_operations_inventory_items_organization_id'), 'inventory_items', ['organization_id'], unique=False, schema=SCHEMA)
    op.create_index('ix_inventory_items_org_warehouse', 'inventory_items', ['organization_id', 'warehouse'], unique=False, schema=SCHEMA)
    op.create_index('ix_inventory_items_org_category', 'inventory_items', ['organization_id', 'category'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('inventory_items', schema=SCHEMA)
    op.drop_table('purchase_order_lines', schema=SCHEMA)
    op.drop_table('purchase_orders', schema=SCHEMA)
    op.drop_table('warehouses', schema=SCHEMA)
    op.drop_table('suppliers', schema=SCHEMA)
    op.drop_table('processed_events', schema=SCHEMA)
    op.drop_table('outbox', schema=SCHEMA)
    op.drop_table('audit_logs', schema=SCHEMA)
