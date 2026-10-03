"""inbound message intent

`app/intent.py` reads what an inbound WhatsApp message means and records the verdict on the row:
the label, the action taken and P(marketing opt-out). Four nullable columns, no backfill -- a
message that arrived before the feature was on (or while it is off) was never classified, and
null says exactly that. None of the four holds customer text.

Revision ID: b7e3c9a1d452
Revises: f1c8a2d5b7e4
Create Date: 2026-10-03 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3c9a1d452"  # pragma: allowlist secret
down_revision: str | None = "f1c8a2d5b7e4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
TABLE = "inbound_messages"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("intent", sa.String(24), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("intent_action", sa.String(12), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("intent_p_opt_out", sa.Float, nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("intent_classified_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for column in ("intent_classified_at", "intent_p_opt_out", "intent_action", "intent"):
        op.drop_column(TABLE, column, schema=SCHEMA)
