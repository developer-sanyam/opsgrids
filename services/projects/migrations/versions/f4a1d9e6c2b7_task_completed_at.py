"""task completed_at

Adds `tasks.completed_at` (ADR-0020 §3, slice I6): the timestamp `insights.task_facts` needs for
"tasks completed" and "points completed" — nothing else on this row records when a card entered
`done`. Backfilled from this organization's own `audit_logs`, never guessed: the latest
`task.updated` audit row whose `after->>'stage'` is `done`, else the `task.created` row that
already carried `done`. A task with no such audit row (seeded or imported before auditing
existed) is left `NULL` — insights' coverage for `tasks_completed`/`task_points_completed` then
starts at go-live for that organization rather than inventing a date (ADR-0020 §3, "an unknown
date is never guessed").

Revision ID: f4a1d9e6c2b7
Revises: c7b52e0a9f43
Create Date: 2026-09-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a1d9e6c2b7"  # pragma: allowlist secret
down_revision: str | None = "c7b52e0a9f43"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "projects"


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tasks_org_completed_at",
        "tasks",
        ["organization_id", "completed_at"],
        schema=SCHEMA,
    )

    # The backfill: per task currently `done`, the latest qualifying audit row's own `at`.
    # `DISTINCT ON` picks the newest `task.updated` row whose recorded `after.stage` is `done`;
    # falling back to `task.created` covers a task created directly in `done` that was never
    # itself the subject of a later `task.updated` row.
    op.execute(
        f"""
        UPDATE {SCHEMA}.tasks AS t
        SET completed_at = latest.at
        FROM (
            SELECT DISTINCT ON (entity_id) entity_id, at
            FROM {SCHEMA}.audit_logs
            WHERE entity = 'task'
              AND action = 'task.updated'
              AND after ->> 'stage' = 'done'
            ORDER BY entity_id, at DESC
        ) AS latest
        WHERE t.id = latest.entity_id
          AND t.stage = 'done'
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.tasks AS t
        SET completed_at = created.at
        FROM (
            SELECT entity_id, at
            FROM {SCHEMA}.audit_logs
            WHERE entity = 'task'
              AND action = 'task.created'
              AND after ->> 'stage' = 'done'
        ) AS created
        WHERE t.id = created.entity_id
          AND t.stage = 'done'
          AND t.completed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_org_completed_at", table_name="tasks", schema=SCHEMA)
    op.drop_column("tasks", "completed_at", schema=SCHEMA)
