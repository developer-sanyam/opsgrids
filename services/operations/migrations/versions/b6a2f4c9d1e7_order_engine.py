"""order engine: orders, order lines, order transitions, fulfilment settings

ADR-0024 (fulfilment boundaries), ADR-0025 (order and task state); `docs/fulfilment/plan.md` F2.

1. `inventory_items` gains `selling_price_minor` / `selling_price_currency` (ADR-0024 §3, amending
   ADR-0014): both nullable, both null or both set, `selling_price_minor >= 0`. The MVP price
   source for an order line — distinct from `unit_value_minor`, which is cost.
2. `orders` (ADR-0025 §1's status vocabulary, `String(20)`, no CHECK — the vocabulary lives in
   `app.domain.ORDER_TRANSITIONS`, enforced by the write path, the same precedent
   `purchase_orders.status` already sets), `order_lines`, `order_transitions` (append-only —
   joins `APPEND_ONLY_TABLES` in `migrations/env.py`), `fulfilment_settings` (per-org, optionally
   per-warehouse, two partial unique indexes).

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy, the
same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: b6a2f4c9d1e7
Revises: 46345e7e794d
Create Date: 2026-09-28 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6a2f4c9d1e7"  # pragma: allowlist secret
down_revision: str | None = "46345e7e794d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"


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


def _unrls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_{SCHEMA}_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def upgrade() -> None:
    # --- 1. selling price on inventory_items (ADR-0024 §3) ------------------------------------
    op.add_column(
        "inventory_items",
        sa.Column("selling_price_minor", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "inventory_items",
        sa.Column("selling_price_currency", sa.String(length=3), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inventory_items_selling_price_currency_matches_minor"),
        "inventory_items",
        "(selling_price_minor IS NULL) = (selling_price_currency IS NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inventory_items_selling_price_non_negative"),
        "inventory_items",
        "selling_price_minor IS NULL OR selling_price_minor >= 0",
        schema=SCHEMA,
    )

    # --- 2. orders ------------------------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("customer_name", sa.String(length=160), nullable=False),
        sa.Column("customer_phone", sa.String(length=32), nullable=False),
        sa.Column("subtotal_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("settings_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("subtotal_minor >= 0", name=op.f("ck_orders_subtotal_non_negative")),
        sa.CheckConstraint("total_minor >= 0", name=op.f("ck_orders_total_non_negative")),
        sa.CheckConstraint("version >= 0", name=op.f("ck_orders_version_non_negative")),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_orders_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        schema=SCHEMA,
    )
    _org_index("orders")
    op.create_index(
        "uq_orders_org_number",
        "orders",
        ["organization_id", "number"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_orders_org_status", "orders", ["organization_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "ix_orders_org_channel", "orders", ["organization_id", "channel"], schema=SCHEMA
    )
    op.create_index(
        "ix_orders_org_customer", "orders", ["organization_id", "customer_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_orders_org_warehouse", "orders", ["organization_id", "warehouse_id"], schema=SCHEMA
    )
    _rls("orders")

    # --- 3. order_lines ---------------------------------------------------------------------------
    op.create_table(
        "order_lines",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("price_overridden", sa.Boolean(), nullable=False),
        sa.Column("selling_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("line_total_minor", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity_milli > 0", name=op.f("ck_order_lines_quantity_positive")),
        sa.CheckConstraint(
            "selling_price_minor IS NULL OR selling_price_minor >= 0",
            name=op.f("ck_order_lines_selling_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_order_lines_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_order_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_lines")),
        schema=SCHEMA,
    )
    _org_index("order_lines")
    op.create_index(
        "ix_order_lines_org_order", "order_lines", ["organization_id", "order_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_order_lines_org_item",
        "order_lines",
        ["organization_id", "inventory_item_id"],
        schema=SCHEMA,
    )
    _rls("order_lines")

    # --- 4. order_transitions (append-only) ---------------------------------------------------
    op.create_table(
        "order_transitions",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_order_transitions_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_transitions")),
        schema=SCHEMA,
    )
    _org_index("order_transitions")
    op.create_index(
        "ix_order_transitions_org_order",
        "order_transitions",
        ["organization_id", "order_id", "at"],
        schema=SCHEMA,
    )
    _rls("order_transitions")
    # Append-only by privilege (re-revoked in `migrations/env.py` after every grant
    # re-application) — the same rule `inventory_moves`, `inventory_ledger` and
    # `inventory_reservation_releases` already carry.
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.order_transitions FROM "{APP_ROLE}"')

    # --- 5. fulfilment_settings -----------------------------------------------------------------
    op.create_table(
        "fulfilment_settings",
        sa.Column("warehouse_id", sa.UUID(), nullable=True),
        sa.Column("invoice_timing", sa.String(length=20), nullable=False),
        sa.Column("payment_defaults", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("offer_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("offer_rounds", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "invoice_timing IN ('on_accept', 'on_dispatch', 'on_delivery')",
            name=op.f("ck_fulfilment_settings_invoice_timing_known"),
        ),
        sa.CheckConstraint(
            "offer_timeout_seconds > 0", name=op.f("ck_fulfilment_settings_offer_timeout_positive")
        ),
        sa.CheckConstraint(
            "offer_rounds > 0", name=op.f("ck_fulfilment_settings_offer_rounds_positive")
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_fulfilment_settings_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fulfilment_settings")),
        schema=SCHEMA,
    )
    _org_index("fulfilment_settings")
    op.create_index(
        "uq_fulfilment_settings_org_default",
        "fulfilment_settings",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("warehouse_id IS NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_fulfilment_settings_org_warehouse",
        "fulfilment_settings",
        ["organization_id", "warehouse_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("warehouse_id IS NOT NULL AND deleted_at IS NULL"),
    )
    _rls("fulfilment_settings")


def downgrade() -> None:
    _unrls("fulfilment_settings")
    op.drop_table("fulfilment_settings", schema=SCHEMA)

    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.order_transitions TO "{APP_ROLE}"')
    _unrls("order_transitions")
    op.drop_table("order_transitions", schema=SCHEMA)

    _unrls("order_lines")
    op.drop_table("order_lines", schema=SCHEMA)

    _unrls("orders")
    op.drop_table("orders", schema=SCHEMA)

    op.drop_constraint(
        "ck_inventory_items_selling_price_non_negative", "inventory_items", schema=SCHEMA
    )
    op.drop_constraint(
        "ck_inventory_items_selling_price_currency_matches_minor",
        "inventory_items",
        schema=SCHEMA,
    )
    op.drop_column("inventory_items", "selling_price_currency", schema=SCHEMA)
    op.drop_column("inventory_items", "selling_price_minor", schema=SCHEMA)
