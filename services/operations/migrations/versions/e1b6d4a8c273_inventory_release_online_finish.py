"""finish the inventory release's heavy steps without blocking writes

`c5e91a7d3b28` and `c5a82f6d3b19` leave four things for this revision because none of them may
run inside the migration's own transaction on a populated table (the pattern `c4a8e1f6d273`
set for lots and rolls):

* the two reservation indexes are built `CONCURRENTLY` (a plain `CREATE INDEX` blocks every
  reservation write for the build);
* the `inventory_items` and `purchase_order_lines` CHECKs were added `NOT VALID` and are
  validated here, which needs only `SHARE UPDATE EXCLUSIVE`;
* `purchase_order_lines.received_milli` is backfilled from whole units in committed batches
  rather than one full-table UPDATE;
* the temporary server defaults on the two new `purchase_order_lines` columns are dropped.

Each statement runs in `autocommit_block()`; every step is idempotent, so a failure part-way is
resumed by re-running `alembic upgrade head` (a leftover INVALID index from an interrupted build
is dropped and rebuilt).

Downgrade restores the defaults and drops the indexes in the migration's own transaction; the constraints go with the columns in
`c5e91a7d3b28`.

Revision ID: e1b6d4a8c273
Revises: d7b3e5a19f62
Create Date: 2026-10-11 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1b6d4a8c273"  # pragma: allowlist secret
down_revision: str | None = "d7b3e5a19f62"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
BATCH = 5000

CHECKS = (
    ("inventory_items", "ck_inventory_items_receipt_tolerance_bp_range"),
    ("purchase_order_lines", "ck_purchase_order_lines_received_milli_non_negative"),
)


def _drop_invalid(name: str) -> None:
    """An interrupted `CREATE INDEX CONCURRENTLY` leaves an INVALID index that `IF NOT EXISTS`
    would then skip forever."""
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :name AND NOT i.indisvalid"
            ),
            {"schema": SCHEMA, "name": name},
        )
        .scalar_one()
    )
    if invalid:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.{name}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _drop_invalid("ix_inventory_reservations_org_created")
        op.create_index(
            "ix_inventory_reservations_org_created",
            "inventory_reservations",
            ["organization_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
            schema=SCHEMA,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        _drop_invalid("ix_inventory_reservations_org_expires_active")
        op.create_index(
            "ix_inventory_reservations_org_expires_active",
            "inventory_reservations",
            ["organization_id", "expires_at"],
            schema=SCHEMA,
            postgresql_where=sa.text("status = 'active' AND expires_at IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        for table, name in CHECKS:
            op.execute(f"ALTER TABLE {SCHEMA}.{table} VALIDATE CONSTRAINT {name}")
        # Whole units before this release; one unit is 1000 milli-units (ADR-0014 §2). Only
        # untouched rows (0) are rewritten, so a re-run never clobbers a real milli figure.
        while True:
            done = op.get_bind().execute(
                sa.text(
                    f"UPDATE {SCHEMA}.purchase_order_lines SET received_milli = "  # noqa: S608
                    "received_qty::bigint * 1000 WHERE id IN (SELECT id FROM "
                    f"{SCHEMA}.purchase_order_lines WHERE received_milli = 0 "
                    "AND received_qty <> 0 LIMIT :n)"
                ),
                {"n": BATCH},
            )
            if done.rowcount == 0:
                break
    op.alter_column("purchase_order_lines", "received_milli", server_default=None, schema=SCHEMA)
    op.alter_column("purchase_order_lines", "short_closed", server_default=None, schema=SCHEMA)


def downgrade() -> None:
    op.alter_column("purchase_order_lines", "received_milli", server_default="0", schema=SCHEMA)
    op.alter_column(
        "purchase_order_lines", "short_closed", server_default=sa.false(), schema=SCHEMA
    )
    # Plain, transactional drops (no autocommit block): a later revision refusing the downgrade
    # must roll these back with it, never leave a database stamped at head without its indexes.
    for name in (
        "ix_inventory_reservations_org_expires_active",
        "ix_inventory_reservations_org_created",
    ):
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{name}")
