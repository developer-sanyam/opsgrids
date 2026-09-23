"""inventory ledger foundation: locations, milli-unit ledger, balances, reservations

ADR-0014 Phase 0. Moves stock from "a cached integer on the item" to "an append-only ledger of
milli-unit deltas per bucket, with a locked balance projection":

1. **Refuses to start** if any item's stored `on_hand` disagrees with the sum of its movements —
   the migration never silently picks one of two numbers that should be equal.
2. `locations`: one default bin per warehouse (soft-deleted warehouses too, so historical
   movements have somewhere to point).
3. **Live** items whose `warehouse` string names no *live* warehouse get redirected to a
   per-organization `UNASSIGNED` warehouse (by that warehouse's own name — an org that already
   had one under a name of its choosing is matched, not overwritten), so no stock is dropped for
   want of a place, and no live item is left pointed at a bin under a warehouse the live API can
   never address again (`post_move`'s own location lookup excludes soft-deleted warehouses).
   Nothing in the live API can create such an item today; this is for data that predates the
   checks. A soft-deleted item that names only a soft-deleted warehouse is left alone — that is
   coherent history, not a stranded item, and step 4's tie-break still finds it.
4. `inventory_items.home_location_id` ← that warehouse's default bin, preferring a live warehouse
   over a soft-deleted one of the same name. Refuses, naming the item, if one with existing
   movements still has no home afterwards — the alternative is a bare `NOT NULL` violation four
   steps later with nothing to point at.
5. `inventory_moves` (header, one per old movement, **same UUID**), then `stock_movements` is
   renamed `inventory_ledger` in place — row ids unchanged — and its integer quantities become
   milli-units (`× 1000`, exact). `unit_cost_minor` and its new `unit_cost_currency` both arrive
   NULL for every pre-existing row: the currency a cost was struck in was never recorded either,
   so neither is invented (ADR-0014 §8, revised for the currency defect found in review — a
   receipt's cost and the item's own stock-value currency are not always the same, and this
   migration does not assume they are).
6. `inventory_balances` backfilled as `SUM(ledger)` per bucket — so balance = ledger by
   construction — and `inventory_items.on_hand` is dropped: one source of truth.
7. `inventory_reservations`, `inventory_discrepancies`.

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy.
`inventory_moves` and `inventory_ledger` are append-only by privilege (re-revoked in
`migrations/env.py` after every grant re-application).

**Downgrade** restores the integer model exactly, and refuses — rather than truncating — when
the data has outgrown it: a fractional quantity, stock outside an item's home bucket, an active
reservation, or a move kind the old vocabulary lacks. The `UNASSIGNED` warehouses this migration
invented are removed only when nothing — a purchase order's destination or an item's `warehouse`
string — still names them.

Revision ID: d4e8b1c93f20
Revises: a1c7fe093d42
Create Date: 2026-09-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8b1c93f20"  # pragma: allowlist secret
down_revision: str | None = "a1c7fe093d42"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
#: Stamped on rows this migration invents, so its downgrade can find exactly those.
MIGRATION_ACTOR = "migration:d4e8b1c93f20"

STATUSES = (
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
STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")"
LEGACY_KINDS = ("opening", "receipt", "adjustment", "count")


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


def _refuse_if(sql: str, message: str) -> None:
    """Abort the migration, naming the problem, if `sql` returns any row."""
    message = message.replace("'", "''")
    op.execute(
        f"""
        DO $$
        DECLARE offender text;
        BEGIN
            SELECT x::text INTO offender FROM ({sql}) AS x LIMIT 1;
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION '{message} (first offender: %)', offender;
            END IF;
        END
        $$
        """
    )


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_{SCHEMA}_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def upgrade() -> None:
    # --- 1. the cached balance must agree with its ledger before we trust either -------------
    _refuse_if(
        f"""
        SELECT i.id, i.sku, i.on_hand, COALESCE(SUM(m.quantity_delta), 0) AS ledger
          FROM {SCHEMA}.inventory_items i
          LEFT JOIN {SCHEMA}.stock_movements m ON m.inventory_item_id = i.id
         GROUP BY i.id, i.sku, i.on_hand
        HAVING i.on_hand <> COALESCE(SUM(m.quantity_delta), 0)
        """,
        "inventory_items.on_hand disagrees with SUM(stock_movements.quantity_delta); "
        "reconcile it before migrating",
    )

    # --- 2. locations ------------------------------------------------------------------------
    op.create_table(
        "locations",
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("kind IN ('zone', 'rack', 'bin')", name=op.f("ck_locations_kind_known")),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_locations_parent_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_locations_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        schema=SCHEMA,
    )
    _org_index("locations")
    op.create_index(
        "uq_locations_org_warehouse_code",
        "locations",
        ["organization_id", "warehouse_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_locations_org_warehouse_default",
        "locations",
        ["organization_id", "warehouse_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    _rls("locations")

    # --- 3. a home for *live* stock whose warehouse string names no *live* warehouse ---------
    # Matched against live warehouses only, mirroring the write path (`get_warehouse_by_name`):
    # a live item whose `warehouse` string names nothing but a soft-deleted warehouse is exactly
    # as stranded as one naming nothing at all — its ledger rows would end up pointed, in step 4
    # below, at a bin under a warehouse the live API can never address again, and every future
    # write to it (`post_move`'s own location lookup excludes soft-deleted warehouses) would 404.
    #
    # Restricted to *live* items (`i.deleted_at IS NULL`): a soft-deleted item that named a since
    # soft-deleted warehouse is coherent history — it really was there, before both it and its
    # warehouse went away — and step 4's tie-break still finds that warehouse for it. Redirecting
    # it here too would rewrite that history to say it was never assigned anywhere, which is not
    # what happened.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.warehouses
            (id, organization_id, code, name, location, created_at, updated_at, created_by)
        SELECT gen_random_uuid(), o.organization_id, 'UNASSIGNED', 'Unassigned stock', '',
               now(), now(), '{MIGRATION_ACTOR}'
          FROM (SELECT DISTINCT i.organization_id
                  FROM {SCHEMA}.inventory_items i
                 WHERE i.deleted_at IS NULL
                   AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.warehouses w
                                    WHERE w.organization_id = i.organization_id
                                      AND w.name = i.warehouse
                                      AND w.deleted_at IS NULL)) AS o
         WHERE NOT EXISTS (SELECT 1 FROM {SCHEMA}.warehouses w
                            WHERE w.organization_id = o.organization_id
                              AND w.code = 'UNASSIGNED' AND w.deleted_at IS NULL)
        """
    )
    # Redirect to the org's actual UNASSIGNED warehouse **by its own name** — never a hardcoded
    # string — so an org that already had a live warehouse coded UNASSIGNED under a name of its
    # own choosing (from before this migration ever ran) is matched correctly in step 4, instead
    # of being pointed at a name that warehouse does not actually have.
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_items i
           SET warehouse = u.name
          FROM {SCHEMA}.warehouses u
         WHERE i.deleted_at IS NULL
           AND u.organization_id = i.organization_id
           AND u.code = 'UNASSIGNED' AND u.deleted_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.warehouses w
                            WHERE w.organization_id = i.organization_id
                              AND w.name = i.warehouse
                              AND w.deleted_at IS NULL)
        """
    )

    # One default bin per warehouse, live or soft-deleted (a deleted warehouse's bin is deleted
    # with it, and exists only so its history has a location).
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.locations
            (id, organization_id, warehouse_id, parent_id, kind, code, name, is_default,
             created_at, updated_at, created_by, deleted_at)
        SELECT gen_random_uuid(), w.organization_id, w.id, NULL, 'bin', 'DEFAULT',
               'Default bin', true, now(), now(), '{MIGRATION_ACTOR}', w.deleted_at
          FROM {SCHEMA}.warehouses w
        """
    )

    # --- 4. items point at their warehouse's default bin -------------------------------------
    op.add_column(
        "inventory_items", sa.Column("home_location_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_inventory_items_home_location_id_locations"),
        "inventory_items",
        "locations",
        ["home_location_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    # Names are unique among live warehouses (the write path enforces it); prefer the live one,
    # then the lowest code, so the pick is deterministic even over old soft-deleted duplicates.
    # Step 3 already redirected every *live* item that named no live warehouse, so the only thing
    # this can still match to a soft-deleted warehouse is a soft-deleted item — coherent history,
    # not a stranded one (step 3's comment).
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_items i
           SET home_location_id = (
                SELECT l.id
                  FROM {SCHEMA}.warehouses w
                  JOIN {SCHEMA}.locations l ON l.warehouse_id = w.id AND l.is_default
                 WHERE w.organization_id = i.organization_id AND w.name = i.warehouse
                 ORDER BY (w.deleted_at IS NULL) DESC, w.code, w.id
                 LIMIT 1)
        """
    )
    # A NULL home here would surface later as a bare NOT NULL violation on `inventory_ledger.
    # location_id` (step 5b) — this names the item instead, while the column it names is still
    # easy to inspect and fix.
    _refuse_if(
        f"""
        SELECT DISTINCT i.id
          FROM {SCHEMA}.inventory_items i
          JOIN {SCHEMA}.stock_movements m ON m.inventory_item_id = i.id
         WHERE i.home_location_id IS NULL
        """,
        "some items with existing stock movements have no home location after the warehouse "
        "backfill; fix their `warehouse` string, or the warehouse it should name, and re-run",
    )

    # --- 5a. move headers, one per existing movement, same id --------------------------------
    op.create_table(
        "inventory_moves",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=True),
        sa.Column("reference_type", sa.String(length=30), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_moves")),
        schema=SCHEMA,
    )
    _org_index("inventory_moves")
    op.create_index(
        "uq_inventory_moves_org_idempotency_key",
        "inventory_moves",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_moves_org_reference",
        "inventory_moves",
        ["organization_id", "reference_id"],
        unique=False,
        schema=SCHEMA,
    )
    _rls("inventory_moves")
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.inventory_moves
            (id, organization_id, kind, reason, reference_type, reference_id, note, occurred_on,
             created_at, created_by)
        SELECT id, organization_id, kind, reason, reference_type, reference_id, note,
               occurred_on, created_at, created_by
          FROM {SCHEMA}.stock_movements
        """
    )
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.inventory_moves FROM "{APP_ROLE}"')

    # --- 5b. stock_movements becomes inventory_ledger, in place ------------------------------
    op.rename_table("stock_movements", "inventory_ledger", schema=SCHEMA)
    op.execute(
        f"ALTER TABLE {SCHEMA}.inventory_ledger "
        "RENAME CONSTRAINT pk_stock_movements TO pk_inventory_ledger"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.inventory_ledger RENAME CONSTRAINT "
        "fk_stock_movements_inventory_item_id_inventory_items "
        "TO fk_inventory_ledger_inventory_item_id_inventory_items"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_operations_stock_movements_organization_id "
        "RENAME TO ix_operations_inventory_ledger_organization_id"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_stock_movements_org_item_created "
        "RENAME TO ix_inventory_ledger_org_item_created"
    )
    op.drop_constraint(
        "ck_stock_movements_on_hand_after_non_negative", "inventory_ledger", schema=SCHEMA
    )
    op.drop_constraint("ck_stock_movements_quantity_delta_nonzero", "inventory_ledger", schema=SCHEMA)

    for column in (
        sa.Column("move_id", sa.UUID(), nullable=True),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=True),
        sa.Column("balance_after_milli", sa.BigInteger(), nullable=True),
        sa.Column("unit_cost_minor", sa.BigInteger(), nullable=True),
        sa.Column("unit_cost_currency", sa.String(length=3), nullable=True),
    ):
        op.add_column("inventory_ledger", column, schema=SCHEMA)

    # Every existing movement was single-location stock at the item's warehouse, AVAILABLE.
    # Unit cost at the time was never recorded, so it stays NULL rather than being invented.
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_ledger g
           SET move_id = g.id,
               location_id = i.home_location_id,
               status = 'AVAILABLE',
               quantity_milli = g.quantity_delta::bigint * 1000,
               balance_after_milli = g.on_hand_after::bigint * 1000
          FROM {SCHEMA}.inventory_items i
         WHERE i.id = g.inventory_item_id
        """
    )
    for name in ("move_id", "location_id", "status", "quantity_milli", "balance_after_milli"):
        op.alter_column("inventory_ledger", name, nullable=False, schema=SCHEMA)
    for name in (
        "kind",
        "reason",
        "quantity_delta",
        "on_hand_after",
        "reference_type",
        "reference_id",
        "note",
        "occurred_on",
    ):
        op.drop_column("inventory_ledger", name, schema=SCHEMA)

    op.create_check_constraint(
        op.f("ck_inventory_ledger_balance_after_non_negative"),
        "inventory_ledger",
        "balance_after_milli >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inventory_ledger_status_known"), "inventory_ledger", STATUS_CHECK, schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_inventory_ledger_unit_cost_currency_matches_minor"),
        "inventory_ledger",
        "(unit_cost_minor IS NULL) = (unit_cost_currency IS NULL)",
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_inventory_ledger_move_id_inventory_moves"),
        "inventory_ledger",
        "inventory_moves",
        ["move_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_inventory_ledger_location_id_locations"),
        "inventory_ledger",
        "locations",
        ["location_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_inventory_ledger_org_move",
        "inventory_ledger",
        ["organization_id", "move_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inventory_ledger_org_bucket",
        "inventory_ledger",
        ["organization_id", "inventory_item_id", "location_id", "status"],
        unique=False,
        schema=SCHEMA,
    )
    # The rename carried the tenant policy and the privilege revoke along with the table.

    # --- 6. balances = SUM(ledger) per bucket; on_hand stops being stored --------------------
    op.create_table(
        "inventory_balances",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "quantity_milli >= 0", name=op.f("ck_inventory_balances_quantity_non_negative")
        ),
        sa.CheckConstraint(
            "reserved_milli >= 0 AND reserved_milli <= quantity_milli",
            name=op.f("ck_inventory_balances_reserved_within"),
        ),
        sa.CheckConstraint(STATUS_CHECK, name=op.f("ck_inventory_balances_status_known")),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_inventory_balances_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_inventory_balances_location_id_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_balances")),
        schema=SCHEMA,
    )
    _org_index("inventory_balances")
    op.create_index(
        "uq_inventory_balances_bucket",
        "inventory_balances",
        ["organization_id", "inventory_item_id", "location_id", "status", "lot_id", "roll_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_nulls_not_distinct=True,
    )
    _rls("inventory_balances")
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.inventory_balances
            (id, organization_id, inventory_item_id, location_id, status, lot_id, roll_id,
             quantity_milli, reserved_milli, updated_at)
        SELECT gen_random_uuid(), organization_id, inventory_item_id, location_id, status,
               lot_id, roll_id, SUM(quantity_milli), 0, now()
          FROM {SCHEMA}.inventory_ledger
         GROUP BY organization_id, inventory_item_id, location_id, status, lot_id, roll_id
        """
    )
    op.drop_column("inventory_items", "on_hand", schema=SCHEMA)

    # --- 7. reservations and discrepancies ---------------------------------------------------
    op.create_table(
        "inventory_reservations",
        sa.Column("balance_id", sa.UUID(), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("consumed_milli", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reference_type", sa.String(length=30), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "consumed_milli >= 0 AND consumed_milli <= quantity_milli",
            name=op.f("ck_inventory_reservations_consumed_within"),
        ),
        sa.CheckConstraint(
            "quantity_milli > 0", name=op.f("ck_inventory_reservations_quantity_positive")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name=op.f("ck_inventory_reservations_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["balance_id"],
            [f"{SCHEMA}.inventory_balances.id"],
            name=op.f("fk_inventory_reservations_balance_id_inventory_balances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_inventory_reservations_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_reservations")),
        schema=SCHEMA,
    )
    _org_index("inventory_reservations")
    op.create_index(
        "uq_inventory_reservations_org_idempotency_key",
        "inventory_reservations",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_reservations_org_balance_active",
        "inventory_reservations",
        ["organization_id", "balance_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'active'"),
    )
    _rls("inventory_reservations")

    op.create_table(
        "inventory_discrepancies",
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_id", sa.UUID(), nullable=True),
        sa.Column("stored_milli", sa.BigInteger(), nullable=False),
        sa.Column("expected_milli", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('balance', 'reserved')", name=op.f("ck_inventory_discrepancies_kind_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_discrepancies")),
        schema=SCHEMA,
    )
    _org_index("inventory_discrepancies")
    op.create_index(
        "uq_inventory_discrepancies_open_bucket",
        "inventory_discrepancies",
        [
            "organization_id",
            "kind",
            "inventory_item_id",
            "location_id",
            "status",
            "lot_id",
            "roll_id",
        ],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("resolved_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_inventory_discrepancies_org_detected",
        "inventory_discrepancies",
        ["organization_id", "detected_at"],
        unique=False,
        schema=SCHEMA,
    )
    _rls("inventory_discrepancies")


def downgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in LEGACY_KINDS)
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_reservations WHERE status = 'active'",
        "active reservations exist; release them before downgrading",
    )
    _refuse_if(
        f"""
        SELECT id FROM {SCHEMA}.inventory_ledger
         WHERE quantity_milli % 1000 <> 0 OR balance_after_milli % 1000 <> 0
        """,
        "fractional quantities exist; the integer stock model cannot hold them",
    )
    _refuse_if(
        f"""
        SELECT g.id FROM {SCHEMA}.inventory_ledger g
          JOIN {SCHEMA}.inventory_items i ON i.id = g.inventory_item_id
         WHERE g.location_id IS DISTINCT FROM i.home_location_id
            OR g.status <> 'AVAILABLE' OR g.lot_id IS NOT NULL OR g.roll_id IS NOT NULL
        """,
        "stock has moved outside an item's home bucket; the single-location model cannot hold it",
    )
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_moves WHERE kind NOT IN ({kinds})",
        "moves of a kind the old ledger has no word for exist",
    )

    for table in ("inventory_discrepancies", "inventory_reservations"):
        _unrls(table)
        op.drop_table(table, schema=SCHEMA)

    op.add_column(
        "inventory_items",
        sa.Column("on_hand", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_items i
           SET on_hand = (SELECT COALESCE(SUM(b.quantity_milli), 0) / 1000
                            FROM {SCHEMA}.inventory_balances b
                           WHERE b.inventory_item_id = i.id)
        """
    )
    op.alter_column("inventory_items", "on_hand", server_default=None, schema=SCHEMA)
    _unrls("inventory_balances")
    op.drop_table("inventory_balances", schema=SCHEMA)

    # inventory_ledger → stock_movements, pulling the header fields back onto each row.
    op.drop_index("ix_inventory_ledger_org_bucket", table_name="inventory_ledger", schema=SCHEMA)
    op.drop_index("ix_inventory_ledger_org_move", table_name="inventory_ledger", schema=SCHEMA)
    for column in (
        sa.Column("kind", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.String(length=30), nullable=True),
        sa.Column("quantity_delta", sa.Integer(), nullable=True),
        sa.Column("on_hand_after", sa.Integer(), nullable=True),
        sa.Column("reference_type", sa.String(length=30), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
    ):
        op.add_column("inventory_ledger", column, schema=SCHEMA)
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_ledger g
           SET kind = m.kind, reason = m.reason, reference_type = m.reference_type,
               reference_id = m.reference_id, note = m.note, occurred_on = m.occurred_on,
               quantity_delta = (g.quantity_milli / 1000)::integer,
               on_hand_after = (g.balance_after_milli / 1000)::integer
          FROM {SCHEMA}.inventory_moves m
         WHERE m.id = g.move_id
        """
    )
    for name in ("kind", "quantity_delta", "on_hand_after", "note", "occurred_on"):
        op.alter_column("inventory_ledger", name, nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "fk_inventory_ledger_move_id_inventory_moves", "inventory_ledger", schema=SCHEMA
    )
    op.drop_constraint(
        "fk_inventory_ledger_location_id_locations", "inventory_ledger", schema=SCHEMA
    )
    op.drop_constraint("ck_inventory_ledger_status_known", "inventory_ledger", schema=SCHEMA)
    op.drop_constraint(
        "ck_inventory_ledger_balance_after_non_negative", "inventory_ledger", schema=SCHEMA
    )
    op.drop_constraint(
        "ck_inventory_ledger_unit_cost_currency_matches_minor", "inventory_ledger", schema=SCHEMA
    )
    for name in (
        "move_id",
        "location_id",
        "status",
        "lot_id",
        "roll_id",
        "quantity_milli",
        "balance_after_milli",
        "unit_cost_minor",
        "unit_cost_currency",
    ):
        op.drop_column("inventory_ledger", name, schema=SCHEMA)
    # `op.f`: these are the finished names. Without it the naming convention would prefix them
    # with the table's *current* name, `inventory_ledger`, and the next upgrade — which drops
    # them by their `stock_movements` names — would fail.
    op.create_check_constraint(
        op.f("ck_stock_movements_on_hand_after_non_negative"),
        "inventory_ledger",
        "on_hand_after >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_stock_movements_quantity_delta_nonzero"),
        "inventory_ledger",
        "quantity_delta <> 0 OR kind = 'count'",
        schema=SCHEMA,
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_inventory_ledger_org_item_created "
        "RENAME TO ix_stock_movements_org_item_created"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_operations_inventory_ledger_organization_id "
        "RENAME TO ix_operations_stock_movements_organization_id"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.inventory_ledger RENAME CONSTRAINT "
        "fk_inventory_ledger_inventory_item_id_inventory_items "
        "TO fk_stock_movements_inventory_item_id_inventory_items"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.inventory_ledger "
        "RENAME CONSTRAINT pk_inventory_ledger TO pk_stock_movements"
    )
    op.rename_table("inventory_ledger", "stock_movements", schema=SCHEMA)

    _unrls("inventory_moves")
    op.drop_table("inventory_moves", schema=SCHEMA)

    op.drop_constraint(
        "fk_inventory_items_home_location_id_locations", "inventory_items", schema=SCHEMA
    )
    op.drop_column("inventory_items", "home_location_id", schema=SCHEMA)
    _unrls("locations")
    op.drop_table("locations", schema=SCHEMA)
    # The UNASSIGNED warehouses this migration invented, unless something now refers to them —
    # a purchase order's destination, *or* an item's `warehouse` string, which is exactly what
    # step 3 pointed at one of these to begin with. Checking only purchase orders would delete a
    # warehouse still named by a live item, leaving it pointing at nothing.
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.warehouses w
         WHERE w.created_by = '{MIGRATION_ACTOR}'
           AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.purchase_orders p
                            WHERE p.organization_id = w.organization_id AND p.warehouse = w.name)
           AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.inventory_items i
                            WHERE i.organization_id = w.organization_id AND i.warehouse = w.name)
        """
    )
