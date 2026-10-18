"""mark the suite bundle available in the product registry

Suite is now available on this deployment, offering Suite Duo, Suite Trio, and Suite Complete.

Revision ID: d5e7f9a1b3c5
Revises: c4e6a8b0d2f1
Create Date: 2026-10-18 19:38:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e7f9a1b3c5"  # pragma: allowlist secret
down_revision: str | None = "c4e6a8b0d2f1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUNDLE_KEY = "suite"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE product.products SET status = 'available' WHERE key = :key"
        ).bindparams(key=BUNDLE_KEY)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE product.products SET status = 'soon' WHERE key = :key"
        ).bindparams(key=BUNDLE_KEY)
    )
