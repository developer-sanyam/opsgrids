"""activation sources, and the suite as a non-routable bundle product

A bundle buyer was entitled to nothing. A `suite/trio` subscription's lifecycle events named
`suite`, the projection upserted one activation for `suite`, and no gateway route checks `suite`.
Fixing that means one subscription switching on three activations — and the moment one
activation can be switched on by two things (its own subscription *and* a bundle), a single
status column written by whichever event arrived last is wrong: cancelling the suite would switch
off the standalone HRMS the same customer still pays for.

So an activation gets **sources** (ADR-0004 §5, addendum). One row per thing that entitles it;
the activation is on while any source is live. Three steps:

1. **`products.is_bundle`**, and a `suite` row carrying it. A bundle is sold and listed but never
   routed and never activated: the marketplace can show it, `activate()` refuses it, and the
   projection fans a subscription to it out to its members. `status` is `soon` because which
   products a deployment sells is deployment data (ADR-0013 §5) — and a bundle is only honest to
   sell once its members are.
2. **`activation_sources`**, tenant-scoped, under the same forced `tenant_isolation` policy as
   `activations` (a projection write only passes with the tenant GUC bound), with a tenant-leading
   index. Unique per `(organization_id, product_key, source_product_key)`.
3. **One source per existing activation**, `source_product_key = product_key`: the product's own
   source, which is what every activation was until now — a standalone subscription or a hand
   activation, the two having always shared the row. Status, trial end and `state_occurred_at`
   carry across, so the recompute lands every existing activation exactly where it already is.
   Run as the migration role, which bypasses row-level security, because it spans every tenant.

Revision ID: b3d5f7a9c1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-09-23 10:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d5f7a9c1e2"  # pragma: allowlist secret
down_revision: str | None = "e5f6a7b8c9d0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
APP_ROLE = "opsgrids_product"
BUNDLE_KEY = "suite"

#: Every existing activation becomes its own source. A deactivated row is a suspended source
#: whatever its status column says — `deactivated_at` has always been what switched it off.
#: Idempotent (`ON CONFLICT DO NOTHING`), and a module constant so a test can run exactly this
#: statement against a seeded activation rather than a copy of it.
BACKFILL_SOURCES = f"""
    INSERT INTO {SCHEMA}.activation_sources
        (id, organization_id, product_key, source_product_key, source_subscription_id,
         status, trial_ends_at, state_occurred_at)
    SELECT gen_random_uuid(), a.organization_id, a.product_key, a.product_key, NULL,
           CASE
               WHEN a.deactivated_at IS NOT NULL THEN 'suspended'
               WHEN a.status IN ('trial', 'active') THEN a.status
               ELSE 'suspended'
           END,
           a.trial_ends_at, a.state_occurred_at
      FROM {SCHEMA}.activations a
    ON CONFLICT (organization_id, product_key, source_product_key) DO NOTHING
"""


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("is_bundle", sa.Boolean(), server_default="false", nullable=False),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO product.products
                (id, key, name, category, status, tagline, connects_to, features,
                 is_platform, is_bundle, sort_order)
            VALUES
                (gen_random_uuid(), :key, 'OpsGrids Suite', 'Bundles', 'soon',
                 'Two, three or four products on one bill — each one you add lowers the price of all of them',
                 '[]'::jsonb, '[]'::jsonb, false, true, 5)
            ON CONFLICT (key) DO UPDATE SET is_bundle = true
            """
        ).bindparams(key=BUNDLE_KEY)
    )

    op.create_table(
        "activation_sources",
        sa.Column("product_key", sa.String(length=40), nullable=False),
        sa.Column("source_product_key", sa.String(length=40), nullable=False),
        sa.Column("source_subscription_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_occurred_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status in ('trial', 'active', 'suspended', 'removed')",
            name=op.f("ck_activation_sources_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activation_sources")),
        sa.UniqueConstraint(
            "organization_id",
            "product_key",
            "source_product_key",
            name="uq_activation_sources_org_product_source",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_activation_sources_org_source",
        "activation_sources",
        ["organization_id", "source_product_key"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_product_activation_sources_organization_id"),
        "activation_sources",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.execute(f"ALTER TABLE {SCHEMA}.activation_sources ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.activation_sources FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.activation_sources
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )

    op.execute(BACKFILL_SOURCES)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.activation_sources")
    op.drop_index(
        op.f("ix_product_activation_sources_organization_id"),
        table_name="activation_sources",
        schema=SCHEMA,
    )
    op.drop_index("ix_activation_sources_org_source", table_name="activation_sources", schema=SCHEMA)
    op.drop_table("activation_sources", schema=SCHEMA)
    # Only a suite nobody bought: an activation row for it (from before this revision) is left
    # alone, and the registry row with it, rather than deleting data a customer's history names.
    op.execute(
        sa.text(
            "DELETE FROM product.products p WHERE p.key = :key AND NOT EXISTS "
            "(SELECT 1 FROM product.activations a WHERE a.product_key = p.key)"
        ).bindparams(key=BUNDLE_KEY)
    )
    op.drop_column("products", "is_bundle", schema=SCHEMA)
