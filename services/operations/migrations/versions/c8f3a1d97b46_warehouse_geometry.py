"""warehouse geometry: warehouse_floors, layout_versions, location_placements, walk_nodes,
walk_edges

Plan §2's domain-model table (`docs/fulfilment/plan.md`) named this group as schema-only until a
phase needed it; F7 is that phase. `location_rules` (also named in the plan's table) is not built
here — nothing yet needs a rule beyond the overlap/connectivity checks `app.domain` runs at
validate/publish time.

1. `warehouse_floors` — one row per physical floor of a warehouse.
2. `locations.floor_id` — extends the existing F1 `Location` row (never a second identity table)
   with which floor it sits on. Nullable: a location predates having a floor plan at all.
3. `layout_versions` — a floor's versioned layout: `draft -> published -> archived -> published`
   (a rollback). At most one `draft` and one `published` per floor (two partial unique indexes).
4. `location_placements` — where one location sits **in one layout version** — kept off
   `locations` itself so a draft's coordinates can never leak into what is published (two
   different versions may each place the same location differently at once).
5. `walk_nodes` / `walk_edges` — the routing graph over one layout version, weighted by
   `distance_mm` for `app.domain.shortest_walk_path`'s Dijkstra search.

Every new tenant table gets ENABLE + FORCE RLS and the standard `tenant_isolation` policy, the
same shape every migration since `c3f1a7e5b2d9` uses.

Revision ID: c8f3a1d97b46
Revises: 393a08e3886a
Create Date: 2026-09-29 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f3a1d97b46"  # pragma: allowlist secret
down_revision: str | None = "393a08e3886a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"


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
    # --- warehouse_floors ----------------------------------------------------------------------
    op.create_table(
        "warehouse_floors",
        sa.Column("warehouse_id", sa.UUID(), nullable=False),
        sa.Column("floor_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            [f"{SCHEMA}.warehouses.id"],
            name=op.f("fk_warehouse_floors_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warehouse_floors")),
        schema=SCHEMA,
    )
    _org_index("warehouse_floors")
    op.create_index(
        "uq_warehouse_floors_org_warehouse_number",
        "warehouse_floors",
        ["organization_id", "warehouse_id", "floor_number"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_warehouse_floors_org_warehouse",
        "warehouse_floors",
        ["organization_id", "warehouse_id"],
        schema=SCHEMA,
    )
    _rls("warehouse_floors")

    # --- locations.floor_id (extends the existing F1 table, never a second one) ----------------
    op.add_column(
        "locations", sa.Column("floor_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_locations_floor_id_warehouse_floors"),
        "locations",
        "warehouse_floors",
        ["floor_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_locations_org_floor", "locations", ["organization_id", "floor_id"], schema=SCHEMA
    )

    # --- layout_versions -------------------------------------------------------------------------
    op.create_table(
        "layout_versions",
        sa.Column("warehouse_floor_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_ts_columns(),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name=op.f("ck_layout_versions_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_floor_id"],
            [f"{SCHEMA}.warehouse_floors.id"],
            name=op.f("fk_layout_versions_warehouse_floor_id_warehouse_floors"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_layout_versions")),
        schema=SCHEMA,
    )
    _org_index("layout_versions")
    op.create_index(
        "uq_layout_versions_org_floor_number",
        "layout_versions",
        ["organization_id", "warehouse_floor_id", "version_number"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_layout_versions_org_floor_draft",
        "layout_versions",
        ["organization_id", "warehouse_floor_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'draft' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_layout_versions_org_floor_published",
        "layout_versions",
        ["organization_id", "warehouse_floor_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_layout_versions_org_floor",
        "layout_versions",
        ["organization_id", "warehouse_floor_id"],
        schema=SCHEMA,
    )
    _rls("layout_versions")

    # --- location_placements ----------------------------------------------------------------
    op.create_table(
        "location_placements",
        sa.Column("layout_version_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("x_mm", sa.Integer(), nullable=False),
        sa.Column("y_mm", sa.Integer(), nullable=False),
        sa.Column("z_mm", sa.Integer(), nullable=True),
        sa.Column("width_mm", sa.Integer(), nullable=False),
        sa.Column("depth_mm", sa.Integer(), nullable=False),
        sa.Column("height_mm", sa.Integer(), nullable=True),
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
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "width_mm > 0 AND depth_mm > 0", name=op.f("ck_location_placements_footprint_positive")
        ),
        sa.CheckConstraint(
            "height_mm IS NULL OR height_mm > 0",
            name=op.f("ck_location_placements_height_positive_when_present"),
        ),
        sa.ForeignKeyConstraint(
            ["layout_version_id"],
            [f"{SCHEMA}.layout_versions.id"],
            name=op.f("fk_location_placements_layout_version_id_layout_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_location_placements_location_id_locations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_placements")),
        schema=SCHEMA,
    )
    _org_index("location_placements")
    op.create_index(
        "uq_location_placements_org_version_location",
        "location_placements",
        ["organization_id", "layout_version_id", "location_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_location_placements_org_location",
        "location_placements",
        ["organization_id", "location_id"],
        schema=SCHEMA,
    )
    _rls("location_placements")

    # --- walk_nodes ----------------------------------------------------------------------------
    op.create_table(
        "walk_nodes",
        sa.Column("layout_version_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("x_mm", sa.Integer(), nullable=False),
        sa.Column("y_mm", sa.Integer(), nullable=False),
        sa.Column("z_mm", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["layout_version_id"],
            [f"{SCHEMA}.layout_versions.id"],
            name=op.f("fk_walk_nodes_layout_version_id_layout_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            [f"{SCHEMA}.locations.id"],
            name=op.f("fk_walk_nodes_location_id_locations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_walk_nodes")),
        schema=SCHEMA,
    )
    _org_index("walk_nodes")
    op.create_index(
        "uq_walk_nodes_org_version_code",
        "walk_nodes",
        ["organization_id", "layout_version_id", "code"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_walk_nodes_org_version_location",
        "walk_nodes",
        ["organization_id", "layout_version_id", "location_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("location_id IS NOT NULL"),
    )
    op.create_index(
        "ix_walk_nodes_org_version", "walk_nodes", ["organization_id", "layout_version_id"],
        schema=SCHEMA,
    )
    _rls("walk_nodes")

    # --- walk_edges ----------------------------------------------------------------------------
    op.create_table(
        "walk_edges",
        sa.Column("layout_version_id", sa.UUID(), nullable=False),
        sa.Column("from_node_id", sa.UUID(), nullable=False),
        sa.Column("to_node_id", sa.UUID(), nullable=False),
        sa.Column("distance_mm", sa.BigInteger(), nullable=False),
        sa.Column("is_traversable", sa.Boolean(), nullable=False),
        sa.Column("bidirectional", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("distance_mm > 0", name=op.f("ck_walk_edges_distance_positive")),
        sa.CheckConstraint(
            "from_node_id != to_node_id", name=op.f("ck_walk_edges_no_self_loop")
        ),
        sa.ForeignKeyConstraint(
            ["layout_version_id"],
            [f"{SCHEMA}.layout_versions.id"],
            name=op.f("fk_walk_edges_layout_version_id_layout_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_node_id"],
            [f"{SCHEMA}.walk_nodes.id"],
            name=op.f("fk_walk_edges_from_node_id_walk_nodes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id"],
            [f"{SCHEMA}.walk_nodes.id"],
            name=op.f("fk_walk_edges_to_node_id_walk_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_walk_edges")),
        schema=SCHEMA,
    )
    _org_index("walk_edges")
    op.create_index(
        "uq_walk_edges_org_version_nodes",
        "walk_edges",
        ["organization_id", "layout_version_id", "from_node_id", "to_node_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_walk_edges_org_version", "walk_edges", ["organization_id", "layout_version_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_walk_edges_org_from_node", "walk_edges", ["organization_id", "from_node_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_walk_edges_org_to_node", "walk_edges", ["organization_id", "to_node_id"],
        schema=SCHEMA,
    )
    _rls("walk_edges")


def downgrade() -> None:
    _unrls("walk_edges")
    op.drop_table("walk_edges", schema=SCHEMA)

    _unrls("walk_nodes")
    op.drop_table("walk_nodes", schema=SCHEMA)

    _unrls("location_placements")
    op.drop_table("location_placements", schema=SCHEMA)

    _unrls("layout_versions")
    op.drop_table("layout_versions", schema=SCHEMA)

    op.drop_index("ix_locations_org_floor", table_name="locations", schema=SCHEMA)
    op.drop_constraint(
        op.f("fk_locations_floor_id_warehouse_floors"),
        "locations",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("locations", "floor_id", schema=SCHEMA)

    _unrls("warehouse_floors")
    op.drop_table("warehouse_floors", schema=SCHEMA)
