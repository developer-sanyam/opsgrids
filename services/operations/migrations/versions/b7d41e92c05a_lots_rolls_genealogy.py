"""lots, rolls, roll_events, lot_genealogy — and the foreign keys that tie buckets to them

ADR-0014 §5 / inventory Phase 1 (I3). `inventory_ledger`, `inventory_balances` and
`inventory_discrepancies` have carried `lot_id` / `roll_id` since Phase 0 as plain UUIDs so the
bucket key would never change; this migration creates the tables they name and adds the foreign
keys.

1. `lots` — a supply/production batch of one item (internal + supplier lot numbers, dates,
   quality status, source document, flat attributes).
2. `rolls` — a physical roll: primary measured quantity (the item's UOM, milli-units) and a
   secondary one (e.g. 82.5 m / 14.8 kg), width, GSM, shade, grade, `parent_roll_id`.
3. `roll_events` — append-only history of a roll (created, split, merged, moved, …); the edges
   the roll trace walks alongside `rolls.parent_roll_id`.
4. `lot_genealogy` — append-only parent -> child lot edges (the lot trace's graph).

The foreign keys are **organization-scoped**: composite `(organization_id, lot_id)` ->
`lots (organization_id, id)`, so a bucket cannot name another tenant's lot even if a query forgot
the filter. NULL (no lot / no roll) is not checked.

**Existing data.** Phase 0 had no way to set a lot or roll over HTTP, but the kernel accepts
them, so a database may hold ledger/balance/discrepancy rows naming a lot or roll that has no
row. Rather than refuse (and strand the stock) or drop the ids (and lose the bucket key), the
upgrade creates a placeholder `LEGACY-…` lot/roll for each distinct orphan id, in the same
organization and (for rolls) with a length equal to the stock ever received into it. Nothing is
deleted or rewritten. The downgrade drops the tables and constraints; the ids stay on the
ledger as plain UUIDs again.

Every new table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy;
`roll_events` and `lot_genealogy` are append-only (UPDATE/DELETE revoked, and re-revoked in
`migrations/env.py` after every grant re-application).

Revision ID: b7d41e92c05a
Revises: 665e44e243d7
Create Date: 2026-10-04 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d41e92c05a"  # pragma: allowlist secret
down_revision: str | None = "665e44e243d7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"

#: The tables that carry a bucket key and so gain the lot/roll foreign keys.
BUCKET_TABLES = ("inventory_ledger", "inventory_balances", "inventory_discrepancies")


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


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _fk(name: str, local: list[str], target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        local,
        [f"{SCHEMA}.{target}.organization_id", f"{SCHEMA}.{target}.id"],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    # --- lots ------------------------------------------------------------------------------------
    op.create_table(
        "lots",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("lot_no", sa.String(length=60), nullable=False),
        sa.Column("supplier_lot_no", sa.String(length=60), nullable=True),
        sa.Column("manufactured_on", sa.Date(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("quality_status", sa.String(length=12), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "quality_status IN ('PENDING', 'APPROVED', 'ON_HOLD', 'REJECTED')",
            name=op.f("ck_lots_quality_status_known"),
        ),
        sa.CheckConstraint(
            "(source_type IS NULL) = (source_id IS NULL)",
            name=op.f("ck_lots_source_matches_id"),
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_lots_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lots")),
        sa.UniqueConstraint("organization_id", "id", name="uq_lots_org_id"),
        schema=SCHEMA,
    )
    _org_index("lots")
    op.create_index(
        "uq_lots_org_lot_no", "lots", ["organization_id", "lot_no"], unique=True, schema=SCHEMA
    )
    op.create_index("ix_lots_org_item", "lots", ["organization_id", "inventory_item_id"], schema=SCHEMA)
    op.create_index(
        "ix_lots_org_created",
        "lots",
        ["organization_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    _rls("lots")

    # --- rolls -----------------------------------------------------------------------------------
    op.create_table(
        "rolls",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("lot_id", sa.UUID(), nullable=True),
        sa.Column("roll_no", sa.String(length=60), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("primary_milli", sa.BigInteger(), nullable=False),
        sa.Column("secondary_milli", sa.BigInteger(), nullable=True),
        sa.Column("secondary_uom", sa.String(length=10), nullable=True),
        sa.Column("width_mm", sa.Integer(), nullable=True),
        sa.Column("gsm", sa.Integer(), nullable=True),
        sa.Column("shade", sa.String(length=40), nullable=True),
        sa.Column("grade", sa.String(length=20), nullable=True),
        sa.Column("parent_roll_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'SPLIT', 'MERGED')", name=op.f("ck_rolls_state_known")
        ),
        sa.CheckConstraint("primary_milli >= 0", name=op.f("ck_rolls_primary_non_negative")),
        sa.CheckConstraint(
            "secondary_milli IS NULL OR secondary_milli >= 0",
            name=op.f("ck_rolls_secondary_non_negative"),
        ),
        sa.CheckConstraint(
            "(secondary_milli IS NULL) = (secondary_uom IS NULL)",
            name=op.f("ck_rolls_secondary_uom_matches"),
        ),
        sa.CheckConstraint("width_mm IS NULL OR width_mm > 0", name=op.f("ck_rolls_width_positive")),
        sa.CheckConstraint("gsm IS NULL OR gsm > 0", name=op.f("ck_rolls_gsm_positive")),
        sa.CheckConstraint(
            "parent_roll_id IS NULL OR parent_roll_id <> id", name=op.f("ck_rolls_not_own_parent")
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_rolls_inventory_item_id_inventory_items"),
            ondelete="RESTRICT",
        ),
        _fk("fk_rolls_org_lot", ["organization_id", "lot_id"], "lots"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rolls")),
        sa.UniqueConstraint("organization_id", "id", name="uq_rolls_org_id"),
        schema=SCHEMA,
    )
    # Self-reference added after the table (and its `(organization_id, id)` key) exists.
    op.create_foreign_key(
        "fk_rolls_org_parent",
        "rolls",
        "rolls",
        ["organization_id", "parent_roll_id"],
        ["organization_id", "id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    _org_index("rolls")
    op.create_index(
        "uq_rolls_org_roll_no", "rolls", ["organization_id", "roll_no"], unique=True, schema=SCHEMA
    )
    op.create_index("ix_rolls_org_item", "rolls", ["organization_id", "inventory_item_id"], schema=SCHEMA)
    op.create_index(
        "ix_rolls_org_lot",
        "rolls",
        ["organization_id", "lot_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("lot_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rolls_org_parent",
        "rolls",
        ["organization_id", "parent_roll_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("parent_roll_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rolls_org_created",
        "rolls",
        ["organization_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    _rls("rolls")

    # --- roll_events (append-only) ---------------------------------------------------------------
    op.create_table(
        "roll_events",
        sa.Column("roll_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("related_roll_id", sa.UUID(), nullable=True),
        sa.Column("move_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=True),
        sa.Column("secondary_milli", sa.BigInteger(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('CREATED', 'OPENING', 'SPLIT', 'SPLIT_CHILD', 'MERGED', 'MERGE_RESULT', "
            "'MOVED', 'STATUS_CHANGED', 'MEASURED')",
            name=op.f("ck_roll_events_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["move_id"],
            [f"{SCHEMA}.inventory_moves.id"],
            name=op.f("fk_roll_events_move_id_inventory_moves"),
            ondelete="RESTRICT",
        ),
        _fk("fk_roll_events_org_roll", ["organization_id", "roll_id"], "rolls"),
        _fk("fk_roll_events_org_related", ["organization_id", "related_roll_id"], "rolls"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roll_events")),
        schema=SCHEMA,
    )
    _org_index("roll_events")
    op.create_index(
        "ix_roll_events_org_roll",
        "roll_events",
        ["organization_id", "roll_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_roll_events_org_related",
        "roll_events",
        ["organization_id", "related_roll_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("related_roll_id IS NOT NULL"),
    )
    _rls("roll_events")
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.roll_events FROM "{APP_ROLE}"')

    # --- lot_genealogy (append-only) -------------------------------------------------------------
    op.create_table(
        "lot_genealogy",
        sa.Column("parent_lot_id", sa.UUID(), nullable=False),
        sa.Column("child_lot_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.String(length=10), nullable=False),
        sa.Column("move_id", sa.UUID(), nullable=True),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        _now(),
        sa.CheckConstraint(
            "relation IN ('SPLIT', 'MERGE', 'BLEND', 'REWORK', 'DERIVED')",
            name=op.f("ck_lot_genealogy_relation_known"),
        ),
        sa.CheckConstraint(
            "parent_lot_id <> child_lot_id OR relation IN ('SPLIT', 'MERGE')",
            name=op.f("ck_lot_genealogy_self_edge_only_for_roll_ops"),
        ),
        sa.ForeignKeyConstraint(
            ["move_id"],
            [f"{SCHEMA}.inventory_moves.id"],
            name=op.f("fk_lot_genealogy_move_id_inventory_moves"),
            ondelete="RESTRICT",
        ),
        _fk("fk_lot_genealogy_org_parent", ["organization_id", "parent_lot_id"], "lots"),
        _fk("fk_lot_genealogy_org_child", ["organization_id", "child_lot_id"], "lots"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lot_genealogy")),
        schema=SCHEMA,
    )
    _org_index("lot_genealogy")
    op.create_index(
        "ix_lot_genealogy_org_parent",
        "lot_genealogy",
        ["organization_id", "parent_lot_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_lot_genealogy_org_child",
        "lot_genealogy",
        ["organization_id", "child_lot_id"],
        schema=SCHEMA,
    )
    _rls("lot_genealogy")
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.lot_genealogy FROM "{APP_ROLE}"')

    # --- existing buckets that already name a lot or roll ----------------------------------------
    # One placeholder per distinct (organization, id). A lot belongs to one item, so an id used
    # with several items is attached to the lowest item id (the data was never valid there).
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.lots (id, organization_id, inventory_item_id, lot_no, quality_status,
                                   created_by)
        SELECT u.lot_id, u.organization_id, min(u.inventory_item_id::text)::uuid,
               'LEGACY-' || substr(replace(u.lot_id::text, '-', ''), 1, 12),
               'APPROVED', 'migration:b7d41e92c05a'
          FROM (
              SELECT organization_id, inventory_item_id, lot_id FROM {SCHEMA}.inventory_ledger
               WHERE lot_id IS NOT NULL
              UNION
              SELECT organization_id, inventory_item_id, lot_id FROM {SCHEMA}.inventory_balances
               WHERE lot_id IS NOT NULL
              UNION
              SELECT organization_id, inventory_item_id, lot_id
                FROM {SCHEMA}.inventory_discrepancies WHERE lot_id IS NOT NULL
          ) u
         GROUP BY u.organization_id, u.lot_id
        """
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.rolls (id, organization_id, inventory_item_id, roll_no, state,
                                    primary_milli, created_by)
        SELECT u.roll_id, u.organization_id, min(u.inventory_item_id::text)::uuid,
               'LEGACY-' || substr(replace(u.roll_id::text, '-', ''), 1, 12), 'ACTIVE',
               COALESCE(max(u.received), 0), 'migration:b7d41e92c05a'
          FROM (
              SELECT organization_id, inventory_item_id, roll_id,
                     SUM(GREATEST(quantity_milli, 0))
                         OVER (PARTITION BY organization_id, roll_id) AS received
                FROM {SCHEMA}.inventory_ledger WHERE roll_id IS NOT NULL
              UNION
              SELECT organization_id, inventory_item_id, roll_id, NULL::bigint
                FROM {SCHEMA}.inventory_balances WHERE roll_id IS NOT NULL
              UNION
              SELECT organization_id, inventory_item_id, roll_id, NULL::bigint
                FROM {SCHEMA}.inventory_discrepancies WHERE roll_id IS NOT NULL
          ) u
         GROUP BY u.organization_id, u.roll_id
        """
    )

    # --- the foreign keys and the read-path indexes on the existing tables -----------------------
    for table in BUCKET_TABLES:
        op.create_foreign_key(
            f"fk_{table}_org_lot",
            table,
            "lots",
            ["organization_id", "lot_id"],
            ["organization_id", "id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{table}_org_roll",
            table,
            "rolls",
            ["organization_id", "roll_id"],
            ["organization_id", "id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="RESTRICT",
        )

    op.create_index(
        "ix_inventory_ledger_org_created",
        "inventory_ledger",
        ["organization_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inventory_ledger_org_location_created",
        "inventory_ledger",
        ["organization_id", "location_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inventory_ledger_org_lot_created",
        "inventory_ledger",
        ["organization_id", "lot_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("lot_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_ledger_org_roll_created",
        "inventory_ledger",
        ["organization_id", "roll_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("roll_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_balances_org_location",
        "inventory_balances",
        ["organization_id", "location_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inventory_balances_org_lot",
        "inventory_balances",
        ["organization_id", "lot_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("lot_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_balances_org_roll",
        "inventory_balances",
        ["organization_id", "roll_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("roll_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inventory_moves_org_occurred",
        "inventory_moves",
        ["organization_id", sa.text("occurred_on DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_moves_org_occurred", table_name="inventory_moves", schema=SCHEMA)
    for name, table in (
        ("ix_inventory_balances_org_roll", "inventory_balances"),
        ("ix_inventory_balances_org_lot", "inventory_balances"),
        ("ix_inventory_balances_org_location", "inventory_balances"),
        ("ix_inventory_ledger_org_roll_created", "inventory_ledger"),
        ("ix_inventory_ledger_org_lot_created", "inventory_ledger"),
        ("ix_inventory_ledger_org_location_created", "inventory_ledger"),
        ("ix_inventory_ledger_org_created", "inventory_ledger"),
    ):
        op.drop_index(name, table_name=table, schema=SCHEMA)

    for table in BUCKET_TABLES:
        op.drop_constraint(f"fk_{table}_org_roll", table, schema=SCHEMA, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_org_lot", table, schema=SCHEMA, type_="foreignkey")

    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.lot_genealogy TO "{APP_ROLE}"')
    _unrls("lot_genealogy")
    op.drop_table("lot_genealogy", schema=SCHEMA)

    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.roll_events TO "{APP_ROLE}"')
    _unrls("roll_events")
    op.drop_table("roll_events", schema=SCHEMA)

    _unrls("rolls")
    op.drop_table("rolls", schema=SCHEMA)

    _unrls("lots")
    op.drop_table("lots", schema=SCHEMA)
