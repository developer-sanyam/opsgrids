"""hierarchy_nodes exited index

`ix_hierarchy_nodes_org_exited` — the index deferred to slice 4 in `0a7f41108309`'s own
docstring, brought forward here because it serves a query slice 2 already ships: `_INBOX_COUNT_SQL`
/ `_INBOX_PAGE_SQL`'s (`app/selectors.py`) route-(c) "abandoned approver" arm runs

    EXISTS (
        SELECT 1 FROM approval.hierarchy_nodes h
         WHERE h.organization_id = s.organization_id
           AND h.employee_id = s.approver_employee_id
           AND h.status = 'exited'
    )

for every `decide:ORG` caller's inbox page. `hierarchy_nodes`'s primary key already covers
`(organization_id, employee_id)` for the equality lookup, but the EXISTS is reached from the
*steps* side as a semi-join candidate set, not as 3,200 independent PK probes — on a 20,000-node
organization with 3,200 open steps, measured plans without this index choose a `Seq Scan` of the
whole `hierarchy_nodes` table to build the semi-join's hash table (11.6ms, 516 buffers) rather than
3,200 index probes, because the planner reads the *whole table* once as cheaper than the per-row
alternative it is comparing against. A partial index on exactly the predicate this query filters by
— `status = 'exited'` — shrinks what a `Bitmap Index Scan` has to read to the (typically small)
exited subset instead of the whole table: measured at 3.4ms, 524 buffers on the same fixture, and
the gap widens as headcount grows because the index stays proportional to the exited population
while a sequential scan stays proportional to everyone.

Columns `(organization_id, employee_id)`, not `(organization_id, employee_id, status)`: the
predicate `status = 'exited'` is the partial index's own `WHERE`, so it is already implied by using
the index at all and does not need to be a column too — data model §1.0's own reasoning for why
`hierarchy_nodes`'s composite primary key carries no redundant single-column index applies here in
the same shape.

Revision ID: cde7b9237543
Revises: c8d3e6f4a1b2
Create Date: 2026-09-16 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'cde7b9237543'
down_revision: str | None = 'c8d3e6f4a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"


def upgrade() -> None:
    op.create_index(
        "ix_hierarchy_nodes_org_exited",
        "hierarchy_nodes",
        ["organization_id", "employee_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where="status = 'exited'",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hierarchy_nodes_org_exited",
        table_name="hierarchy_nodes",
        schema=SCHEMA,
    )
