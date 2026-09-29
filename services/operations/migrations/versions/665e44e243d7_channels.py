"""channels: channels, channel_sku_mappings, channel_order_imports, channel_sync_logs

Fulfilment plan F11 — external sales-channel connections, SKU mapping, order-import
idempotency and sync history.

1. `channels` — one organization's connection to an external channel (`app.domain
   .CHANNEL_PROVIDERS`; only `mock_marketplace` exists today). Sealed credential
   (`api_key_enc`), connected/disconnected/error status, health counters.
2. `channel_sku_mappings` — external SKU -> `inventory_items.id`, plus this SKU's own last-push
   history.
3. `channel_order_imports` — the idempotency claim for one external order id, and the link to
   the real F2 `orders` row it became.
4. `channel_sync_logs` (append-only — joins `APPEND_ONLY_TABLES` in `migrations/env.py`, the
   same privilege-enforced append-only rule `order_transitions`/`shipment_transitions` already
   carry).

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy, the
same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: 665e44e243d7
Revises: c8e2a4f6b1d3
Create Date: 2026-09-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "665e44e243d7"  # pragma: allowlist secret
down_revision: str | None = "c8e2a4f6b1d3"  # pragma: allowlist secret
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


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    # --- channels ------------------------------------------------------------------------------
    op.create_table(
        "channels",
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_shop_id", sa.String(length=120), nullable=False),
        sa.Column("api_base_url", sa.String(length=300), nullable=True),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_masked", sa.String(length=40), nullable=False),
        sa.Column("default_warehouse_id", sa.UUID(), nullable=True),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column("auto_accept_orders", sa.Boolean(), nullable=False),
        sa.Column("last_order_cursor", sa.String(length=120), nullable=True),
        sa.Column("last_orders_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inventory_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=40), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint(
            "consecutive_failures >= 0", name=op.f("ck_channels_consecutive_failures_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["default_warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_channels_default_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channels")),
        schema=SCHEMA,
    )
    _org_index("channels")
    op.create_index(
        "uq_channels_org_provider_shop",
        "channels",
        ["organization_id", "provider", "external_shop_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_channels_org_status", "channels", ["organization_id", "status"], schema=SCHEMA
    )
    _rls("channels")

    # --- channel_sku_mappings -------------------------------------------------------------------
    op.create_table(
        "channel_sku_mappings",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("external_sku", sa.String(length=120), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("last_pushed_available_milli", sa.BigInteger(), nullable=True),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_push_status", sa.String(length=20), nullable=True),
        sa.Column("last_push_error", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{SCHEMA}.channels.id"],
            name=op.f("fk_channel_sku_mappings_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_channel_sku_mappings_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_sku_mappings")),
        schema=SCHEMA,
    )
    _org_index("channel_sku_mappings")
    op.create_index(
        "uq_channel_sku_mappings_org_channel_sku",
        "channel_sku_mappings",
        ["organization_id", "channel_id", "external_sku"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_channel_sku_mappings_org_item",
        "channel_sku_mappings",
        ["organization_id", "inventory_item_id"],
        schema=SCHEMA,
    )
    _rls("channel_sku_mappings")

    # --- channel_order_imports -------------------------------------------------------------------
    op.create_table(
        "channel_order_imports",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("external_order_id", sa.String(length=120), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{SCHEMA}.channels.id"],
            name=op.f("fk_channel_order_imports_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_channel_order_imports_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_order_imports")),
        schema=SCHEMA,
    )
    _org_index("channel_order_imports")
    op.create_index(
        "uq_channel_order_imports_org_channel_external",
        "channel_order_imports",
        ["organization_id", "channel_id", "external_order_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_channel_order_imports_org_order",
        "channel_order_imports",
        ["organization_id", "order_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )
    _rls("channel_order_imports")

    # --- channel_sync_logs (append-only) ---------------------------------------------------------
    op.create_table(
        "channel_sync_logs",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("sync_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_ref", sa.String(length=120), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{SCHEMA}.channels.id"],
            name=op.f("fk_channel_sync_logs_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_sync_logs")),
        schema=SCHEMA,
    )
    _org_index("channel_sync_logs")
    op.create_index(
        "ix_channel_sync_logs_org_channel_at",
        "channel_sync_logs",
        ["organization_id", "channel_id", "attempted_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_channel_sync_logs_org_channel_status",
        "channel_sync_logs",
        ["organization_id", "channel_id", "status"],
        schema=SCHEMA,
    )
    _rls("channel_sync_logs")
    # Append-only by privilege (re-revoked in `migrations/env.py` after every grant
    # re-application) — the same rule `order_transitions`/`shipment_transitions` already carry.
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.channel_sync_logs FROM "{APP_ROLE}"')


def downgrade() -> None:
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.channel_sync_logs TO "{APP_ROLE}"')
    _unrls("channel_sync_logs")
    op.drop_table("channel_sync_logs", schema=SCHEMA)

    _unrls("channel_order_imports")
    op.drop_table("channel_order_imports", schema=SCHEMA)

    _unrls("channel_sku_mappings")
    op.drop_table("channel_sku_mappings", schema=SCHEMA)

    _unrls("channels")
    op.drop_table("channels", schema=SCHEMA)
