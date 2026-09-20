"""delegation list and TEAM-arm indexes

Four indexes, all missing at the query that has needed them since the migration each would
naturally have shipped with — a slice-3 review caught the first three, a follow-up architect
pass caught the fourth:

`ix_delegations_org_starts` — `(organization_id, starts_at)` — pattern 11b (data model §2,
table row 11b): `GET /delegations` at ORG scope, `ORDER BY starts_at DESC`. Neither `deleted_at`
nor `starts_at` led any existing index (the unique on `(organization_id, id)` cannot serve this
sort), so both the count and the page ran a full `Seq Scan` of the whole multi-tenant table.
Measured on a 281-organization, 94,704-row seeded fleet: **1,710 buffers / ~10-12 ms** before,
**28 buffers / 0.46 ms** after. Plain, not partial on `deleted_at IS NULL` — the data model
built and measured the partial form and it earns nothing at this table's soft-delete ratio
(§2.3): a delegation is revoked, not deleted, in the ordinary case.

`ix_delegations_org_delegator_starts` / `ix_delegations_org_delegate_starts` —
`(organization_id, delegator_employee_id, starts_at)` / `(organization_id,
delegate_employee_id, starts_at)` — pattern 11a: `GET /delegations` below ORG (OWN, and TEAM
and LOCATION, which collapse to the same "either party" read — `app/catalogue.py`'s ceiling on
`delegation.write` never grants those two anything wider). `selectors.delegations_stmt` writes
"delegator or delegate" as an `OR` across two columns, which only a pair of indexes — one
leading each side — lets the planner serve with a `BitmapOr`; a single three-column index
cannot. Measured on the same fixture, a caller with 4 of an organization's 24,000 delegations:
**634 buffers / 4.6 ms** before (the `OR` discarded on the heap, proportional to the whole
organization's rows, not the caller's), **7 buffers** after.

`ix_hierarchy_nodes_org_manager` — `(organization_id, reporting_manager_id, employee_id)` —
pattern 19 (data model §2, table row 19): `GET /requests` at TEAM
(`requests_stmt`'s `ctx.apply(team_col=HierarchyNode.reporting_manager_id, ...)`), feeding
`ix_approval_requests_org_requester_opened`. This one shipped in neither `0a7f41108309` (the
routing tables) nor `7108bbc9ecc6` (the delegate-candidate indexes) — it was named in both as a
citation ("the same index-only reason `ix_hierarchy_nodes_org_manager` carries it") for a
different index's own column ordering, which is how it came to read as an existing thing:
the name propagated through comments and the data model's own index table while the index
itself was never declared or created. Shipped here, in the same migration as the delegation
indexes above, because it is the same table family and the same slice-boundary mistake — TEAM
reads shipped with `GET /requests` in slice 2, and the index the data model assigned to that
query should have shipped with it.

Measured write cost of the three `delegations` indexes together, one insert on the seeded
fixture: **~4 extra buffers, ~0.15 ms** — in the region the data model's own pattern-11-vs-
pattern-9 comparison implies (§2.3: delegation writes are rare, a few per manager per year, so
three more indexes on this table is a write-side cost worth paying for a read-side one that
runs on every render of the delegation screen).

Plain `op.create_index()` throughout: none of these four needs a functional expression or an
operator class, unlike the two `7108bbc9ecc6` created with raw `op.execute()`.

Revision ID: 640aba68b036
Revises: 7108bbc9ecc6
Create Date: 2026-09-16 15:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '640aba68b036'
down_revision: str | None = '7108bbc9ecc6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"


def upgrade() -> None:
    op.create_index(
        "ix_delegations_org_starts",
        "delegations",
        ["organization_id", "starts_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_delegations_org_delegator_starts",
        "delegations",
        ["organization_id", "delegator_employee_id", "starts_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_delegations_org_delegate_starts",
        "delegations",
        ["organization_id", "delegate_employee_id", "starts_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hierarchy_nodes_org_manager",
        "hierarchy_nodes",
        ["organization_id", "reporting_manager_id", "employee_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hierarchy_nodes_org_manager",
        table_name="hierarchy_nodes",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_delegations_org_delegate_starts",
        table_name="delegations",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_delegations_org_delegator_starts",
        table_name="delegations",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_delegations_org_starts",
        table_name="delegations",
        schema=SCHEMA,
    )
