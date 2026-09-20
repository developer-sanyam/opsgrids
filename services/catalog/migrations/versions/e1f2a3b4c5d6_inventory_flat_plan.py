"""the Inventory plan: one plan, one published version, one flat monthly price

ADR-0013 §2: a product is sold as a flat price per organisation per month, and Inventory (the
`operations` product) is the first product sold this way. Seeded like the HRMS plans — public,
deterministic ids, `ON CONFLICT DO NOTHING` — so every environment agrees the plan exists.

**The amount is a placeholder.** What a product costs is deployment data: the owner sets it
through the staff console (Plans) on the box that sells it, which publishes a new immutable
version and leaves this one behind. Seeding a number at all is what lets a trial start before
that decision is made; seeding a real number here would make a repository the place prices are
decided, which it is not.

Revision ID: e1f2a3b4c5d6
Revises: f78c86fcec78
Create Date: 2026-09-20 16:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"  # pragma: allowlist secret
down_revision: str | None = "f78c86fcec78"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed ids so the seed is idempotent and reversible.
PLAN = "77777777-7777-4777-8777-777777777777"
VERSION = "88888888-8888-4888-8888-888888888888"
PRICE = "99999999-9999-4999-8999-999999999999"

#: ₹2,499.00 per organisation per month, as integer minor units (ADR-0000 §8). A placeholder.
AMOUNT_MINOR = 249900


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.plans (id, product_key, code, name, state, sort_order)
            VALUES (CAST(:plan AS uuid), 'operations', 'inventory', 'Inventory', 'active', 30)
            ON CONFLICT (product_key, code) DO NOTHING
            """
        ).bindparams(plan=PLAN)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.plan_versions
                (id, plan_id, version_no, state, published_at, billing_interval, currency)
            VALUES (CAST(:v AS uuid), CAST(:plan AS uuid), 1, 'published', now(), 'month', 'INR')
            ON CONFLICT (plan_id, version_no) DO NOTHING
            """
        ).bindparams(v=VERSION, plan=PLAN)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog.prices (id, plan_version_id, kind, amount_minor, currency, unit)
            VALUES (CAST(:p AS uuid), CAST(:v AS uuid), 'flat', :amount, 'INR', NULL)
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(p=PRICE, v=VERSION, amount=AMOUNT_MINOR)
    )


#: The version is published, and published versions and their children are frozen by trigger
#: (ADR-0000 §9). A downgrade is the one legitimate deletion of a seeded row, so — exactly as the
#: commerce-packaging seed does — the triggers are disabled around it and re-enabled after,
#: whatever happens in between.
_FROZEN_TRIGGERS = (
    ("prices", "trg_prices_frozen_immutable"),
    ("features", "trg_features_frozen_immutable"),
    ("seat_classes", "trg_seat_classes_frozen_immutable"),
    ("plan_versions", "trg_plan_versions_no_delete"),
)


def downgrade() -> None:
    conn = op.get_bind()
    for table, trigger in _FROZEN_TRIGGERS:
        conn.execute(sa.text(f"ALTER TABLE catalog.{table} DISABLE TRIGGER {trigger}"))
    try:
        conn.execute(
            sa.text("DELETE FROM catalog.prices WHERE id = CAST(:p AS uuid)").bindparams(p=PRICE)
        )
        conn.execute(
            sa.text("DELETE FROM catalog.plan_versions WHERE id = CAST(:v AS uuid)").bindparams(
                v=VERSION
            )
        )
        conn.execute(
            sa.text("DELETE FROM catalog.plans WHERE id = CAST(:plan AS uuid)").bindparams(
                plan=PLAN
            )
        )
    finally:
        for table, trigger in _FROZEN_TRIGGERS:
            conn.execute(sa.text(f"ALTER TABLE catalog.{table} ENABLE TRIGGER {trigger}"))
