"""Meta deauthorize and data-deletion callbacks (slice W1 follow-up)

Three changes, all additive:

* `provider_accounts.meta_user_id` -- the Facebook-scoped user id (`FB.login`'s
  `authResponse.userID`) of whoever completed Embedded Signup for this connection, captured going
  forward only. A partial index (most rows have none) is what
  `selectors.accounts_for_meta_user_id` resolves a deauthorize or data-deletion callback against --
  the directory reader's grant is table-level (`a4b7c9e1f206`), so it already covers this column
  with no further `GRANT`.
* `w1_setup_runs.meta_user_id_hint` -- the same id, as reported by the browser at signup time,
  mirrored onto `provider_accounts.meta_user_id` once the connection is written
  (`w1_setup._step_number`). A hint, exactly like `waba_hint`/`phone_number_hint` beside it.
* `w1_meta_deletion_requests` -- new table, and deliberately **not** tenant-scoped: a Meta
  data-deletion callback can name a Facebook user id shared by more than one organization's
  connection, or none at all, so no single `organization_id` is the right scope for it, and this
  migration never enables row-level security on it -- the same judgement call `processed_events`
  already makes (`b2c3d4e5f6a7`), for a related reason. No `GRANT` here either: `env.py`'s
  post-migration `apply_schema_grants` re-applies the app role's schema-wide DML after every
  migration, which already covers a brand new table.

No `lock_timeout` needed: two nullable column adds (no default, so no table rewrite) and one new,
empty table. See `services/auth/migrations/versions/9c3e7a1d5b28_mfa_factors_confirm_failed_
attempts.py` for the shape a migration takes when that is not true.

Revision ID: e2b9f47a63d1
Revises: d7b4e9a2c613
Create Date: 2026-09-27 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b9f47a63d1"  # pragma: allowlist secret
down_revision: str | None = "d7b4e9a2c613"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "provider_accounts",
        sa.Column("meta_user_id", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_provider_accounts_meta_user_id",
        "provider_accounts",
        ["meta_user_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("meta_user_id IS NOT NULL"),
    )

    op.add_column(
        "w1_setup_runs",
        sa.Column("meta_user_id_hint", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )

    op.create_table(
        "w1_meta_deletion_requests",
        sa.Column("confirmation_code", sa.String(length=43), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("confirmation_code", name=op.f("pk_w1_meta_deletion_requests")),
        sa.UniqueConstraint("request_key", name="uq_w1_meta_deletion_requests_request_key"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("w1_meta_deletion_requests", schema=SCHEMA)
    op.drop_column("w1_setup_runs", "meta_user_id_hint", schema=SCHEMA)
    op.drop_index(
        "ix_provider_accounts_meta_user_id", table_name="provider_accounts", schema=SCHEMA
    )
    op.drop_column("provider_accounts", "meta_user_id", schema=SCHEMA)
