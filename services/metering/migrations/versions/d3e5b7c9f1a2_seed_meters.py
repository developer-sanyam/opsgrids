"""seed the meter catalogue

Like the product catalogue and catalog's plans, the reference layer ships a starting set in a
migration rather than a script, so a fresh database is immediately correct and every environment
agrees on what a meter means. `meters` is global platform vocabulary (no `organization_id`,
un-policied), so these rows belong to no tenant and are read by all of them.

These are illustrative platform meters — enough to price against once a plan does, and to give
the reference table real content. Deterministic ids and `ON CONFLICT DO NOTHING` keep the
migration idempotent; `downgrade` removes only the rows this migration wrote, never a meter an
operator added afterwards.

Revision ID: d3e5b7c9f1a2
Revises: c2f4a6b8d0e1
Create Date: 2026-08-24 21:12:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd3e5b7c9f1a2'  # pragma: allowlist secret
down_revision: str | None = 'c2f4a6b8d0e1'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed ids so the seed is idempotent and reversible.
API_CALL = "aaaaaaaa-0000-4000-8000-000000000001"
STORAGE_GB = "aaaaaaaa-0000-4000-8000-000000000002"
SEATS_ACTIVE = "aaaaaaaa-0000-4000-8000-000000000003"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO metering.meters (id, meter_key, product_key, unit, description)
            VALUES
                (CAST(:api AS uuid), 'core.api_call', 'core', 'api_call',
                 'One authenticated API call served'),
                (CAST(:gb AS uuid), 'core.storage_gb', 'core', 'gb',
                 'One gigabyte-month of stored data'),
                (CAST(:seat AS uuid), 'hrms.active_seat', 'hrms', 'seat',
                 'One active HRMS seat in the period')
            ON CONFLICT (meter_key) DO NOTHING
            """
        ).bindparams(api=API_CALL, gb=STORAGE_GB, seat=SEATS_ACTIVE)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM metering.meters "
            "WHERE id IN (CAST(:api AS uuid), CAST(:gb AS uuid), CAST(:seat AS uuid))"
        ).bindparams(api=API_CALL, gb=STORAGE_GB, seat=SEATS_ACTIVE)
    )
