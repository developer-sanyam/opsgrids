"""SLA sweep discovery indexes

Two indexes on `approval_steps`, both cross-tenant — deliberately not leading with
`organization_id`, because both serve the SLA sweep's BYPASSRLS discovery session
(`app/cron.py`, slice 4), which exists to find out *which* organizations have overdue work, so
there is no tenant to bind while asking (data model §2.2, the same shape subscription's
`ix_dunning_cycles_due` uses for the dunning sweep).

`ix_approval_steps_sla_due` — `(due_at) INCLUDE (organization_id, request_id, id) WHERE outcome =
'open' AND approver_employee_id IS NOT NULL AND due_at IS NOT NULL` — is the sweep's own discovery
read (pattern 3): open, assigned, overdue steps, index-only. Measured on the data model's seeded
fixture (375,003 steps, 300 organizations): 7 buffers, 0.11 ms for a 500-row slice, `Heap Fetches:
0`.

`ix_approval_steps_stuck_open` — `(opened_at) INCLUDE (organization_id, request_id, id,
approver_employee_id) WHERE outcome = 'open' AND due_at IS NULL` — serves the two liveness probes
(patterns 14/15): the primary alarm (oldest unassigned open step — nothing ever escalates it, the
ladder's own terminus) and the secondary (oldest assigned-but-disabled-policy open step). The two
indexes' `WHERE` clauses are exact complements over `outcome = 'open'`
(`ck_approval_steps_unassigned_has_no_due` is what makes the split exhaustive — an open step with
no due date has either no approver or a disabled policy, and an open step with a due date always
has an approver, so no open step can fall outside both). Measured: 823 buffers / 2.879 ms (bitmap
scan plus a sort of the whole unassigned-and-disabled backlog) down to 3 buffers / 0.015 ms
(index-only, no sort).

Both are §15's textbook case for "an index ships with the query, not with the feature it was
designed for": the queries themselves do not exist before this slice, so there is no earlier point
either index could have shipped at.

Revision ID: 0007b9b716ef
Revises: 640aba68b036
Create Date: 2026-09-16 16:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0007b9b716ef'
down_revision: str | None = '640aba68b036'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"


def upgrade() -> None:
    op.create_index(
        "ix_approval_steps_sla_due",
        "approval_steps",
        ["due_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_include=("organization_id", "request_id", "id"),
        postgresql_where=(
            "outcome = 'open' and approver_employee_id is not null and due_at is not null"
        ),
    )
    op.create_index(
        "ix_approval_steps_stuck_open",
        "approval_steps",
        ["opened_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_include=("organization_id", "request_id", "id", "approver_employee_id"),
        postgresql_where="outcome = 'open' and due_at is null",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_steps_stuck_open",
        table_name="approval_steps",
        schema=SCHEMA,
        postgresql_include=("organization_id", "request_id", "id", "approver_employee_id"),
        postgresql_where="outcome = 'open' and due_at is null",
    )
    op.drop_index(
        "ix_approval_steps_sla_due",
        table_name="approval_steps",
        schema=SCHEMA,
        postgresql_include=("organization_id", "request_id", "id"),
        postgresql_where=(
            "outcome = 'open' and approver_employee_id is not null and due_at is not null"
        ),
    )
