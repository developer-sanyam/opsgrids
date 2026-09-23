"""item master: categories, typed attributes, variants, tracking flags, HSN, secondary UOM

ADR-0014 Phase 1, plan §2 item 2. Extends the item master `inventory_items` already is
(ADR-0014 §10: splitting it was rejected) rather than creating a parallel one:

1. `item_categories` — an org-authored category tree, additive to the fixed procurement
   `category` enum `inventory_items.category` already carries. Adjacency list (`parent_id`),
   the same shape `locations` already uses for zone → rack → bin.
2. `item_attributes` — typed key/value facts (GSM, width, shade, composition, …). Exactly one of
   `value_text` / `value_number` / `value_boolean` / `value_date` is set, enforced by a CHECK —
   there is no untyped `value` column for a caller to mass-assign into.
3. `item_variants` — a purchasable/stockable variant of an item, its own SKU.
4. `inventory_items` gains: `item_category_id` (FK, optional), `secondary_uom_id` (FK into
   `uoms`, optional — the ratio to the stock `uom` is a per-item `uom_conversions` row),
   `hsn_code`, `track_lot`/`track_roll`/`track_expiry`/`track_quality` (booleans, default off),
   `is_active` (default on).

Every new column is nullable or carries a safe default, and every new table is empty at first —
this migration invents no data and cannot lose any. Every new tenant table gets ENABLE + FORCE
RLS and the standard `tenant_isolation` policy.

**Downgrade** drops the three new tables and the new columns. It refuses if any item was made
inactive, tracks lot/roll/expiry/quality, or carries a category/secondary-UOM/HSN code — the old
shape has nowhere to keep any of that — mirroring `37e17a153b64`'s refusal for `uom_conversions`.

Revision ID: 7899dcdf7534
Revises: 37e17a153b64
Create Date: 2026-09-23 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7899dcdf7534"  # pragma: allowlist secret
down_revision: str | None = "37e17a153b64"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
ATTRIBUTE_TYPE_CHECK = "value_type IN ('text', 'number', 'boolean', 'date')"


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
    # --- item_categories -----------------------------------------------------------------
    op.create_table(
        "item_categories",
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [f"{SCHEMA}.item_categories.id"],
            name=op.f("fk_item_categories_parent_id_item_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_categories")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_item_categories_organization_id"),
        "item_categories",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_item_categories_org_code",
        "item_categories",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_item_categories_org_parent",
        "item_categories",
        ["organization_id", "parent_id"],
        schema=SCHEMA,
    )
    _rls("item_categories")

    # --- inventory_items: new columns ------------------------------------------------------
    op.add_column(
        "inventory_items", sa.Column("item_category_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_inventory_items_item_category_id_item_categories"),
        "inventory_items",
        "item_categories",
        ["item_category_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_inventory_items_org_item_category",
        "inventory_items",
        ["organization_id", "item_category_id"],
        schema=SCHEMA,
    )
    op.add_column(
        "inventory_items", sa.Column("secondary_uom_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_inventory_items_secondary_uom_id_uoms"),
        "inventory_items",
        "uoms",
        ["secondary_uom_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.add_column(
        "inventory_items",
        sa.Column("hsn_code", sa.String(length=20), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.alter_column("inventory_items", "hsn_code", server_default=None, schema=SCHEMA)
    for flag in ("track_lot", "track_roll", "track_expiry", "track_quality"):
        op.add_column(
            "inventory_items",
            sa.Column(flag, sa.Boolean(), nullable=False, server_default=sa.false()),
            schema=SCHEMA,
        )
        op.alter_column("inventory_items", flag, server_default=None, schema=SCHEMA)
    op.add_column(
        "inventory_items",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=SCHEMA,
    )
    op.alter_column("inventory_items", "is_active", server_default=None, schema=SCHEMA)

    # --- item_attributes ---------------------------------------------------------------------
    op.create_table(
        "item_attributes",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value_type", sa.String(length=10), nullable=False),
        sa.Column("value_text", sa.String(length=500), nullable=True),
        sa.Column("value_number", sa.BigInteger(), nullable=True),
        sa.Column("value_boolean", sa.Boolean(), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
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
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(ATTRIBUTE_TYPE_CHECK, name=op.f("ck_item_attributes_value_type_known")),
        sa.CheckConstraint(
            "(CASE WHEN value_text IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN value_number IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN value_boolean IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN value_date IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name=op.f("ck_item_attributes_exactly_one_value"),
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_item_attributes_inventory_item_id_inventory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_attributes")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_item_attributes_organization_id"),
        "item_attributes",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_item_attributes_org_item_key",
        "item_attributes",
        ["organization_id", "inventory_item_id", "key"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("item_attributes")

    # --- item_variants -------------------------------------------------------------------
    op.create_table(
        "item_variants",
        sa.Column("inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("sku", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            [f"{SCHEMA}.inventory_items.id"],
            name=op.f("fk_item_variants_inventory_item_id_inventory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_variants")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_item_variants_organization_id"),
        "item_variants",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_item_variants_org_sku",
        "item_variants",
        ["organization_id", "sku"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_item_variants_org_item",
        "item_variants",
        ["organization_id", "inventory_item_id"],
        schema=SCHEMA,
    )
    _rls("item_variants")


def downgrade() -> None:
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.item_variants",
        "item variants exist; the pre-Phase-1 item master has nowhere to keep them",
    )
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.item_attributes",
        "item attributes exist; the pre-Phase-1 item master has nowhere to keep them",
    )
    _refuse_if(
        f"""
        SELECT id FROM {SCHEMA}.inventory_items
         WHERE item_category_id IS NOT NULL OR secondary_uom_id IS NOT NULL OR hsn_code <> ''
            OR track_lot OR track_roll OR track_expiry OR track_quality OR NOT is_active
        """,
        "an item carries a Phase 1 item-master fact (category, secondary UOM, HSN code, a "
        "tracking flag, or inactive status) the pre-Phase-1 shape has no column for",
    )

    _unrls("item_variants")
    op.drop_table("item_variants", schema=SCHEMA)

    _unrls("item_attributes")
    op.drop_table("item_attributes", schema=SCHEMA)

    op.drop_column("inventory_items", "is_active", schema=SCHEMA)
    for flag in ("track_quality", "track_expiry", "track_roll", "track_lot"):
        op.drop_column("inventory_items", flag, schema=SCHEMA)
    op.drop_column("inventory_items", "hsn_code", schema=SCHEMA)
    op.drop_constraint("fk_inventory_items_secondary_uom_id_uoms", "inventory_items", schema=SCHEMA)
    op.drop_column("inventory_items", "secondary_uom_id", schema=SCHEMA)
    op.drop_index(
        "ix_inventory_items_org_item_category", table_name="inventory_items", schema=SCHEMA
    )
    op.drop_constraint(
        "fk_inventory_items_item_category_id_item_categories", "inventory_items", schema=SCHEMA
    )
    op.drop_column("inventory_items", "item_category_id", schema=SCHEMA)

    _unrls("item_categories")
    op.drop_table("item_categories", schema=SCHEMA)
