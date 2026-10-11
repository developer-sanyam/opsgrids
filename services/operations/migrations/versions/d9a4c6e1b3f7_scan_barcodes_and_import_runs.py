"""item / lot / roll barcodes and the append-only record of committed imports

ADR-0027 / inventory Phase 9.

1. `inventory_items.barcode`, `lots.barcode`, `rolls.barcode` — nullable `String(64)`, each
   **unique per organization where set** (a partial unique index; items additionally ignore
   soft-deleted rows, like `uq_inventory_items_org_sku`, so a deleted item's old code is
   reusable). `locations.barcode` already exists (`46345e7e794d`) and is untouched. A `NULL`
   barcode means "scan the natural code" — the SKU, lot number or roll number, all of which are
   already unique — so nothing needs backfilling and no existing row changes.
2. `inventory_import_runs` — one row per **committed** bulk import, written in the same
   transaction as the rows it created: the idempotency key + request hash (a retried commit is a
   replay), the entity, the counts and the response, replayed verbatim. Append-only (UPDATE and
   DELETE revoked, re-revoked in `migrations/env.py` after every grant re-application), RLS on.

Downgrade drops both, in reverse. It loses the barcodes people typed and the import history; it
cannot lose stock, which no part of this migration touches.

Revision ID: d9a4c6e1b3f7
Revises: c5a82f6d3b19
Create Date: 2026-10-05 10:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9a4c6e1b3f7"  # pragma: allowlist secret
down_revision: str | None = "c5a82f6d3b19"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
LOCK_TIMEOUT = "5s"
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"


def upgrade() -> None:
    # Fail the deploy, not the service: ADD COLUMN needs ACCESS EXCLUSIVE, and queued behind a
    # long transaction it would stall every read and write on the table.
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for table in ("inventory_items", "lots", "rolls"):
        op.add_column(
            table, sa.Column("barcode", sa.String(length=64), nullable=True), schema=SCHEMA
        )
    # The three unique barcode indexes are built CONCURRENTLY by `e1b6d4a8c273`.

    op.create_table(
        "inventory_import_runs",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=20), nullable=False),
        sa.Column("on_duplicate", sa.String(length=10), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_import_runs")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_inventory_import_runs_organization_id"),
        "inventory_import_runs",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_inventory_import_runs_org_key",
        "inventory_import_runs",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inventory_import_runs_org_created",
        "inventory_import_runs",
        ["organization_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.inventory_import_runs ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.inventory_import_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.inventory_import_runs
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.inventory_import_runs FROM "{APP_ROLE}"')


def _refuse_destructive_downgrade() -> None:
    """Dropping the columns forgets every barcode people typed, and dropping the table forgets the
    import history. Refuse once either holds data, unless the operator says in the environment
    that destroying it is the point."""
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    real = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT (SELECT count(*) FROM {SCHEMA}.inventory_items WHERE barcode IS NOT NULL)
                 + (SELECT count(*) FROM {SCHEMA}.lots WHERE barcode IS NOT NULL)
                 + (SELECT count(*) FROM {SCHEMA}.rolls WHERE barcode IS NOT NULL)
                 + (SELECT count(*) FROM {SCHEMA}.inventory_import_runs)
            """
            )
        )
        .scalar_one()
    )
    if real:
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {real} barcode(s) and/or import run(s) exist and "
            "this would destroy them. Roll the application back instead (the schema is backward "
            "compatible). To destroy them deliberately, after a backup, set "
            f"{ALLOW_DESTRUCTIVE_ENV}=1."
        )


def downgrade() -> None:
    _refuse_destructive_downgrade()
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.inventory_import_runs TO "{APP_ROLE}"')
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.inventory_import_runs")
    op.drop_table("inventory_import_runs", schema=SCHEMA)

    # The barcode indexes went with `e1b6d4a8c273`'s downgrade; dropping a column drops any left.
    for table in ("rolls", "lots", "inventory_items"):
        op.drop_column(table, "barcode", schema=SCHEMA)
