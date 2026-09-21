"""a flat monthly plan for Engage

`10e6b62d9ac7` seeded one flat plan for every sellable product that existed then. Engage was
registered afterwards (product `e5f6a7b8c9d0`) and got none — so the dashboard's marketplace card
showed "Price on request" with no "Start trial" button, nobody could activate it, no token ever
carried the `engage` entitlement, and the Engage console's every call was refused. This is the
same row shape for the one product that missed it. The amount is a placeholder, as there: the
owner sets the real price in the staff console, which publishes a new immutable version.

Revision ID: 7c2e9d41a8b3
Revises: 10e6b62d9ac7
Create Date: 2026-09-22 00:20:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e9d41a8b3"  # pragma: allowlist secret
down_revision: str | None = "10e6b62d9ac7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed so a rerun of this file always names the same rows, in this database or any other.
_NAMESPACE = uuid.UUID("e28c4370-3dd2-4c5f-8349-c985fc3d5b04")

#: (product_key, code, name, amount_minor, sort_order). Amounts are placeholders (see above);
#: sort_order 65 sits between Revenue (60) and PulseBoard (70), matching the product's own order.
PRODUCTS: tuple[tuple[str, str, str, int, int], ...] = (
    ("engage", "engage", "Engage", 149900, 65),
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
