"""reactivate the suite's full rung now that Projects has launched

`c5f1e8a2b7d4` retired `c0de0001-0000-4000-8000-000000000006` (`suite/full`) until Projects launched.
Now that Projects is live and available in the product catalogue, `full` is active and sellable again,
entitling `hrms`, `crm`, `finance`, and `projects`.

Revision ID: c6a7b8d9e0f1
Revises: b88a833020dc
Create Date: 2026-10-18 19:38:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6a7b8d9e0f1"  # pragma: allowlist secret
down_revision: str | None = "b88a833020dc"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FULL_PLAN = "c0de0001-0000-4000-8000-000000000006"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE catalog.plans SET state = 'active' WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=FULL_PLAN)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE catalog.plans SET state = 'retired' WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=FULL_PLAN)
    )
