"""fulfilment auto-assignment: settings opt-in, item skill requirement, task failure reason

ADR-0024 §9 (deterministic assignment, no AI/ML); `docs/adr/0025-order-and-task-state.md` §2's
own note that auto-assignment was "deferred, no phase number assigned" past F3. This closes that
gap without touching F3's own tables or the `PICK_TASK_TRANSITIONS`/`assignment_offers` shape —
three additive columns only:

1. `fulfilment_settings.auto_assignment_enabled` (default `False`, then the default dropped, the
   same two-step shape `require_pickup_code` used in `ae34570ed578`) — an org must opt in;
   existing organizations' behaviour is unaffected.
2. `inventory_items.required_skill` (nullable, no default) — the skill (if any,
   `worker_skills.skill`'s own free-text vocabulary) a picker needs to be offered a task touching
   this item. Schema-only: no write path yet exposes it (see the model's own docstring).
3. `pick_tasks.auto_assign_failure_reason` (nullable, no default) — set when an org has
   auto-assignment on but no worker was eligible; cleared the moment any offer, auto or manual,
   is made for the task.

Revision ID: 393a08e3886a
Revises: b7d4f1c9a06e
Create Date: 2026-09-29 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "393a08e3886a"  # pragma: allowlist secret
down_revision: str | None = "b7d4f1c9a06e"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


def upgrade() -> None:
    op.add_column(
        "fulfilment_settings",
        sa.Column(
            "auto_assignment_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema=SCHEMA,
    )
    op.alter_column(
        "fulfilment_settings", "auto_assignment_enabled", server_default=None, schema=SCHEMA
    )

    op.add_column(
        "inventory_items",
        sa.Column("required_skill", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )

    op.add_column(
        "pick_tasks",
        sa.Column("auto_assign_failure_reason", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pick_tasks", "auto_assign_failure_reason", schema=SCHEMA)
    op.drop_column("inventory_items", "required_skill", schema=SCHEMA)
    op.drop_column("fulfilment_settings", "auto_assignment_enabled", schema=SCHEMA)
