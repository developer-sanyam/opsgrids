"""contact sealed phone

One nullable column: the other half of the platform's sealed-envelope pair for a contact's
phone number. `contacts.phone` already held the masked display form; `phone_enc` holds the raw
number encrypted by `platform_core.pii.seal`.

Nullable and with no backfill, deliberately. There is nothing to backfill *from* — the masked
column has always contained only a mask, and a mask cannot be reversed into a number. Every
existing row therefore reads exactly as it did before this migration: masked phone present,
sealed phone absent, `phone_enc IS NULL` meaning "no real number has ever been supplied". A
default or a NOT NULL here would have required inventing data.

No row-level-security change is needed. The policy on `customer.contacts` is defined per table,
not per column, so a new column inherits tenant isolation the moment it exists. That is worth
stating because the opposite — a column that quietly escapes the policy — is exactly the kind of
mistake that does not announce itself.

Revision ID: e28f05c17d4b
Revises: d17e94b06c3a
Create Date: 2026-09-19 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e28f05c17d4b"  # pragma: allowlist secret
down_revision: str | None = "d17e94b06c3a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("phone_enc", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Dropping this column destroys every sealed number, and they cannot be recovered from the
    # masks left behind. That is the correct behaviour for a downgrade — it returns the schema
    # to a state that held no raw numbers — but it is a data loss, not a no-op, and the
    # round-trip check in CI exercises it on an empty database rather than a populated one.
    op.drop_column("contacts", "phone_enc", schema=SCHEMA)
