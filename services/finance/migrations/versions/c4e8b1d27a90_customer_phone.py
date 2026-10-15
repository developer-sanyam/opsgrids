"""customer phone

A finance customer gains a phone number, so the customer import (and the customer form) can carry
one. Sealed by the application with `platform_core.pii` (`phone_enc`) beside its mask
(`phone_masked`), the same pair customer-service keeps for a CRM contact. Expand-only: one nullable
column and one with a server default, so the previous build keeps inserting customers between
`migrate` and `up`.

Downgrade drops both columns, and with them every number entered after this revision; there is no
other copy.

Revision ID: c4e8b1d27a90
Revises: ba73c648c84a
Create Date: 2026-10-15 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b1d27a90"  # pragma: allowlist secret
down_revision: str | None = "ba73c648c84a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "customers",
        sa.Column("phone_masked", sa.String(length=40), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.add_column(
        "customers", sa.Column("phone_enc", sa.LargeBinary(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("customers", "phone_enc", schema=SCHEMA)
    op.drop_column("customers", "phone_masked", schema=SCHEMA)
