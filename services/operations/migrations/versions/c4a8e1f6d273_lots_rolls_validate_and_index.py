"""validate the bucket foreign keys and build the read-path indexes without blocking writes

The second half of `b7d41e92c05a`. That revision adds the lot/roll foreign keys `NOT VALID`
(enforced for new rows, existing rows not yet checked) and leaves the indexes the new read paths
need on `inventory_ledger`, `inventory_balances` and `inventory_moves` to this one, because both
steps are the kind that must not run inside the migration's own transaction:

* `VALIDATE CONSTRAINT` scans the whole table. Inside the first revision's transaction it would run
  while that transaction still held the lock the `ADD CONSTRAINT` took — the one that blocks
  writes. In its own transaction it needs only `SHARE UPDATE EXCLUSIVE`, which lets reads *and
  writes* carry on.
* `CREATE INDEX CONCURRENTLY` cannot run in a transaction at all; a plain `CREATE INDEX` on the
  ledger blocks every stock post for as long as the build takes.

Both run in `autocommit_block()`, one statement per transaction, so the two halves are separate
revisions: if a build or a validation fails (a long-running transaction, an orphan row written
between the revisions), the first revision is already committed and stamped, and re-running
`alembic upgrade head` simply resumes here. Every step is idempotent — a leftover `INVALID` index
from an interrupted `CONCURRENTLY` build is dropped and rebuilt, and validating a validated
constraint is a no-op.

Downgrade drops the indexes (the constraints are dropped by `b7d41e92c05a`'s downgrade).

Revision ID: c4a8e1f6d273
Revises: b7d41e92c05a
Create Date: 2026-10-05 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8e1f6d273"  # pragma: allowlist secret
down_revision: str | None = "b7d41e92c05a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
BUCKET_TABLES = ("inventory_ledger", "inventory_balances", "inventory_discrepancies")

#: (index name, table, columns, partial-index predicate)
INDEXES: tuple[tuple[str, str, tuple[object, ...], str | None], ...] = (
    (
        "ix_inventory_ledger_org_created",
        "inventory_ledger",
        ("organization_id", sa.text("created_at DESC"), sa.text("id DESC")),
        None,
    ),
    (
        "ix_inventory_ledger_org_location_created",
        "inventory_ledger",
        ("organization_id", "location_id", sa.text("created_at DESC"), sa.text("id DESC")),
        None,
    ),
    (
        "ix_inventory_ledger_org_lot_created",
        "inventory_ledger",
        ("organization_id", "lot_id", sa.text("created_at DESC"), sa.text("id DESC")),
        "lot_id IS NOT NULL",
    ),
    (
        "ix_inventory_ledger_org_roll_created",
        "inventory_ledger",
        ("organization_id", "roll_id", sa.text("created_at DESC"), sa.text("id DESC")),
        "roll_id IS NOT NULL",
    ),
    (
        "ix_inventory_balances_org_location",
        "inventory_balances",
        ("organization_id", "location_id"),
        None,
    ),
    (
        "ix_inventory_balances_org_lot",
        "inventory_balances",
        ("organization_id", "lot_id"),
        "lot_id IS NOT NULL",
    ),
    (
        "ix_inventory_balances_org_roll",
        "inventory_balances",
        ("organization_id", "roll_id"),
        "roll_id IS NOT NULL",
    ),
    (
        "ix_inventory_moves_org_occurred",
        "inventory_moves",
        ("organization_id", sa.text("occurred_on DESC")),
        None,
    ),
)


def _drop_invalid(name: str) -> None:
    """An interrupted `CREATE INDEX CONCURRENTLY` leaves an `INVALID` index behind, and
    `IF NOT EXISTS` would then skip it forever."""
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
        for name, table, columns, where in INDEXES:
            _drop_invalid(name)
            op.create_index(
                name,
                table,
                list(columns),  # type: ignore[arg-type]
                schema=SCHEMA,
                postgresql_where=sa.text(where) if where else None,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
        for table in BUCKET_TABLES:
            for column in ("lot", "roll"):
                op.execute(
                    f"ALTER TABLE {SCHEMA}.{table} VALIDATE CONSTRAINT fk_{table}_org_{column}"
                )


def downgrade() -> None:
    for name, _table, _columns, _where in reversed(INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{name}")
