"""erase campaign recipient copies

`campaign_recipients.email_enc` is a sealed copy of a contact's address, taken at campaign
expansion so a report on a finished send still says who it went to after the contact is edited
or removed. That copying is exactly what a contact deletion or erasure has to reach back into:
without it, an address a customer asked to be forgotten stays recoverable forever in every
campaign it was ever addressed by.

`erased_at` is the one column this needs. `services.erase_contact_campaign_copies` sets it, in
the same transaction as the contact's deletion, and re-seals `email_enc` over a placeholder
rather than nulling it — the column stays `NOT NULL` and always valid ciphertext, so nothing
that has ever called `pii.open_sealed` on this table needs to learn a new null check; the send
driver checks `erased_at` instead, before it would otherwise decrypt. `email_masked` is
overwritten the same way, and `email_index` is cleared so the row can no longer be found by
blind-index lookup either. The row itself is never removed: a campaign's delivery history and
stats must outlive the contact that received it.

Revision ID: a1c7e93f5b02
Revises: 4f999b248e7e
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7e93f5b02"  # pragma: allowlist secret
down_revision: str | None = "4f999b248e7e"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
TABLE = "campaign_recipients"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.create_index(
        "ix_campaign_recipients_org_contact",
        TABLE,
        ["organization_id", "contact_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_recipients_org_contact",
        table_name=TABLE,
        schema=SCHEMA,
    )
    op.drop_column(TABLE, "erased_at", schema=SCHEMA)
