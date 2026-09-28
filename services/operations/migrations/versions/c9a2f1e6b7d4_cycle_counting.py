"""counting: count sessions and lines (cycle counting / stocktaking)

ADR-0026; `docs/fulfilment/plan.md` F8.

1. `count_sessions` — one scoped physical count (whole warehouse, one zone, or an explicit
   location list). `uq_count_sessions_org_warehouse_live` is the whole concurrency story: at most
   one `ACTIVE`/`REVIEW` session per warehouse (ADR-0026 §5).
2. `count_lines` — one row per bucket in scope, the same `(item, location, status, lot, roll)`
   tuple `inventory_balances` keys on. `uq_count_lines_session_bucket` is `NULLS NOT DISTINCT`, the
   same technique `uq_inventory_balances_bucket` uses.

Neither table is append-only; both get ENABLE + FORCE RLS and the standard `tenant_isolation`
policy, the same shape every migration since `c3f1a7e5b2d9` uses. No change to any existing table,
and no kernel change (`app.inventory.ledger.post_move` already supports `kind="count"` with
`counted_milli` — ADR-0026 §3).

Revision ID: c9a2f1e6b7d4
Revises: c8f3a1d97b46
Create Date: 2026-09-29 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9a2f1e6b7d4"  # pragma: allowlist secret
down_revision: str | None = "c8f3a1d97b46"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

_STOCK_STATUSES = (
    "AVAILABLE",
    "QUALITY_HOLD",
    "QUARANTINE",
    "DAMAGED",
    "REJECTED",
    "WIP",
    "IN_TRANSIT",
    "AT_JOB_WORKER",
    "BLOCKED",
)
_STATUS_SQL = ", ".join(f"'{s}'" for s in _STOCK_STATUSES)


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
    # --- count_sessions ---------------------------------------------------------------------------
    op.create_table(
        "count_sessions",
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("scope_kind", sa.String(length=12), nullable=False),
        sa.Column("zone_location_id", sa.UUID(), nullable=True),
        sa.Column("location_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint(
            "scope_kind IN ('WAREHOUSE', 'ZONE', 'LOCATIONS')",
            name=op.f("ck_count_sessions_scope_kind_known"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVIEW', 'CLOSED', 'CANCELLED')",
            name=op.f("ck_count_sessions_status_known"),
        ),
        sa.CheckConstraint(
            "(scope_kind <> 'ZONE') OR (zone_location_id IS NOT NULL)",
            name=op.f("ck_count_sessions_zone_needs_location"),
        ),
        sa.CheckConstraint(
            "(scope_kind <> 'LOCATIONS') OR (location_ids IS NOT NULL)",
            name=op.f("ck_count_sessions_locations_needs_list"),
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_count_sessions_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["zone_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_count_sessions_zone_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_count_sessions")),
        schema=SCHEMA,
    )
    _org_index("count_sessions")
    op.create_index(
        "uq_count_sessions_org_warehouse_live",
        "count_sessions",
        ["organization_id", "warehouse_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('ACTIVE', 'REVIEW')"),
    )
    op.create_index(
        "ix_count_sessions_org_warehouse",
        "count_sessions",
        ["organization_id", "warehouse_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_count_sessions_org_status", "count_sessions", ["organization_id", "status"], schema=SCHEMA
    )
    _rls("count_sessions")

    # --- count_lines -------------------------------------------------------------------------------
    op.create_table(
        "count_lines",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("is_unlisted", sa.Boolean(), nullable=False),
        sa.Column("expected_milli", sa.BigInteger(), nullable=False),
        sa.Column(
            "expected_snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("counted_milli", sa.BigInteger(), nullable=True),
        sa.Column("count_method", sa.String(length=10), nullable=True),
        sa.Column("scanned_barcode", sa.String(length=64), nullable=True),
        sa.Column("counted_by", sa.String(length=64), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_status", sa.String(length=10), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=False),
        sa.Column("posted_move_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint(f"status IN ({_STATUS_SQL})", name=op.f("ck_count_lines_status_known")),
        sa.CheckConstraint(
            "count_method IS NULL OR count_method IN ('scan', 'manual')",
            name=op.f("ck_count_lines_count_method_known"),
        ),
        sa.CheckConstraint(
            "review_status IN ('NONE', 'PENDING', 'APPROVED', 'REJECTED')",
            name=op.f("ck_count_lines_review_status_known"),
        ),
        sa.CheckConstraint("expected_milli >= 0", name=op.f("ck_count_lines_expected_non_negative")),
        sa.CheckConstraint(
            "counted_milli IS NULL OR counted_milli >= 0",
            name=op.f("ck_count_lines_counted_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            [f"{SCHEMA}.count_sessions.id"],
            name=op.f("fk_count_lines_session_id_count_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_count_lines_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_count_lines_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posted_move_id"],
            [f"{SCHEMA}.inventory_moves.id"],
            name=op.f("fk_count_lines_posted_move_id_inventory_moves"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_count_lines")),
        schema=SCHEMA,
    )
    _org_index("count_lines")
    op.create_index(
        "uq_count_lines_session_bucket",
        "count_lines",
        [
            "organization_id",
            "session_id",
            "inventory_item_id",
            "location_id",
            "status",
            "lot_id",
            "roll_id",
        ],
        unique=True,
        schema=SCHEMA,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_count_lines_org_session", "count_lines", ["organization_id", "session_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_count_lines_org_review", "count_lines", ["organization_id", "review_status"], schema=SCHEMA
    )
    _rls("count_lines")


def downgrade() -> None:
    _unrls("count_lines")
    op.drop_table("count_lines", schema=SCHEMA)

    _unrls("count_sessions")
    op.drop_table("count_sessions", schema=SCHEMA)
