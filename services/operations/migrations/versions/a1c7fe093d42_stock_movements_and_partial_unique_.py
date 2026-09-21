"""stock movements ledger and partial unique indexes

Makes `inventory_items.on_hand` a real cached balance instead of overwritten-in-place state:
`operations.stock_movements` is the append-only ledger it is the running sum of, and from here on
only a movement row may change it. One `opening` movement is backfilled per existing item with
stock, so the invariant (`on_hand == SUM(quantity_delta)`) holds for every row from day one.

Same revision swaps the total unique constraints on `suppliers.code`, `warehouses.code` and
`inventory_items.sku` for partial ones (`WHERE deleted_at IS NULL`), which the soft-delete
endpoints landing alongside this need: without it, a soft-deleted code/SKU blocks re-creation with
an `IntegrityError` 500 (house precedent: `services/account/app/models.py:250`).
`purchase_orders.number` stays a total unique constraint — a PO number is never reused.

Backward compatible with the version already running: additive (`stock_movements` is a brand-new
table) and the constraint swap only widens what is allowed (a soft-deleted row's code/SKU becomes
reusable; nothing that was previously permitted becomes refused).

Revision ID: a1c7fe093d42
Revises: c3f1a7e5b2d9
Create Date: 2026-09-21 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7fe093d42"  # pragma: allowlist secret
down_revision: str | None = "c3f1a7e5b2d9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"


def upgrade() -> None:
    # --- stock_movements -----------------------------------------------------------------
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=True),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("on_hand_after", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(length=30), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "on_hand_after >= 0", name=op.f("ck_stock_movements_on_hand_after_non_negative")
        ),
        sa.CheckConstraint(
            "quantity_delta <> 0 OR kind = 'count'",
            name=op.f("ck_stock_movements_quantity_delta_nonzero"),
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_stock_movements_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_stock_movements_organization_id"),
        "stock_movements",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_movements_org_item_created",
        "stock_movements",
        ["organization_id", "inventory_item_id", sa.literal_column("created_at DESC")],
        unique=False,
        schema=SCHEMA,
    )

    # Row-level security, exactly as `c3f1a7e5b2d9` does for every other tenant table: ENABLE +
    # FORCE, and a single `tenant_isolation` policy with both USING and WITH CHECK.
    op.execute(f"ALTER TABLE {SCHEMA}.stock_movements ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.stock_movements FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.stock_movements
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )

    # Append-only by table privilege, not by convention — the same tool `audit_logs` uses
    # (`platform_core/db.py:142-145`). `apply_schema_grants` re-grants UPDATE/DELETE on every
    # table on each migration run and only re-revokes `audit_logs` itself, so this alone is not
    # durable across a later `alembic upgrade`; `migrations/env.py` re-applies it after every
    # grant re-application, in the same shape `console`/`metering` use for their own append-only
    # tables.
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.stock_movements FROM "{APP_ROLE}"')

    # Backfill: one `opening` movement per existing item with stock, so the invariant
    # (`on_hand == SUM(stock_movements.quantity_delta)`) holds for every row from day one.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.stock_movements
            (id, organization_id, inventory_item_id, kind, reason, quantity_delta,
             on_hand_after, reference_type, reference_id, note, occurred_on, created_at,
             created_by)
        SELECT
            gen_random_uuid(),
            organization_id,
            id,
            'opening',
            NULL,
            on_hand,
            on_hand,
            NULL,
            NULL,
            '',
            COALESCE(stocked_at, created_at::date),
            created_at,
            created_by
        FROM {SCHEMA}.inventory_items
        WHERE on_hand <> 0
        """
    )

    # --- partial unique indexes: a soft-deleted code/SKU may be reused --------------------
    op.drop_constraint("uq_suppliers_org_code", "suppliers", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_suppliers_org_code",
        "suppliers",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_constraint("uq_warehouses_org_code", "warehouses", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_warehouses_org_code",
        "warehouses",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_constraint(
        "uq_inventory_items_org_sku", "inventory_items", schema=SCHEMA, type_="unique"
    )
    op.create_index(
        "uq_inventory_items_org_sku",
        "inventory_items",
        ["organization_id", "sku"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_inventory_items_org_sku", table_name="inventory_items", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_inventory_items_org_sku", "inventory_items", ["organization_id", "sku"], schema=SCHEMA
    )

    op.drop_index("uq_warehouses_org_code", table_name="warehouses", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_warehouses_org_code", "warehouses", ["organization_id", "code"], schema=SCHEMA
    )

    op.drop_index("uq_suppliers_org_code", table_name="suppliers", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_suppliers_org_code", "suppliers", ["organization_id", "code"], schema=SCHEMA
    )

    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.stock_movements")
    op.execute(f"ALTER TABLE {SCHEMA}.stock_movements NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.stock_movements DISABLE ROW LEVEL SECURITY")
    op.drop_table("stock_movements", schema=SCHEMA)
