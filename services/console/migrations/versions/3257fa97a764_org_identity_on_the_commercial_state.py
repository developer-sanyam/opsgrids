"""org identity on the commercial state

Adds `name`, `slug` and `identity_synced_at` to `org_commercial_state` — every existing
organization and subscription predates console's own event queue (it went live after them), so
their projection row has never had an identity folded into it. These three nullable columns are
what `app/consumers.py`'s new `core.org.*` binding and the one-off `app/backfill.py` write into;
a row with none of the three yet is simply one neither has reached, same as any other nullable
projection field here (`currency`, `trial_ends_at`).

Revision ID: 3257fa97a764
Revises: d2e1f3a5b7c9
Create Date: 2026-09-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '3257fa97a764'  # pragma: allowlist secret
down_revision: str | None = 'd2e1f3a5b7c9'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "console"
TABLE = "org_commercial_state"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("name", sa.String(length=200), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("slug", sa.String(length=60), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("identity_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "identity_synced_at", schema=SCHEMA)
    op.drop_column(TABLE, "slug", schema=SCHEMA)
    op.drop_column(TABLE, "name", schema=SCHEMA)
