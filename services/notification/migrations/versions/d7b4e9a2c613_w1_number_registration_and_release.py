"""WhatsApp number registration state, and a releasable routing key (slice W1)

Two changes to what Embedded Signup keeps:

* `w1_numbers` learns the three Graph fields it did not read (`code_verification_status`,
  `platform_type`, `throughput_level`) and the bookkeeping for registering a number with Meta
  (`POST /{phone_number_id}/register`): the PIN this platform chose, **sealed** like the access
  token, and how many register calls were made in the current 72-hour window, because Meta allows
  ten and a blind retry would burn them.
* `provider_accounts.phone_number_id` — the globally unique webhook routing key — becomes
  nullable, and `released_phone_number_id` keeps history. A disconnect used to leave the number on
  the row, so no other organization could ever claim it (`number_claimed`); it now nulls the key
  (a unique constraint permits any number of nulls) and remembers the number here. A webhook for
  the released number finds no account and is dropped as an unknown number.

No new table, so no new policy: both tables already carry `organization_id` with FORCE row-level
security, and the app role's grants are re-applied after every migration.

Revision ID: d7b4e9a2c613
Revises: c5e19a7d3f48
Create Date: 2026-09-27 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7b4e9a2c613"  # pragma: allowlist secret
down_revision: str | None = "c5e19a7d3f48"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "w1_numbers",
        sa.Column("code_verification_status", sa.String(length=24), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "w1_numbers", sa.Column("platform_type", sa.String(length=24), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "w1_numbers",
        sa.Column("throughput_level", sa.String(length=24), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "w1_numbers",
        sa.Column("registration_pin_sealed", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "w1_numbers",
        sa.Column("registration_attempts", sa.Integer(), server_default="0", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "w1_numbers",
        sa.Column("registration_attempted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # The model supplies the default in Python; the server default only backfilled existing rows.
    op.alter_column("w1_numbers", "registration_attempts", server_default=None, schema=SCHEMA)

    op.add_column(
        "provider_accounts",
        sa.Column("released_phone_number_id", sa.String(length=80), nullable=True),
        schema=SCHEMA,
    )
    op.alter_column("provider_accounts", "phone_number_id", nullable=True, schema=SCHEMA)
    # A connection that was already disconnected keeps holding its number today, which is the very
    # bug being fixed: release it so the number can move. (Its history is kept alongside.)
    op.execute(
        f"""
        UPDATE {SCHEMA}.provider_accounts
           SET released_phone_number_id = phone_number_id,
               phone_number_id = NULL
         WHERE channel = 'whatsapp' AND status = 'disconnected' AND phone_number_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Restore NOT NULL: a released row gets its remembered number back, and a row with no number
    # at all (never happened before this revision) gets a unique placeholder so the constraint
    # can be re-imposed. Two organizations cannot both hold a number, so a number that has since
    # moved to another organization is not restored to the first.
    op.execute(
        f"""
        UPDATE {SCHEMA}.provider_accounts AS a
           SET phone_number_id = a.released_phone_number_id
         WHERE a.phone_number_id IS NULL
           AND a.released_phone_number_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1 FROM {SCHEMA}.provider_accounts AS b
                  WHERE b.phone_number_id = a.released_phone_number_id
               )
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.provider_accounts
           SET phone_number_id = 'released-' || replace(id::text, '-', '')
         WHERE phone_number_id IS NULL
        """
    )
    op.alter_column("provider_accounts", "phone_number_id", nullable=False, schema=SCHEMA)
    op.drop_column("provider_accounts", "released_phone_number_id", schema=SCHEMA)
    for column in (
        "registration_attempted_at",
        "registration_attempts",
        "registration_pin_sealed",
        "throughput_level",
        "platform_type",
        "code_verification_status",
    ):
        op.drop_column("w1_numbers", column, schema=SCHEMA)
