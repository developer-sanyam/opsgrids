"""contact phone index

One nullable column: a keyed blind index of the contact's phone number.

It exists because `platform_core.pii.seal` uses a fresh nonce per call — deliberately, so equal
ciphertext cannot reveal that two rows hold the same value — which leaves the sealed column
unsearchable. An inbound WhatsApp message arrives carrying a number, and "which contact is this"
has to be answerable or every conversation creates a duplicate customer.

No backfill, and none is possible for rows whose number was never supplied raw. Rows that *do*
have `phone_enc` are also left alone: backfilling would mean decrypting every contact's number
in a migration, which is a bulk PII read with no audit trail and no operator behind it. They
gain an index the next time their number is written, and `phone_index IS NULL` reads correctly
as "not findable by number yet" rather than "has no number".

Revision ID: b5c81e02a7d9
Revises: e28f05c17d4b
Create Date: 2026-09-19 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c81e02a7d9"  # pragma: allowlist secret
down_revision: str | None = "e28f05c17d4b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("phone_index", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    # Not unique. Two contacts in one organization may legitimately share a number — a shared
    # office line, a person listed twice — and a unique constraint would reject the second
    # write rather than let the lookup pick deterministically.
    op.create_index(
        "ix_contacts_org_phone_index",
        "contacts",
        ["organization_id", "phone_index"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_contacts_org_phone_index", "contacts", schema=SCHEMA)
    op.drop_column("contacts", "phone_index", schema=SCHEMA)
