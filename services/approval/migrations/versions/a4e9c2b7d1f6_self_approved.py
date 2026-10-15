"""owner self-approval fallback: `self_approved` and the relaxed decider check

A small business may have no second approver, which left the owner's own request undecidable
(ADR-0006 §6 rule 3). The decision endpoint now lets an org owner approve their own unassigned
request on an explicit `self_approve` confirmation; the row records it so the DB invariant
(decider <> requester) still holds for every other row.

Revision ID: a4e9c2b7d1f6
Revises: 435fc681c9e5
Create Date: 2026-10-16 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4e9c2b7d1f6"
down_revision: str | None = "435fc681c9e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"
CK = "decider_not_requester"  # the naming convention adds the ck_<table>_ prefix
GUARD = "trg_approval_requests_decide_once"


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("self_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.drop_constraint(CK, "approval_requests", schema=SCHEMA, type_="check")
    # A no-op on first upgrade (the strict check allowed no such row). After a downgrade and
    # re-upgrade it restores the flag, which is derivable: decider = requester only by
    # self-approval. Decided rows are immutable, so the guard is paused for this one write.
    op.execute(f"ALTER TABLE {SCHEMA}.approval_requests DISABLE TRIGGER {GUARD}")
    op.execute(
        f"UPDATE {SCHEMA}.approval_requests SET self_approved = true "
        "WHERE decided_by_employee_id = requester_employee_id"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.approval_requests ENABLE TRIGGER {GUARD}")
    op.create_check_constraint(
        CK,
        "approval_requests",
        "decided_by_employee_id is null or decided_by_employee_id <> requester_employee_id "
        "or self_approved",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # The flag column goes but stays derivable (upgrade restores it). NOT VALID keeps any
    # self-approved row already decided (an approval is history, never deleted) while the
    # strict rule binds every new write; a plain re-add would fail on those rows.
    op.drop_constraint(CK, "approval_requests", schema=SCHEMA, type_="check")
    op.execute(
        f"ALTER TABLE {SCHEMA}.approval_requests ADD CONSTRAINT ck_approval_requests_{CK} "
        "CHECK (decided_by_employee_id is null or decided_by_employee_id <> requester_employee_id)"
        " NOT VALID"
    )
    op.drop_column("approval_requests", "self_approved", schema=SCHEMA)
