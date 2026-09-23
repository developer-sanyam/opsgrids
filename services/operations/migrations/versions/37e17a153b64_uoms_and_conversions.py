"""uoms and uom_conversions

ADR-0014 Phase 1, plan §2 item 1. `inventory_items.uom` was free text (ADR-0014's own §10
revision, and Phase 0's "Differences" note: "No `uoms` table yet"). This adds the per-organization
directory and gives every item a real foreign key into it, without changing what the wire has
always carried:

1. `uoms` — seeded per organization with the fixed code registry
   (`app.inventory.quantity.UNITS`, inlined below rather than imported: a migration is a snapshot
   of what it did, and must not change behaviour if the registry ever does), `is_system=True`.
   Seeded for every organization this schema already has data for — found the same way
   `d4e8b1c93f20` found one to invent an `UNASSIGNED` warehouse for: distinct `organization_id`
   across every tenant table this service owns.
2. `uom_conversions` — item-specific rational ratios (`numerator`/`denominator`, integers only).
   Empty after this migration; Phase 1's item master API is what writes to it.
3. `inventory_items.uom_id`, backfilled from the free-text `uom` column: matched
   case-insensitively against the seeded codes first, then a custom `is_system=False` row is
   created per organization for every distinct (lower-cased) free-text unit that matched nothing
   — so **no unit string is lost**, exactly as the item's own `uom` column is not touched (it stays
   the denormalised value the kernel and the wire have always read; `uom_id` is additive
   referential integrity, the same relationship `warehouse` (string) and `home_location_id` (FK)
   already have on this table).

Alias folding (`"meter"` → `m`, `"kgs"` → `kg`, …) **is** applied here, via `SEED_ALIASES` — an
inlined copy of `app.inventory.quantity._ALIASES` at the time this migration was written, for the
same reason `SEED_UNITS` is a copy rather than an import. Without it, an item stocked in `"meter"`
would backfill to its own custom unit instead of the seeded `m`, while a new item created after
this migration through `app.inventory.services.resolve_or_create_uom` (which folds the same
aliases) would land on `m` — two rows meaning the same thing. Folding here keeps backfill and the
live write path agreeing on every spelling either one recognises.

Backward compatible: additive tables, and `inventory_items.uom_id` is nullable (an item with a
blank `uom` — the create API's own default before Phase 1 required one — gets no `uom_id` either,
same as it gets no `home_location_id` without a `warehouse`).

**Downgrade** refuses if any `uom_conversions` row exists — an item-specific ratio has no analog
anywhere else and would be lost outright — but otherwise drops both tables and `uom_id`: the
`uom` string itself is untouched throughout, so nothing else is lost.

Revision ID: 37e17a153b64
Revises: 46e1eade7b18
Create Date: 2026-09-23 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "37e17a153b64"  # pragma: allowlist secret
down_revision: str | None = "46e1eade7b18"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
MIGRATION_ACTOR = "migration:37e17a153b64"

#: Inlined copy of `app.inventory.quantity.UNITS` + `UNIT_NAMES` at the time this migration was
#: written (code, name, dimension, is_discrete). See the module docstring for why this is a copy
#: rather than an import.
SEED_UNITS: tuple[tuple[str, str, str, bool], ...] = (
    ("mm", "Millimetre", "length", False),
    ("cm", "Centimetre", "length", False),
    ("m", "Metre", "length", False),
    ("in", "Inch", "length", False),
    ("yd", "Yard", "length", False),
    ("mg", "Milligram", "mass", False),
    ("g", "Gram", "mass", False),
    ("kg", "Kilogram", "mass", False),
    ("t", "Tonne", "mass", False),
    ("ml", "Millilitre", "volume", False),
    ("l", "Litre", "volume", False),
    ("m2", "Square metre", "area", False),
    ("pcs", "Piece", "count", True),
    ("dozen", "Dozen", "count", True),
    ("box", "Box", "box", True),
    ("carton", "Carton", "carton", True),
    ("bundle", "Bundle", "bundle", True),
    ("set", "Set", "set", True),
    ("roll", "Roll", "roll", True),
)

#: Inlined copy of `app.inventory.quantity._ALIASES` at the time this migration was written — see
#: the module docstring for why this is a copy rather than an import.
SEED_ALIASES: tuple[tuple[str, str], ...] = (
    ("pc", "pcs"),
    ("piece", "pcs"),
    ("pieces", "pcs"),
    ("nos", "pcs"),
    ("ea", "pcs"),
    ("each", "pcs"),
    ("units", "pcs"),
    ("meter", "m"),
    ("meters", "m"),
    ("metre", "m"),
    ("metres", "m"),
    ("mtr", "m"),
    ("kgs", "kg"),
    ("gm", "g"),
    ("gms", "g"),
    ("grams", "g"),
    ("litre", "l"),
    ("liter", "l"),
    ("ltr", "l"),
    ("boxes", "box"),
    ("cartons", "carton"),
    ("bundles", "bundle"),
    ("sets", "set"),
    ("rolls", "roll"),
)


def _fold_uom_sql(column: str) -> str:
    """SQL for folding a raw `uom` string the same way `app.inventory.quantity.unit()` does:
    trim, lower-case, then the alias table — anything left over is its own canonical spelling.
    Used everywhere this migration matches or groups free-text units, so backfill agrees with
    what `resolve_or_create_uom` would resolve the same string to."""
    cases = " ".join(f"WHEN '{alias}' THEN '{code}'" for alias, code in SEED_ALIASES)
    return f"(CASE lower(trim({column})) {cases} ELSE lower(trim({column})) END)"


#: Every tenant table this service owns that could name an organization no other table does.
ORG_SOURCE_TABLES = (
    "warehouses",
    "suppliers",
    "purchase_orders",
    "inventory_items",
    "locations",
)


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


def upgrade() -> None:
    # --- uoms ----------------------------------------------------------------------------
    op.create_table(
        "uoms",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("dimension", sa.String(length=20), nullable=False),
        sa.Column("is_discrete", sa.Boolean(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uoms")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_uoms_organization_id"), "uoms", ["organization_id"], schema=SCHEMA
    )
    op.create_index(
        "uq_uoms_org_code",
        "uoms",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_uoms_org_dimension", "uoms", ["organization_id", "dimension"], schema=SCHEMA
    )
    _rls("uoms")

    # One row per (organization, seed unit) for every organization this schema already has data
    # for — a fresh organization with none yet gets its directory lazily, the first time
    # `app.inventory.services.ensure_seed_uoms` runs for it (same reasoning as a warehouse's
    # default bin: reference data is seeded on first touch, not by a hook this service has no
    # organization-creation event to hang it on).
    org_union = " UNION ".join(
        f"SELECT organization_id FROM {SCHEMA}.{t}" for t in ORG_SOURCE_TABLES
    )
    values = ", ".join(
        f"('{code}', '{name}', '{dim}', {str(discrete).upper()})"
        for code, name, dim, discrete in SEED_UNITS
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.uoms
            (id, organization_id, code, name, dimension, is_discrete, is_system,
             created_at, updated_at, created_by)
        SELECT gen_random_uuid(), o.organization_id, seed.code, seed.name, seed.dimension,
               seed.is_discrete, true, now(), now(), '{MIGRATION_ACTOR}'
          FROM (SELECT DISTINCT organization_id FROM ({org_union}) AS x) AS o
         CROSS JOIN (VALUES {values}) AS seed(code, name, dimension, is_discrete)
        """
    )

    # --- uom_conversions -------------------------------------------------------------------
    op.create_table(
        "uom_conversions",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("uom_id", sa.UUID(), nullable=False),
        sa.Column("numerator", sa.BigInteger(), nullable=False),
        sa.Column("denominator", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("numerator > 0", name=op.f("ck_uom_conversions_numerator_positive")),
        sa.CheckConstraint("denominator > 0", name=op.f("ck_uom_conversions_denominator_positive")),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_uom_conversions_inventory_item_id_inventory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uom_id"],
            [f"{SCHEMA}.uoms.id"],
            name=op.f("fk_uom_conversions_uom_id_uoms"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uom_conversions")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_uom_conversions_organization_id"),
        "uom_conversions",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_uom_conversions_org_item_uom",
        "uom_conversions",
        ["organization_id", "inventory_item_id", "uom_id"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("uom_conversions")

    # --- inventory_items.uom_id --------------------------------------------------------------
    op.add_column("inventory_items", sa.Column("uom_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.create_foreign_key(
        op.f("fk_inventory_items_uom_id_uoms"),
        "inventory_items",
        "uoms",
        ["uom_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_inventory_items_org_uom",
        "inventory_items",
        ["organization_id", "uom_id"],
        schema=SCHEMA,
    )

    # A custom row per organization for every distinct alias-and-case-folded free-text unit that
    # matches none of the seed codes — one per folded spelling, so "MT"/"mt" and "meter"/"metre"
    # (an alias of the seeded `m`, so it never reaches here) each collapse the way the live write
    # path would collapse them. `DISTINCT ON` picks one original casing deterministically (ordered)
    # to use as the display `name`; the item's own `uom` string is never rewritten, so nothing
    # about what it displays changes.
    folded_uom = _fold_uom_sql("uom")
    folded_i_uom = _fold_uom_sql("i.uom")
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.uoms
            (id, organization_id, code, name, dimension, is_discrete, is_system,
             created_at, updated_at, created_by)
        SELECT gen_random_uuid(), free.organization_id, free.folded, free.uom, 'other',
               false, false, now(), now(), '{MIGRATION_ACTOR}'
          FROM (
                SELECT DISTINCT ON (organization_id, {folded_uom}) organization_id, uom,
                       {folded_uom} AS folded
                  FROM {SCHEMA}.inventory_items
                 WHERE trim(uom) <> ''
                 ORDER BY organization_id, {folded_uom}, uom
               ) AS free
         WHERE NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.uoms u
                 WHERE u.organization_id = free.organization_id
                   AND lower(u.code) = free.folded
                   AND u.deleted_at IS NULL
               )
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.inventory_items i
           SET uom_id = u.id
          FROM {SCHEMA}.uoms u
         WHERE u.organization_id = i.organization_id
           AND lower(u.code) = {folded_i_uom}
           AND u.deleted_at IS NULL
           AND trim(i.uom) <> ''
        """
    )
    # Every non-blank uom must now have a home — the free-text insert above is exhaustive, so a
    # miss here is a bug in this migration, not a data problem to work around.
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_items WHERE trim(uom) <> '' AND uom_id IS NULL",
        "an item's free-text uom did not resolve to any uoms row after backfill",
    )


def downgrade() -> None:
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.uom_conversions",
        "item-specific unit conversions exist; the pre-Phase-1 shape has nowhere to keep them",
    )

    op.drop_constraint("fk_inventory_items_uom_id_uoms", "inventory_items", schema=SCHEMA)
    op.drop_index("ix_inventory_items_org_uom", table_name="inventory_items", schema=SCHEMA)
    op.drop_column("inventory_items", "uom_id", schema=SCHEMA)

    _unrls("uom_conversions")
    op.drop_table("uom_conversions", schema=SCHEMA)

    _unrls("uoms")
    op.drop_table("uoms", schema=SCHEMA)
