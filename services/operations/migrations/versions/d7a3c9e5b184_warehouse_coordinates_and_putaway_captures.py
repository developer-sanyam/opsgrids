"""warehouse site coordinates and the append-only putaway_captures table

The data half of "warehouse map, find-stock and storage put-away" (spec
`docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md` §3).

1. `warehouses.lat_micro` / `lng_micro` — where the *site* is on a map, nullable integer
   micro-degrees (10^-6; no float, ADR-0000 §8). Named CHECKs: both set or both NULL, and each
   inside its range. There is no `address_text`: `warehouses.location` already holds the address.
2. `putaway_captures` — one append-only row per storage put-away move: the scanned destination
   bin, the bin the server suggested, the worker (nullable — a manager with no worker row can still
   put stock away), the actor, and the worker's GPS fix when the device gave one. The bin is the
   location of record; the fix is audit evidence behind its own permission and an audit row.
   `UPDATE`/`DELETE` are revoked from the app role (and re-revoked in `migrations/env.py` after
   every grant re-application); the 30-day purge is an admin-run procedure
   (`tools/purge_putaway_captures.py`), not an in-app job.

`inventory_moves` is unchanged — its `reference_type` is a free `String(30)` with no CHECK, so
`putaway` needs no DDL. In-schema foreign keys only (ADR-0000 §10: no cross-schema FKs), single
column. Everything is additive: code from before this revision ignores the new columns and table.

**Locking.** `lock_timeout` is 5s (fail the deploy, not the service). `warehouses` is a handful of
rows, so adding its columns and CHECKs is instantaneous; `putaway_captures` is new and empty.

**Downgrade** drops the table and the columns but **refuses** when any `putaway_captures` row
exists or any warehouse has coordinates, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` — it would
destroy proof of work and a site's map position (precedent `b7d41e92c05a`). Prefer rolling the code
back; the schema is backward compatible. On an empty database it fully reverses.

**Re-parenting.** Two unmerged inventory stacks share `down_revision` `c4a8e1f6d273`. Whichever
merges second sets `down_revision` to the first one's head (the schema changes do not overlap);
the `# RE-PARENT HERE` comment below marks the line.

Revision ID: d7a3c9e5b184
Revises: c4a8e1f6d273
Create Date: 2026-10-05 18:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3c9e5b184"  # pragma: allowlist secret
# RE-PARENT HERE (see the module docstring): the second inventory stack to merge changes this.
down_revision: str | None = "c4a8e1f6d273"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

LOCK_TIMEOUT = "5s"
#: Set to `1` to let `downgrade()` destroy real captures and site coordinates.
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"


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


def upgrade() -> None:
    # Fail fast rather than queue: a lock request that waits behind a long transaction makes every
    # later query on that table wait behind *it*. (Local to this transaction.)
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    # --- warehouses: the site's position on a map --------------------------------------------
    op.add_column(
        "warehouses", sa.Column("lat_micro", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "warehouses", sa.Column("lng_micro", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_warehouses_coords_paired"),
        "warehouses",
        "(lat_micro IS NULL) = (lng_micro IS NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_warehouses_lat_in_range"),
        "warehouses",
        "lat_micro IS NULL OR lat_micro BETWEEN -90000000 AND 90000000",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_warehouses_lng_in_range"),
        "warehouses",
        "lng_micro IS NULL OR lng_micro BETWEEN -180000000 AND 180000000",
        schema=SCHEMA,
    )

    # --- putaway_captures (append-only) ------------------------------------------------------
    op.create_table(
        "putaway_captures",
        sa.Column("move_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("suggested_location_id", sa.UUID(), nullable=True),
        sa.Column("worker_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("lat_micro", sa.BigInteger(), nullable=True),
        sa.Column("lng_micro", sa.BigInteger(), nullable=True),
        sa.Column("accuracy_m", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("matched_suggestion", sa.Boolean(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(lat_micro IS NULL) = (lng_micro IS NULL)",
            name=op.f("ck_putaway_captures_coords_paired"),
        ),
        sa.CheckConstraint(
            "lat_micro IS NULL OR lat_micro BETWEEN -90000000 AND 90000000",
            name=op.f("ck_putaway_captures_lat_in_range"),
        ),
        sa.CheckConstraint(
            "lng_micro IS NULL OR lng_micro BETWEEN -180000000 AND 180000000",
            name=op.f("ck_putaway_captures_lng_in_range"),
        ),
        sa.CheckConstraint(
            "accuracy_m IS NULL OR accuracy_m >= 0",
            name=op.f("ck_putaway_captures_accuracy_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["move_id"],
            [f"{SCHEMA}.inventory_moves.id"],
            name=op.f("fk_putaway_captures_move_id_inventory_moves"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_putaway_captures_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suggested_location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_putaway_captures_suggested_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            [f"{SCHEMA}.workers.id"],
            name=op.f("fk_putaway_captures_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_putaway_captures")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_putaway_captures_organization_id"),
        "putaway_captures",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_putaway_captures_org_move",
        "putaway_captures",
        ["organization_id", "move_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_putaway_captures_org_location",
        "putaway_captures",
        ["organization_id", "location_id"],
        schema=SCHEMA,
    )
    _rls("putaway_captures")
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.putaway_captures FROM "{APP_ROLE}"')


def _refuse_destructive_downgrade() -> None:
    """Dropping `putaway_captures` destroys the proof that goes with every put-away, and dropping
    the warehouse columns forgets where each site is. Fine on a database that never held either;
    never fine once anyone has used the feature. Refuse unless the operator says, in the
    environment, that destroying them is the point."""
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    real = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT (SELECT count(*) FROM {SCHEMA}.putaway_captures)
                 + (SELECT count(*) FROM {SCHEMA}.warehouses WHERE lat_micro IS NOT NULL)
            """
            )
        )
        .scalar_one()
    )
    if real:
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {real} putaway capture(s) and/or warehouse "
            "coordinate(s) exist and this would destroy them. Roll the application back instead "
            "(the schema is backward compatible). To destroy them deliberately — after a backup — "
            f"set {ALLOW_DESTRUCTIVE_ENV}=1."
        )


def downgrade() -> None:
    _refuse_destructive_downgrade()
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.putaway_captures TO "{APP_ROLE}"')
    _unrls("putaway_captures")
    op.drop_table("putaway_captures", schema=SCHEMA)

    op.drop_constraint(
        op.f("ck_warehouses_lng_in_range"), "warehouses", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        op.f("ck_warehouses_lat_in_range"), "warehouses", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        op.f("ck_warehouses_coords_paired"), "warehouses", schema=SCHEMA, type_="check"
    )
    op.drop_column("warehouses", "lng_micro", schema=SCHEMA)
    op.drop_column("warehouses", "lat_micro", schema=SCHEMA)
