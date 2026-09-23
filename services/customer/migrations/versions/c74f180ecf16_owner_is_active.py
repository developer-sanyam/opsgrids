"""owner is_active

Every existing rep is active until told otherwise, so the column backfills `true` for rows
already on disk — the server default is then dropped, matching every other boolean flag on
this platform: the Python-level default on `Owner.is_active` is what new rows get, the
column has no default of its own once history is settled.

Revision ID: c74f180ecf16
Revises: f4a1c9d3e7b2
Create Date: 2026-09-23 21:17:03.378824
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = 'c74f180ecf16'  # pragma: allowlist secret
down_revision: str | None = 'f4a1c9d3e7b2'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'owners',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        schema='customer',
    )
    op.alter_column('owners', 'is_active', server_default=None, schema='customer')


def downgrade() -> None:
    op.drop_column('owners', 'is_active', schema='customer')
