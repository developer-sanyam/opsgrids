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


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("self_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.drop_constraint(CK, "approval_requests", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CK,
        "approval_requests",
        "decided_by_employee_id is null or decided_by_employee_id <> requester_employee_id "
        "or self_approved",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CK, "approval_requests", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CK,
        "approval_requests",
        "decided_by_employee_id is null or decided_by_employee_id <> requester_employee_id",
        schema=SCHEMA,
    )
    op.drop_column("approval_requests", "self_approved", schema=SCHEMA)
