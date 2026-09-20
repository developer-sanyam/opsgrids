"""regularization request opened fields

Closes the other half of ADR-0006 §12's data model for this table: `RegularizationRequest`'s own
docstring used to say `approval_request_id` was "populated late from the .opened/.decided events"
before the `.opened` half was stripped out because nothing consumed that event. It is consumed now
(`app/consumers.py::on_approval_request_opened`), and three columns land here for it to write:

* `approval_opened_at` — when `core.approval.request.opened` was applied to this row. Null until
  that event lands, which is what distinguishes "routing not yet known" from "routed, and here is
  (or is not) an approver" once `approver_employee_id` is also read.
* `approver_employee_id` — the step's routed approver from that event's own `approver_employee_id`
  field, a plain UUID into `employee.employees.id` with no cross-schema foreign key, the same
  convention every other employee reference on this table already follows (ADR-0000 §10). Null
  both before `.opened` arrives and when the step opened unassigned (ADR-0006 §4) —
  `approval_opened_at` is what tells those two apart.
* `decision_comment` — the decider's own free text from `.decided`'s `comment` field, carried
  verbatim as plaintext `Text`, the same "already stored in the clear on this table" reasoning
  `reason` follows (data model §5): approval's own copy (`approval_requests.comment`) is plaintext
  too, so sealing this one would protect nothing.

All three are nullable, additive-only, and touch no constraint, index or RLS policy — the existing
`tenant_isolation` policy on this table already covers every column on it.

Revision ID: 63a7f0e8ff00
Revises: 41d1cad3e5dc
Create Date: 2026-09-16 14:02:11.398217
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "63a7f0e8ff00"
down_revision: str | None = "41d1cad3e5dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"
TABLE = "regularization_requests"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("approval_opened_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("approver_employee_id", sa.UUID(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("decision_comment", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "decision_comment", schema=SCHEMA)
    op.drop_column(TABLE, "approver_employee_id", schema=SCHEMA)
    op.drop_column(TABLE, "approval_opened_at", schema=SCHEMA)
