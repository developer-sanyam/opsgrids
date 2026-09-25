"""country and org created at on the commercial state

Adds `country` and `org_created_at` to `org_commercial_state` — the accounts list's Region and
Created filters (ui/backend-data-2). Both are mirrored from account-service the same way
`name`/`slug` already are: `app/consumers.py`'s `core.org.*` fold and the one-off
`app/backfill.py`. `org_created_at` is named to avoid colliding with `TenantBase`'s own
bookkeeping `created_at` (this row's own creation time, not the organization's).

Revision ID: c3bc1da7c0bc
Revises: 3257fa97a764
Create Date: 2026-09-25 04:08:37.732277
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c3bc1da7c0bc'  # pragma: allowlist secret
down_revision: str | None = '3257fa97a764'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "console"
TABLE = "org_commercial_state"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("country", sa.String(length=2), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("org_created_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "org_created_at", schema=SCHEMA)
    op.drop_column(TABLE, "country", schema=SCHEMA)
