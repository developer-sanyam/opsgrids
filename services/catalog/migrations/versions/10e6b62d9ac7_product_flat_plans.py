"""a flat monthly plan for every non-platform product

ADR-0013 §2: a product is sold as a flat price per organisation per month, in the catalogue,
versioned — the same shape `e1f2a3b4c5d6` seeded for Inventory (`operations`), the first product
sold this way. This migration finishes the row: one plan, one published version, one flat
monthly price, for every remaining sellable product — `storefront`, `hrms`, `crm`, `pulseboard`,
`projects`, `support`, `marketing`, `finance` and `itsm`. `core` is excluded: it is `is_platform`
in the product catalogue, included with everything, and never a subscription line of its own.

**The amounts are placeholders.** What a product costs is deployment data, not repository data:
the owner sets the real number through the staff console (Plans) on the box that sells it, which
publishes a new immutable version and leaves this one behind (ADR-0013 §2). Seeding a number at
all is what lets a customer start a trial before that decision is made — the dashboard's
marketplace card only shows a price and a "Start trial" button when the product's catalogue entry
resolves to a published flat price, and a product with no seeded plan cannot appear as buyable no
matter what its `status` says.

`hrms`, `crm`, `finance` and `projects` already carry per-seat plans from `c8811ff511d3` and
`f78c86fcec78` (codes `starter`/`growth`). The codes below — `people`, `revenue`, `finance`,
`projects` — are new codes on those same product keys, chosen so `(product_key, code)` cannot
collide with what is already seeded.

Ids are deterministic — `uuid5` of a fixed namespace, the product key and a row kind, computed
once at import time into a module-level table — so the seed is idempotent (`ON CONFLICT DO
NOTHING`) and its downgrade is exact, exactly as `e1f2a3b4c5d6` does with its literal ids.

Revision ID: 10e6b62d9ac7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-21 10:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "10e6b62d9ac7"  # pragma: allowlist secret
down_revision: str | None = "e1f2a3b4c5d6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed so a rerun of this file always names the same rows, in this database or any other.
_NAMESPACE = uuid.UUID("e28c4370-3dd2-4c5f-8349-c985fc3d5b04")

#: (product_key, code, name, amount_minor, sort_order). Amounts are placeholders (see above);
#: sort_order continues past Inventory's 30, one flight of ten per product, in this order.
PRODUCTS: tuple[tuple[str, str, str, int, int], ...] = (
    ("storefront", "storefront", "Storefront", 149900, 40),
    ("hrms", "people", "People", 199900, 50),
    ("crm", "revenue", "Revenue", 199900, 60),
    ("pulseboard", "pulseboard", "PulseBoard", 149900, 70),
    ("projects", "projects", "Projects", 149900, 80),
    ("support", "support", "Support", 149900, 90),
    ("marketing", "marketing", "Marketing", 149900, 100),
    ("finance", "finance", "Finance", 249900, 110),
    ("itsm", "it", "IT", 199900, 120),
)


def _row_ids(product_key: str) -> tuple[str, str, str]:
    """The (plan, version, price) triple for one product's flat plan, `uuid5`-derived so it is
    the same id every time this migration is read, not just the first time it is run."""
    return (
        str(uuid.uuid5(_NAMESPACE, f"{product_key}:plan")),
        str(uuid.uuid5(_NAMESPACE, f"{product_key}:version")),
        str(uuid.uuid5(_NAMESPACE, f"{product_key}:price")),
    )


#: Computed once, at import time, so both `upgrade` and `downgrade` — and any test that loads
#: this module by path — read the same ids without recomputing them.
IDS: dict[str, tuple[str, str, str]] = {
    product_key: _row_ids(product_key) for product_key, *_rest in PRODUCTS
}


def upgrade() -> None:
    for product_key, code, name, amount_minor, sort_order in PRODUCTS:
        plan_id, version_id, price_id = IDS[product_key]
        op.execute(
            sa.text(
                """
                INSERT INTO catalog.plans (id, product_key, code, name, state, sort_order)
                VALUES (CAST(:plan AS uuid), :product_key, :code, :name, 'active', :sort_order)
                ON CONFLICT (product_key, code) DO NOTHING
                """
            ).bindparams(
                plan=plan_id,
                product_key=product_key,
                code=code,
                name=name,
                sort_order=sort_order,
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO catalog.plan_versions
                    (id, plan_id, version_no, state, published_at, billing_interval, currency)
                VALUES
                    (CAST(:v AS uuid), CAST(:plan AS uuid), 1, 'published', now(), 'month', 'INR')
                ON CONFLICT (plan_id, version_no) DO NOTHING
                """
            ).bindparams(v=version_id, plan=plan_id)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO catalog.prices (id, plan_version_id, kind, amount_minor, currency, unit)
                VALUES (CAST(:p AS uuid), CAST(:v AS uuid), 'flat', :amount, 'INR', NULL)
                ON CONFLICT (id) DO NOTHING
                """
            ).bindparams(p=price_id, v=version_id, amount=amount_minor)
        )


#: The version is published, and published versions and their children are frozen by trigger
#: (ADR-0000 §9). A downgrade is the one legitimate deletion of a seeded row, so — exactly as
#: `e1f2a3b4c5d6` and the commerce-packaging seed do — the triggers are disabled around it and
#: re-enabled after, whatever happens in between.
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
        for product_key, *_rest in PRODUCTS:
            plan_id, version_id, price_id = IDS[product_key]
            conn.execute(
                sa.text("DELETE FROM catalog.prices WHERE id = CAST(:p AS uuid)").bindparams(
                    p=price_id
                )
            )
            conn.execute(
                sa.text(
                    "DELETE FROM catalog.plan_versions WHERE id = CAST(:v AS uuid)"
                ).bindparams(v=version_id)
            )
            conn.execute(
                sa.text("DELETE FROM catalog.plans WHERE id = CAST(:plan AS uuid)").bindparams(
                    plan=plan_id
                )
            )
    finally:
        for table, trigger in _FROZEN_TRIGGERS:
            conn.execute(sa.text(f"ALTER TABLE catalog.{table} ENABLE TRIGGER {trigger}"))
