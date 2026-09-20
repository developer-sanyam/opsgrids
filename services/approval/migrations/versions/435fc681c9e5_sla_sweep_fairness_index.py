"""SLA sweep fairness index

Slice-4 review finding (MAJOR 3): the per-organization escalation fairness cap was advisory, not
real. `app/selectors.due_escalation_steps` used to take the `slice_size` (default 500) oldest
overdue steps **platform-wide** first, and only then cap each organization's share of whatever it
already had in hand — so one tenant with 500+ overdue steps filled the slice entirely on its own,
`row_number() OVER (PARTITION BY organization_id ...)` never got a chance to run for any other
tenant, and every other organization's escalations silently stopped ticking while the sweep kept
logging a healthy `considered=500`.

Discovery now caps *inside* the query: `SELECT DISTINCT organization_id` finds which tenants have
overdue work before any tenant's rows are fetched, then a `CROSS JOIN LATERAL` pulls each
organization's own oldest `per_org_cap` overdue steps independently. `ix_approval_steps_sla_due` —
`(due_at) INCLUDE (organization_id, request_id, id) WHERE ...` — cannot serve that `LATERAL`
without re-scanning the whole table once per organization discovered: it does not lead with
`organization_id`, so Postgres has no way to seek directly to one tenant's rows. This index —
`(organization_id, due_at) INCLUDE (request_id) WHERE outcome = 'open' AND approver_employee_id IS
NOT NULL AND due_at IS NOT NULL` (the identical partial predicate) — is what turns the `LATERAL`
into an index-only seek per organization instead.

**Measured**, both queries run verbatim (with `approval_steps` swapped for a same-shaped scratch
table carrying only the columns and partial predicate this migration and `_DUE_ESCALATION_STEPS_SQL`
touch, seeded with real `psql` against this repo's own dev Postgres — not the numbers from a slide):
one organization with 5,000 steps overdue by up to 5,000 minutes, 299 organizations with one step
each overdue by 1-50 minutes, and 5,000 padding rows the partial predicate must filter out —
the exact "one very overdue tenant, everyone else mildly overdue" shape `tests/test_sweep.py`'s
`TestFairnessCap` seeds, at a scale large enough for the planner's real choices to show.

Before (old two-stage `slice`-then-`row_number()` shape, `per_org_cap=20`, `limit=100`— both
`ix_approval_steps_sla_due` only, this migration's index not yet created):
```
Limit  (cost=143.34..143.59 rows=100 width=56) (actual time=4.320..4.408 rows=20 loops=1)
  ->  Sort  (cost=143.34..144.59 rows=500 width=56) (actual time=4.239..4.266 rows=20 loops=1)
        Sort Key: capped.due_at
        ->  Subquery Scan on capped  (actual time=3.377..3.536 rows=20 loops=1)
              ->  WindowAgg  (Run Condition: (row_number() OVER (?) <= 20))
                    (actual time=3.321..3.460 rows=20 loops=1)
                    ->  Sort  (Sort Key: slice.organization_id, slice.due_at)
                          (actual time=2.409..2.439 rows=500 loops=1)
                          ->  Subquery Scan on slice (actual time=0.626..1.012 rows=500 loops=1)
                                ->  Limit  (actual time=0.626..0.948 rows=500 loops=1)
                                      ->  Index Only Scan using ix_approval_steps_sla_due
                                            Index Cond: (due_at <= now())
                                            Heap Fetches: 500 (actual rows=500 loops=1)
Planning Time: 18.593 ms
Execution Time: 6.727 ms
```
A direct count against the same fixture confirms what the plan implies: `count(DISTINCT
organization_id)` over the rows this query actually returns is **1** — every one of the 20 rows
returned this tick belongs to the single busy organization; the other 299, each with genuinely
overdue work, get nothing, silently, this tick and every tick after it while the busy tenant keeps
manufacturing new overdue steps faster than a 20-per-tick cap ever lets this shape drain.

After (per-organization `LATERAL`, same fixture, same `per_org_cap`/`limit`, both indexes present):
```
Limit  (cost=28512.45..28512.70 rows=100 width=40) (actual time=42.884..44.502 rows=100 loops=1)
  ->  Sort  (Sort Key: s.due_at)  (actual time=42.785..44.392 rows=100 loops=1)
        ->  Nested Loop  (actual time=41.180..43.925 rows=319 loops=1)
              ->  Unique  (actual time=40.232..42.479 rows=300 loops=1)
                    ->  Gather Merge  (Workers Launched: 1)
                          ->  Unique  (actual time=3.075..3.333 rows=150 loops=2)
                                ->  Sort  (Sort Key: bench_steps.organization_id)
                                      ->  Parallel Index Only Scan using ix_approval_steps_sla_due
                                            Index Cond: (due_at <= now())
                                            Heap Fetches: 5299 (actual rows=2650 loops=2)
              ->  Limit  (actual time=0.004..0.004 rows=1 loops=300)
                    ->  Index Only Scan using ix_approval_steps_sla_due_by_org
                          Index Cond: ((organization_id = ...) AND (due_at <= now()))
                          Heap Fetches: 319 (actual rows=1 loops=300)
Planning Time: 25.505 ms
Execution Time: 46.870 ms
```
The same direct count against the new query returns `orgs_served = 300, rows_returned = 319` — the
LATERAL visits every organization discovered as having overdue work, not only the one with the
most. **No `Seq Scan` in either plan** — both are index-only throughout — and the fix costs
roughly 40 ms more on this synthetic 10,600-row fixture (dominated by the `DISTINCT
organization_id` scan and sort, not by the per-organization `LATERAL`, which is a sub-millisecond
index-only lookup per organization) to genuinely serve every tenant with overdue work instead of
only the largest one. `Heap Fetches` is non-zero on both sides only because this scratch table was
never `VACUUM`ed after the bulk seed (no visibility map bits set yet); it is not a property of the
index shape itself and is not expected to persist against a table `autovacuum` has touched.

Revision ID: 435fc681c9e5
Revises: 0007b9b716ef
Create Date: 2026-09-17 10:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '435fc681c9e5'
down_revision: str | None = '0007b9b716ef'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"


def upgrade() -> None:
    op.create_index(
        "ix_approval_steps_sla_due_by_org",
        "approval_steps",
        ["organization_id", "due_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_include=("request_id",),
        postgresql_where=(
            "outcome = 'open' and approver_employee_id is not null and due_at is not null"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_steps_sla_due_by_org",
        table_name="approval_steps",
        schema=SCHEMA,
        postgresql_include=("request_id",),
        postgresql_where=(
            "outcome = 'open' and approver_employee_id is not null and due_at is not null"
        ),
    )
