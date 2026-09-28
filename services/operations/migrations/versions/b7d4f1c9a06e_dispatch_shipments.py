"""dispatch: shipments, shipment_transitions

ADR-0024 §6; `docs/fulfilment/plan.md` F5 — dispatch and notifications.

1. `shipments` — the carrier handover for one order (delivery/courier path, distinct from the
   counter/walk-in path `orders.hand_over` already covers). One per order
   (`uq_shipments_org_order`). `status` runs `PICKUP_HANDOVER -> IN_TRANSIT -> DELIVERED` (or
   `FAILED`/`RETURNED`) — `app.domain.SHIPMENT_TRANSITIONS`. `carrier_name`/`tracking_reference`
   are manual text only (no carrier API integration in this build).
2. `shipment_transitions` (append-only — joins `APPEND_ONLY_TABLES` in `migrations/env.py`, the
   same privilege-enforced append-only rule `order_transitions` already carries).

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy, the
same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: b7d4f1c9a06e
Revises: ae34570ed578
Create Date: 2026-09-29 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4f1c9a06e"  # pragma: allowlist secret
down_revision: str | None = "ae34570ed578"  # pragma: allowlist secret
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
    # --- shipments ---------------------------------------------------------------------------
    op.create_table(
        "shipments",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("carrier_name", sa.String(length=120), nullable=False),
        sa.Column("tracking_reference", sa.String(length=120), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint(
            "status IN ('PICKUP_HANDOVER', 'IN_TRANSIT', 'DELIVERED', 'FAILED', 'RETURNED')",
            name=op.f("ck_shipments_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{SCHEMA}.orders.id"],
            name=op.f("fk_shipments_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_shipments_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        schema=SCHEMA,
    )
    _org_index("shipments")
    op.create_index(
        "uq_shipments_org_order",
        "shipments",
        ["organization_id", "order_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_shipments_org_status", "shipments", ["organization_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "ix_shipments_org_warehouse",
        "shipments",
        ["organization_id", "warehouse_id"],
        schema=SCHEMA,
    )
    _rls("shipments")

    # --- shipment_transitions (append-only) --------------------------------------------------
    op.create_table(
        "shipment_transitions",
        sa.Column("shipment_id", sa.UUID(), nullable=False),
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
            ["shipment_id"],
            [f"{SCHEMA}.shipments.id"],
            name=op.f("fk_shipment_transitions_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_transitions")),
        schema=SCHEMA,
    )
    _org_index("shipment_transitions")
    op.create_index(
        "ix_shipment_transitions_org_shipment",
        "shipment_transitions",
        ["organization_id", "shipment_id", "at"],
        schema=SCHEMA,
    )
    _rls("shipment_transitions")
    # Append-only by privilege (re-revoked in `migrations/env.py` after every grant
    # re-application) — the same rule `order_transitions` already carries.
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.shipment_transitions FROM "{APP_ROLE}"')


def downgrade() -> None:
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.shipment_transitions TO "{APP_ROLE}"')
    _unrls("shipment_transitions")
    op.drop_table("shipment_transitions", schema=SCHEMA)

    _unrls("shipments")
    op.drop_table("shipments", schema=SCHEMA)
