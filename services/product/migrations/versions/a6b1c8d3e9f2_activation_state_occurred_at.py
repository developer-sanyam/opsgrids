"""record when the projected lifecycle fact occurred, for the out-of-order guard

The entitlement projection was last-write-wins: `project_subscription` set the activation's
status from whatever `commerce.subscription.*` event arrived, in arrival order. Idempotency
dedupes an exact redelivery, but a *distinct older* event delivered late — multi-replica
relays claiming disjoint batches, consumer replicas sharing the queue round-robin, a DLQ
replay — would overwrite a newer state: a customer suspended after a reactivation because
the suspension arrived last, or entitled after a suspension for the same reason.

`state_occurred_at` stores the envelope `occurred_at` of the fact the row currently
reflects; the projection now drops anything older. NULL for rows written by the manual
`activate` path, which is not event-driven and carries no ordering to guard.

Revision ID: a6b1c8d3e9f2
Revises: 91001ddbbdc0
Create Date: 2026-08-25 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b1c8d3e9f2"  # pragma: allowlist secret
down_revision: str | None = "91001ddbbdc0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"


def upgrade() -> None:
    op.add_column(
        "activations",
        sa.Column("state_occurred_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("activations", "state_occurred_at", schema=SCHEMA)
