"""enquiries.whatsapp_opt_in_at

ENGAGE-02. The public enquiry form gets an optional, unticked-by-default WhatsApp marketing opt-in.
When the buyer ticks it, the enquiry is stamped with the moment, and notification-service records
the opt-in on its consent ledger after fetching the number through
`GET /internal/v1/enquiries/{id}/whatsapp-opt-in` — which answers only for a stamped enquiry, so
that endpoint cannot be used to read any other buyer's number.

Additive and nullable (a metadata-only change on Postgres 11+), unused by the previous release,
which keeps working against the new schema during a rolling deploy.

Revision ID: b8e4d2a6f913
Revises: ea0b8b84b84d
Create Date: 2026-10-12 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4d2a6f913"  # pragma: allowlist secret
down_revision: str | None = "ea0b8b84b84d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"
TABLE = "enquiries"
COLUMN = "whatsapp_opt_in_at"

#: `ALTER TABLE` takes an ACCESS EXCLUSIVE lock, however briefly; waiting behind a long transaction
#: would queue every buyer's enquiry behind it. Fail fast and re-run instead.
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        TABLE, sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
