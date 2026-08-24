"""seed catalog plans

Like the product catalogue, the definitional layer ships a starting set in a migration rather
than a script, so a fresh database is immediately correct and every environment agrees on what
exists (the same reasoning as `product`'s `seed the product catalogue`). These are public
plans — `restricted_to_org_id` NULL — so every customer of the product can see them; a bespoke
version is authored per deal through the operator API, never seeded.

Deterministic ids and `ON CONFLICT DO NOTHING` keep the migration idempotent, and `downgrade`
removes only the rows this migration wrote — never a plan an operator added afterwards.

Revision ID: c8811ff511d3
Revises: 4fcd576a041c
Create Date: 2026-08-24 18:29:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c8811ff511d3'  # pragma: allowlist secret
down_revision: str | None = '4fcd576a041c'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed ids so the seed is idempotent and reversible.
STARTER_PLAN = "11111111-1111-4111-8111-111111111111"
GROWTH_PLAN = "22222222-2222-4222-8222-222222222222"
STARTER_V1 = "33333333-3333-4333-8333-333333333333"
GROWTH_V1 = "44444444-4444-4444-8444-444444444444"
STARTER_PRICE = "55555555-5555-4555-8555-555555555555"
GROWTH_PRICE = "66666666-6666-4666-8666-666666666666"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.plans (id, product_key, code, name, state, sort_order)
            VALUES
                (CAST(:starter AS uuid), 'hrms', 'starter', 'People Starter', 'active', 10),
                (CAST(:growth  AS uuid), 'hrms', 'growth',  'People Growth',  'active', 20)
            ON CONFLICT (product_key, code) DO NOTHING
            """
        ).bindparams(starter=STARTER_PLAN, growth=GROWTH_PLAN)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.plan_versions
                (id, plan_id, version_no, state, published_at, billing_interval, currency)
            VALUES
                (CAST(:sv AS uuid), CAST(:starter AS uuid), 1, 'published', now(), 'month', 'INR'),
                (CAST(:gv AS uuid), CAST(:growth  AS uuid), 1, 'published', now(), 'month', 'INR')
            ON CONFLICT (plan_id, version_no) DO NOTHING
            """
        ).bindparams(sv=STARTER_V1, gv=GROWTH_V1, starter=STARTER_PLAN, growth=GROWTH_PLAN)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.prices (id, plan_version_id, kind, amount_minor, currency, unit)
            VALUES
                (CAST(:sp AS uuid), CAST(:sv AS uuid), 'per_seat', 19900, 'INR', 'seat'),
                (CAST(:gp AS uuid), CAST(:gv AS uuid), 'per_seat', 34900, 'INR', 'seat')
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(sp=STARTER_PRICE, gp=GROWTH_PRICE, sv=STARTER_V1, gv=GROWTH_V1)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.features (id, plan_version_id, feature_key, value)
            VALUES
                (gen_random_uuid(), CAST(:sv AS uuid), 'max_seats', '25'::jsonb),
                (gen_random_uuid(), CAST(:sv AS uuid), 'payroll',   'false'::jsonb),
                (gen_random_uuid(), CAST(:gv AS uuid), 'max_seats', '250'::jsonb),
                (gen_random_uuid(), CAST(:gv AS uuid), 'payroll',   'true'::jsonb)
            ON CONFLICT (plan_version_id, feature_key) DO NOTHING
            """
        ).bindparams(sv=STARTER_V1, gv=GROWTH_V1)
    )


def downgrade() -> None:
    # Children first, then the plans. FK cascade would take them too, but being explicit keeps
    # the downgrade honest about exactly what it removes.
    op.execute(
        sa.text(
            "DELETE FROM catalog.features "
            "WHERE plan_version_id IN (CAST(:sv AS uuid), CAST(:gv AS uuid))"
        ).bindparams(sv=STARTER_V1, gv=GROWTH_V1)
    )
    op.execute(
        sa.text(
            "DELETE FROM catalog.prices WHERE id IN (CAST(:sp AS uuid), CAST(:gp AS uuid))"
        ).bindparams(sp=STARTER_PRICE, gp=GROWTH_PRICE)
    )
    op.execute(
        sa.text(
            "DELETE FROM catalog.plan_versions "
            "WHERE id IN (CAST(:sv AS uuid), CAST(:gv AS uuid))"
        ).bindparams(sv=STARTER_V1, gv=GROWTH_V1)
    )
    op.execute(
        sa.text(
            "DELETE FROM catalog.plans "
            "WHERE id IN (CAST(:starter AS uuid), CAST(:growth AS uuid))"
        ).bindparams(starter=STARTER_PLAN, growth=GROWTH_PLAN)
    )
