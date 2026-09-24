"""message retry_at

One nullable column on `messages` and the partial index that finds the rows it is set on:
`retry_at`, "the instant something must look at this message again".

Until now a transient send failure put the message back to `pending` and nothing ever queued it
again: the queued event had been acked, and even a redelivery would have hit `processed_events`.
So `dispatch_max_attempts = 6` gave one real attempt, and one SMTP blip dropped a password reset
without a trace (`docs/superpowers/specs/2026-09-21-engage-email-platform-design.md` §4).

`retry_at` is the single clock that fixes it, meaning two things by state:

* `pending` after a failed attempt — the backoff is over; the recovery sweep queues it again;
* `sending` — the lease on the dispatcher's claim; a process that died between claiming and
  recording leaves the row here, and a lapsed lease is settled as a failed attempt.

Null everywhere else, and for every existing row: no backfill. A message already stuck `pending`
by the old defect is not swept back to life by this migration — mailing customers something that
was owed them weeks ago is a decision for whoever owns the message, made by setting `retry_at`
on the rows they choose (docs/superpowers/specs/2026-09-21-engage-email-platform-design.md §4).

The index is partial (only rows with a `retry_at`, a handful next to the table) and led by
`organization_id` like every index in this schema. The sweep reads all of it — it is asking which
tenants have work — through a column-level `SELECT` grant on the outbox role, applied by
`migrations/env.py` after every run. No new table, so nothing new to put under row-level
security: `messages` already carries its forced tenant policy.

Revision ID: d5a8f1c3e7b2
Revises: c9d4e7a2b1f3
Create Date: 2026-09-24 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a8f1c3e7b2"  # pragma: allowlist secret
down_revision: str | None = "c9d4e7a2b1f3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_messages_org_retry_at",
        "messages",
        ["organization_id", "retry_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("retry_at IS NOT NULL"),
    )


def downgrade() -> None:
    # Messages waiting on a retry are left `pending` with nothing to queue them: the honest
    # outcome of removing the only thing that did.
    op.drop_index("ix_messages_org_retry_at", table_name="messages", schema=SCHEMA)
    op.drop_column("messages", "retry_at", schema=SCHEMA)
