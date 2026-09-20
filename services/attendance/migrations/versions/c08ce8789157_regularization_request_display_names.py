"""regularization request display names

Closes the gap `63a7f0e8ff00`'s own docstring left open: `approver_employee_id` on `.opened` and
`decided_by_employee_id` on `.decided` are bare ids, and neither event used to carry a display
name for the "Waiting on {Name}" screen — that has now been fixed on approval's publishing side.
Two nullable text columns land here for `app/consumers.py` to write:

* `approver_name` — the step's routed approver's display name, from `.opened`'s own
  `approver_name`. Copied verbatim as plaintext `String(200)`, the same width `employee.
  employees.full_name` and approval's own `hierarchy_nodes.display_name` already use for the
  same value — outside employee's PII envelope there (approval's `HierarchyNode` docstring), so
  this copy needs no unmask permission either, the same "already in the clear" reasoning
  `reason`/`decision_comment` already follow on this table (data model §5). Null both before
  `.opened` arrives, when the step opened unassigned, and whenever the event simply does not
  carry the key (an event published before approval's change lands, or a replay) — never raised
  on either way.
* `decided_by_name` — the decider's display name, from `.decided`'s own `decided_by_name`. Same
  width and reasoning as `approver_name`. Null when the decider is not in approval's hierarchy
  copy at all (a legitimate org-level decision, not an error) and whenever the key is absent from
  the payload.

Both are additive-only and touch no constraint, index or RLS policy — the existing
`tenant_isolation` policy on this table already covers every column on it, same as `63a7f0e8ff00`.

Revision ID: c08ce8789157
Revises: 63a7f0e8ff00
Create Date: 2026-09-16 13:00:09.478256
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c08ce8789157"
down_revision: str | None = "63a7f0e8ff00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"
TABLE = "regularization_requests"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("approver_name", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("decided_by_name", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "decided_by_name", schema=SCHEMA)
    op.drop_column(TABLE, "approver_name", schema=SCHEMA)
