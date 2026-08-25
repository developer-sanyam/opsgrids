"""seed the commerce packaging examples

Packaging that needed no machinery, shipped as the thing it actually is: rows
(docs/commerce-packaging.md). Three of the four packaging shapes on the market backlog are
configuration over the catalogue that already exists, and the honest way to say so is to
configure them and let the tests read the result back.

What this seeds, and why each row is the shape it is:

* **Three more standalone plans** — `crm`, `finance` and `projects` growth tiers beside the two
  `hrms` tiers already seeded. A bundle discount is only meaningful against a published
  standalone price, and comparing against a price that exists is the difference between a
  claimed saving and a computed one.

* **A graduated bundle ladder** under the `suite` product key: `duo` (2 products), `trio` (3)
  and `full` (4). Each rung is an ordinary immutable plan version with an ordinary `per_seat`
  price — **not** a discount flag layered over the standalone plans — so an invoice priced
  against a rung stays reconstructable from the `plan_version_id` its line already records, long
  after the ladder has been re-priced. What makes a version a bundle is declarative: the
  `bundled_products` feature names its members and `bundle_tier` names the rung. The ladder is
  graduated by arithmetic, not by assertion — 29100, 26900 and 23900 minor units per product per
  seat — and a test recomputes that from these rows rather than trusting this comment.

* **A second version of `crm/growth`, carrying seat classes.** Deliberately a *new version* at
  the same rate rather than an edit to v1: v1 is published, and adding a seat class to it would
  change what a customer already pinned to it signed under. The paid `member` class points at
  the version's own `per_seat` price; the free `viewer` class points at no price at all, because
  free means the absence of a price row and not a zero-amount one.

* **A partner wholesale price**: a second version of `suite/trio` with `restricted_to_org_id` set
  to a reseller organization. This is the *same* audience-of-one mechanism ADR-0004 §2 built for
  a negotiated enterprise price, used for channel distribution instead — no new column, no new
  table, no new code. A test proves another organization cannot see it, which is the same
  negative-authz shape that already defends bespoke pricing.

Deterministic ids and `ON CONFLICT DO NOTHING` keep the migration idempotent; `downgrade` removes
only the rows this migration wrote.

Revision ID: f78c86fcec78
Revises: 8fef75a4a797
Create Date: 2026-08-25 11:20:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f78c86fcec78"  # pragma: allowlist secret
down_revision: str | None = "8fef75a4a797"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _oid(kind: int, n: int) -> str:
    """A fixed, valid v4-shaped UUID, so the seed is idempotent and its downgrade is exact."""
    return f"c0de{kind:04x}-0000-4000-8000-{n:012d}"


# The reseller this partner price is negotiated for. A demo organization id: a tenant is a bare
# UUID here, exactly as it is everywhere else in commerce — no cross-schema row is needed.
PARTNER_ORG = "a17e5e11-0000-4000-8000-000000000001"

#: (id, product_key, code, name, sort_order)
PLANS = [
    (_oid(1, 1), "crm", "growth", "Revenue Growth", 20),
    (_oid(1, 2), "finance", "growth", "Finance Growth", 20),
    (_oid(1, 3), "projects", "growth", "Delivery Growth", 20),
    (_oid(1, 4), "suite", "duo", "Suite Duo", 30),
    (_oid(1, 5), "suite", "trio", "Suite Trio", 31),
    (_oid(1, 6), "suite", "full", "Suite Complete", 32),
]

#: (id, plan_id, version_no, restricted_to_org_id, per-seat minor units)
#:
#: Standalone: 34900 (hrms, already seeded) + 29900 + 24900 + 19900.
#: Bundled per seat: duo 58200 of 64800 apart; trio 80700 of 89700; full 95600 of 109600.
#: Per product per seat: 29100 → 26900 → 23900, which is what "graduated" means here.
VERSIONS = [
    (_oid(2, 1), _oid(1, 1), 1, None, 29900),
    (_oid(2, 2), _oid(1, 2), 1, None, 24900),
    (_oid(2, 3), _oid(1, 3), 1, None, 19900),
    (_oid(2, 4), _oid(1, 4), 1, None, 58200),
    (_oid(2, 5), _oid(1, 5), 1, None, 80700),
    (_oid(2, 6), _oid(1, 6), 1, None, 95600),
    # The partner's wholesale price for the trio: the same audience-of-one column a bespoke
    # enterprise price uses, pointed at a reseller. 15% channel margin off 80700, exactly.
    (_oid(2, 7), _oid(1, 5), 2, PARTNER_ORG, 68595),
    # v2 of crm/growth — the same rate as v1, but it sells two classes of seat. A new version
    # rather than an edit: v1 is published and somebody may be pinned to it.
    (_oid(2, 8), _oid(1, 1), 2, None, 29900),
]

#: (id, plan_version_id, feature_key, json value)
FEATURES = [
    (_oid(3, 1), _oid(2, 4), "bundled_products", ["hrms", "crm"]),
    (_oid(3, 2), _oid(2, 4), "bundle_tier", "duo"),
    (_oid(3, 3), _oid(2, 5), "bundled_products", ["hrms", "crm", "finance"]),
    (_oid(3, 4), _oid(2, 5), "bundle_tier", "trio"),
    (_oid(3, 5), _oid(2, 6), "bundled_products", ["hrms", "crm", "finance", "projects"]),
    (_oid(3, 6), _oid(2, 6), "bundle_tier", "full"),
    (_oid(3, 7), _oid(2, 7), "bundled_products", ["hrms", "crm", "finance"]),
    (_oid(3, 8), _oid(2, 7), "bundle_tier", "trio"),
    # Documentation on the row. The *enforcement* is `restricted_to_org_id`; this exists so an
    # operator screen can say "partner price" rather than infer it from a bare UUID.
    (_oid(3, 9), _oid(2, 7), "partner_of", PARTNER_ORG),
]

#: (id, plan_version_id, code, name, price_id | None, can_write, sort_order, description)
#:
#: A price id of None is a **free** class. The paid class points at its version's own per-seat
#: price, so money lives in `prices` and in nowhere else.
SEAT_CLASSES = [
    (
        _oid(5, 1),
        _oid(2, 8),
        "member",
        "Member seat",
        _oid(4, 8),
        True,
        10,
        "A full seat: reads and writes, billed at the plan's per-seat rate.",
    ),
    (
        _oid(5, 2),
        _oid(2, 8),
        "viewer",
        "View-only seat",
        None,
        False,
        20,
        "Free. Read scopes only — no writes and no PII unmask, enforced where the grant is "
        "computed rather than where it is displayed.",
    ),
]
# Every bundle rung sells the same two classes, so a customer who bundles does not lose the
# free viewer seat they had on the standalone plan.
for _n, (_version_id, _price_id) in enumerate(
    [
        (_oid(2, 4), _oid(4, 4)),
        (_oid(2, 5), _oid(4, 5)),
        (_oid(2, 6), _oid(4, 6)),
        (_oid(2, 7), _oid(4, 7)),
    ],
    start=1,
):
    SEAT_CLASSES.append(
        (
            _oid(5, 10 + _n),
            _version_id,
            "member",
            "Member seat",
            _price_id,
            True,
            10,
            "A full seat across every product in the bundle.",
        )
    )
    SEAT_CLASSES.append(
        (
            _oid(5, 20 + _n),
            _version_id,
            "viewer",
            "View-only seat",
            None,
            False,
            20,
            "Free. Read scopes only, across every product in the bundle.",
        )
    )


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.plans (id, product_key, code, name, state, sort_order)
            VALUES (CAST(:id AS uuid), :product_key, :code, :name, 'active', :sort_order)
            ON CONFLICT (product_key, code) DO NOTHING
            """
        ),
        [
            {"id": i, "product_key": p, "code": c, "name": n, "sort_order": s}
            for i, p, c, n, s in PLANS
        ],
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.plan_versions
                (id, plan_id, version_no, state, published_at, billing_interval, currency,
                 restricted_to_org_id)
            VALUES (CAST(:id AS uuid), CAST(:plan_id AS uuid), :version_no, 'published', now(),
                    'month', 'INR', CAST(:restricted AS uuid))
            ON CONFLICT (plan_id, version_no) DO NOTHING
            """
        ),
        [
            {"id": i, "plan_id": p, "version_no": v, "restricted": r}
            for i, p, v, r, _amount in VERSIONS
        ],
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.prices (id, plan_version_id, kind, amount_minor, currency, unit)
            VALUES (CAST(:id AS uuid), CAST(:version_id AS uuid), 'per_seat', :amount, 'INR',
                    'seat')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        [
            {"id": _oid(4, n), "version_id": version_id, "amount": amount}
            for n, (version_id, amount) in enumerate(
                [(v[0], v[4]) for v in VERSIONS], start=1
            )
        ],
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.features (id, plan_version_id, feature_key, value)
            VALUES (CAST(:id AS uuid), CAST(:version_id AS uuid), :key, CAST(:value AS jsonb))
            ON CONFLICT (plan_version_id, feature_key) DO NOTHING
            """
        ),
        [
            {"id": i, "version_id": v, "key": k, "value": json.dumps(val)}
            for i, v, k, val in FEATURES
        ],
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.seat_classes
                (id, plan_version_id, code, name, price_id, included_count, can_write,
                 sort_order, description)
            VALUES (CAST(:id AS uuid), CAST(:version_id AS uuid), :code, :name,
                    CAST(:price_id AS uuid), NULL, :can_write, :sort_order, :description)
            ON CONFLICT (plan_version_id, code) DO NOTHING
            """
        ),
        [
            {
                "id": i,
                "version_id": v,
                "code": c,
                "name": n,
                "price_id": p,
                "can_write": w,
                "sort_order": s,
                "description": d,
            }
            for i, v, c, n, p, w, s, d in SEAT_CLASSES
        ],
    )


#: The immutability triggers this downgrade has to stand down, and the tables they sit on.
#: Every one of them exists to stop a *published price sheet being edited*. Removing a seed —
#: undoing the act that created those rows in the first place — is the one operation that is not
#: that, and it is only reachable by a migration running as the schema owner. Disabling them for
#: the length of this statement is narrower and more honest than the alternative of leaving the
#: seed un-reversible, which is a rollback nobody has (`make migration-roundtrip` runs this).
_FROZEN_TRIGGERS = (
    ("seat_classes", "trg_seat_classes_frozen_immutable"),
    ("prices", "trg_prices_frozen_immutable"),
    ("features", "trg_features_frozen_immutable"),
    ("plan_versions", "trg_plan_versions_no_delete"),
)


def _delete_by_id(conn: sa.Connection, table: str, ids: list[str]) -> None:
    stmt = sa.text(f"DELETE FROM catalog.{table} WHERE id IN :ids").bindparams(
        sa.bindparam("ids", expanding=True)
    )
    conn.execute(stmt, {"ids": ids})


def downgrade() -> None:
    conn = op.get_bind()
    price_ids = [_oid(4, n) for n in range(1, len(VERSIONS) + 1)]

    for table, trigger in _FROZEN_TRIGGERS:
        conn.execute(sa.text(f"ALTER TABLE catalog.{table} DISABLE TRIGGER {trigger}"))
    try:
        # Children first, then the versions, then the plans. FK cascade would take them anyway;
        # being explicit keeps the downgrade honest about exactly what it removes.
        _delete_by_id(conn, "seat_classes", [s[0] for s in SEAT_CLASSES])
        _delete_by_id(conn, "features", [f[0] for f in FEATURES])
        _delete_by_id(conn, "prices", price_ids)
        _delete_by_id(conn, "plan_versions", [v[0] for v in VERSIONS])
        _delete_by_id(conn, "plans", [p[0] for p in PLANS])
    finally:
        for table, trigger in _FROZEN_TRIGGERS:
            conn.execute(sa.text(f"ALTER TABLE catalog.{table} ENABLE TRIGGER {trigger}"))
