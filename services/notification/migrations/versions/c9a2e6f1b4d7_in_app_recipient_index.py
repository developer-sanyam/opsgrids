"""in-app recipient index

Additive, index-only. `channel` has never been constrained to a fixed set at the database
level — it is `domain.CHANNELS` that refuses an unknown one at write time — so `in_app`
(`app/inbox.py`, `GET /notifications/me`) needs no new column and no new table: it is a
`messages` row like any other, with `to_address` holding the recipient's membership id
instead of an email or a phone number.

What it does need is this index. The feed's own query — one recipient's own rows on the
`in_app` channel, newest first, keyset-paged on `(created_at, id)` — would otherwise fall
back to `ix_messages_org_channel`, which stops at `channel` and leaves Postgres to filter
`to_address` and sort `created_at` over every message any rule has ever staged on this
channel for the whole organization.

Revision ID: c9a2e6f1b4d7
Revises: f4a8c2d6e913
Create Date: 2026-09-25 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9a2e6f1b4d7"  # pragma: allowlist secret
down_revision: str | None = "f4a8c2d6e913"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.create_index(
        "ix_messages_org_channel_recipient",
        "messages",
        ["organization_id", "channel", "to_address", "created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_org_channel_recipient", table_name="messages", schema=SCHEMA)
