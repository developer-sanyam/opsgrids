"""the Engage plan: one plan, one published version, one flat monthly price

Engage was registered in the product catalogue as `available` (product-service `e5f6a7b8c9d0`)
and never given a plan here. `10e6b62d9ac7` seeded a flat plan for every sellable product it
knew about, and Engage was not in its list. The product row alone buys nothing: per ADR-0013 §1
the subscription is what a customer buys and the activation follows it by event, and the
dashboard's marketplace only offers "Start a 14-day trial" when `GET /plans?product_key=engage`
resolves to a published flat price. With no plan there was no price, so no card could start a
trial, no subscription could be created, and no activation could ever follow. Every
`/api/v1/engage` route therefore answered the gateway's 402 — which is what the owner of a real
organisation hit in production, on engage.opsgrids.com's Overview: "Couldn't load your sending
identity — engage is not active for this organization". Nothing was broken in Engage itself;
it simply could not be bought.

This migration is the missing row, in exactly the shape `10e6b62d9ac7` gives every other
product: one plan, one published version, one flat monthly price in INR. It is a new migration
rather than an edit to that one because a published migration is history — every database that
has run `10e6b62d9ac7` would never see a row added to it.

**The amount is a placeholder**, for the reason `e1f2a3b4c5d6` and `10e6b62d9ac7` give: what a
product costs is deployment data, set by the owner through the staff console (Plans), which
publishes a new immutable version and leaves this one behind (ADR-0013 §2). Seeding a number at
all is what lets a customer start a trial before that decision is made.

Ids are deterministic — `uuid5` of this migration's own fixed namespace, the product key and a
row kind — so the seed is idempotent (`ON CONFLICT DO NOTHING`) and its downgrade is exact. The
namespace is new rather than `10e6b62d9ac7`'s, so these ids can never be mistaken for, or collide
with, rows another migration owns.

Revision ID: 53f1a7a8b99d
Revises: 10e6b62d9ac7
Create Date: 2026-09-21 23:55:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "53f1a7a8b99d"  # pragma: allowlist secret
down_revision: str | None = "10e6b62d9ac7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed so a rerun of this file always names the same rows, in this database or any other.
_NAMESPACE = uuid.UUID("753dac70-7635-412e-be9d-4feb1723f019")

#: (product_key, code, name, amount_minor, sort_order). ₹1,499.00 per organisation per month as
#: integer minor units (ADR-0000 §8) — a placeholder, in line with the other flat plans.
#: sort_order continues past `10e6b62d9ac7`'s last flight (IT, 120).
PRODUCTS: tuple[tuple[str, str, str, int, int], ...] = (
    ("engage", "engage", "Engage", 149900, 130),
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
#: `10e6b62d9ac7` and `e1f2a3b4c5d6` do — the triggers are disabled around it and re-enabled
#: after, whatever happens in between.
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
