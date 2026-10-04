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
them, so a database may hold ledger/balance/discrepancy/count-line rows naming a lot or roll that
has no row. Rather than refuse (and strand the stock) or drop the ids (and lose the bucket key),
the upgrade creates a placeholder `LEGACY-…` lot/roll for each distinct (organization, item, id),
in the same organization and (for rolls) with a length equal to the stock ever received into it.
If one id was used with several items, each item gets its own placeholder and its buckets are
re-pointed at it, so no item is left holding stock it cannot move; that is the only rewrite, and
it never changes a quantity. Otherwise nothing is deleted or rewritten.

**Locking.** `lock_timeout` is 5s (fail the deploy, not the service). The foreign keys are added
`NOT VALID`, last, so ledger writes are blocked only for the instant the constraint is created; the
next revision validates them and builds the large-table indexes `CONCURRENTLY`.

**Downgrade** drops the tables and constraints (the ids stay on the ledger as plain UUIDs again)
but **refuses** when any lot or roll other than this migration's own placeholders exists, unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` — it would destroy real data. Prefer rolling the code back.

Every new table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy;
`roll_events` and `lot_genealogy` are append-only (UPDATE/DELETE revoked, and re-revoked in
`migrations/env.py` after every grant re-application).

Revision ID: b7d41e92c05a
Revises: 665e44e243d7
Create Date: 2026-10-04 12:00:00.000000
"""

from __future__ import annotations

import os
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

LOCK_TIMEOUT = "5s"
#: `created_by` of every row this migration makes (the placeholders); anything else is real data.
MIGRATION_MARKER = "migration:b7d41e92c05a"
#: Set to `1` to let `downgrade()` destroy real lots and rolls.
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"

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
    # Fail fast rather than queue: a lock request that waits behind a long transaction makes every
    # later query on that table wait behind *it*. (Local to this transaction.)
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
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
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
    op.create_index(
        "ix_lots_org_item", "lots", ["organization_id", "inventory_item_id"], schema=SCHEMA
    )
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
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
        sa.CheckConstraint(
            "width_mm IS NULL OR width_mm > 0", name=op.f("ck_rolls_width_positive")
        ),
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
    op.create_index(
        "ix_rolls_org_item", "rolls", ["organization_id", "inventory_item_id"], schema=SCHEMA
    )
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
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
    # A lot or roll id is only meaningful together with its item (the posting kernel refuses a
    # lot that "belongs to a different item"), so a placeholder is made per distinct
    # (organization, item, id) — never one placeholder shared by several items, which would leave
    # every item but one with stock it could no longer move. The first claimant of an id keeps
    # the id; any other (organization, item) that used the same id gets a fresh, deterministic one
    # and its buckets are re-pointed at it, in the ledger as well (the admin role that runs
    # migrations owns the tables; append-only is a privilege of the app role). That rewrite is the
    # only place this migration ever changes a row it did not create, it touches nothing when no
    # id is shared across items (the expected case), and it never changes a quantity.
    #
    # Sources: ledger, balances, discrepancies and the open count lines — every table that can
    # carry a bucket key. (Count lines get no foreign key, but approving one posts through the
    # kernel, which needs the lot/roll to exist.)
    bucket_sources = f"""
        SELECT organization_id, inventory_item_id, {{col}} AS id FROM {SCHEMA}.inventory_ledger
         WHERE {{col}} IS NOT NULL
        UNION
        SELECT organization_id, inventory_item_id, {{col}} FROM {SCHEMA}.inventory_balances
         WHERE {{col}} IS NOT NULL
        UNION
        SELECT organization_id, inventory_item_id, {{col}} FROM {SCHEMA}.inventory_discrepancies
         WHERE {{col}} IS NOT NULL
        UNION
        SELECT organization_id, inventory_item_id, {{col}} FROM {SCHEMA}.count_lines
         WHERE {{col}} IS NOT NULL
    """
    for kind, col, table in (("lot", "lot_id", "lots"), ("roll", "roll_id", "rolls")):
        op.execute(
            f"""
            CREATE TEMPORARY TABLE _legacy_{kind}_map ON COMMIT DROP AS
            SELECT u.organization_id, u.inventory_item_id, u.id AS old_id,
                   CASE WHEN row_number() OVER (
                            PARTITION BY u.id
                            ORDER BY u.organization_id::text, u.inventory_item_id::text) = 1
                        THEN u.id
                        ELSE md5(u.organization_id::text || ':' || u.id::text || ':'
                                 || u.inventory_item_id::text)::uuid
                   END AS new_id
              FROM ({bucket_sources.format(col=col)}) u
            """
        )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.lots (id, organization_id, inventory_item_id, lot_no, quality_status,
                                   created_by)
        SELECT m.new_id, m.organization_id, m.inventory_item_id,
               'LEGACY-' || substr(replace(m.new_id::text, '-', ''), 1, 12),
               'APPROVED', '{MIGRATION_MARKER}'
          FROM _legacy_lot_map m
        """
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.rolls (id, organization_id, inventory_item_id, roll_no, state,
                                    primary_milli, created_by)
        SELECT m.new_id, m.organization_id, m.inventory_item_id,
               'LEGACY-' || substr(replace(m.new_id::text, '-', ''), 1, 12), 'ACTIVE',
               COALESCE(r.received, 0), '{MIGRATION_MARKER}'
          FROM _legacy_roll_map m
          LEFT JOIN (
              SELECT organization_id, inventory_item_id, roll_id,
                     SUM(GREATEST(quantity_milli, 0)) AS received
                FROM {SCHEMA}.inventory_ledger WHERE roll_id IS NOT NULL
               GROUP BY organization_id, inventory_item_id, roll_id
          ) r ON r.organization_id = m.organization_id
             AND r.inventory_item_id = m.inventory_item_id AND r.roll_id = m.old_id
        """
    )
    conn = op.get_bind()
    for kind, col in (("lot", "lot_id"), ("roll", "roll_id")):
        shared = conn.execute(
            sa.text(f"SELECT count(*) FROM _legacy_{kind}_map WHERE new_id <> old_id")
        ).scalar_one()
        if not shared:
            continue
        for table in (*BUCKET_TABLES, "count_lines"):
            op.execute(
                f"""
                UPDATE {SCHEMA}.{table} t SET {col} = m.new_id
                  FROM _legacy_{kind}_map m
                 WHERE m.new_id <> m.old_id
                   AND t.organization_id = m.organization_id
                   AND t.inventory_item_id = m.inventory_item_id
                   AND t.{col} = m.old_id
                """
            )

    # --- the foreign keys, last -------------------------------------------------------------------
    # `NOT VALID`: the constraint is enforced for every new row at once, but the existing rows are
    # not scanned here — adding it validated would hold a lock that blocks ledger writes for as
    # long as the scan takes, and this transaction holds what it takes until it commits. The next
    # revision validates them (`VALIDATE CONSTRAINT` only blocks other DDL, not writes). Added at
    # the very end for the same reason: the shortest possible time with the lock held.
    for table in BUCKET_TABLES:
        for column, target in (("lot", "lots"), ("roll", "rolls")):
            op.create_foreign_key(
                f"fk_{table}_org_{column}",
                table,
                target,
                ["organization_id", f"{column}_id"],
                ["organization_id", "id"],
                source_schema=SCHEMA,
                referent_schema=SCHEMA,
                ondelete="RESTRICT",
                postgresql_not_valid=True,
            )


def _refuse_destructive_downgrade() -> None:
    """Dropping `lots`/`rolls`/`roll_events`/`lot_genealogy` destroys every real lot, roll, split,
    merge and trace — and the bucket foreign keys with them. Fine on a database that only ever held
    this migration's own `LEGACY-…` placeholders; never fine once anyone has registered a lot or a
    roll. Refuse unless the operator says, in the environment, that destroying them is the point."""
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    real = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT (SELECT count(*) FROM {SCHEMA}.lots
                     WHERE created_by IS DISTINCT FROM '{MIGRATION_MARKER}')
                 + (SELECT count(*) FROM {SCHEMA}.rolls
                     WHERE created_by IS DISTINCT FROM '{MIGRATION_MARKER}')
            """
            )
        )
        .scalar_one()
    )
    if real:
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {real} real lot/roll row(s) exist and this would "
            "destroy them, their history and their genealogy. Roll the application back instead "
            "(the schema is backward compatible). To destroy them deliberately — after a backup — "
            f"set {ALLOW_DESTRUCTIVE_ENV}=1."
        )


def downgrade() -> None:
    _refuse_destructive_downgrade()
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
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
