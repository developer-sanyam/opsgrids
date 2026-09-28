"""Meta deauthorize and data-deletion callbacks (slice W1 follow-up)

Four changes, all additive:

* `provider_accounts.meta_user_id` -- the Facebook-scoped user id (`FB.login`'s
  `authResponse.userID`) of whoever completed Embedded Signup for this connection, captured going
  forward only. A partial index (most rows have none) is what
  `selectors.accounts_for_meta_user_id` resolves a deauthorize or data-deletion callback against --
  the directory reader's grant is table-level (`a4b7c9e1f206`), so it already covers this column
  with no further `GRANT`.
* `w1_setup_runs.meta_user_id_hint` -- the same id, as reported by the browser at signup time,
  mirrored onto `provider_accounts.meta_user_id` once the connection is written
  (`w1_setup._step_number`). A hint, exactly like `waba_hint`/`phone_number_hint` beside it.
* `w1_connection_health.identity_revoked_at` -- an advisory-only timestamp: when Meta's
  deauthorize or data-deletion callback last reported the login behind a connection as revoked.
  Deliberately separate from `token_state`, which only Meta's own Graph API answering for a token
  may ever flip to `invalid` again -- see `w1_services.apply_meta_deauthorize`/
  `apply_meta_data_deletion` and the security-review note in `docs/whatsapp/webhooks.md`.
* `w1_meta_deletion_requests` -- new table, and deliberately **not** tenant-scoped: a Meta
  data-deletion callback can name a Facebook user id shared by more than one organization's
  connection, or none at all, so no single `organization_id` is the right scope for it, and this
  migration never enables row-level security on it -- the same judgement call `processed_events`
  already makes (`b2c3d4e5f6a7`), for a related reason. **Append-only**, the same way as every
  other ledger in this schema (`consent_events`, `d5a7c3e91f48`): `UPDATE` and `DELETE` are
  revoked from the application role, since a confirmation row is never edited once minted, only
  ever read by its own code or superseded by a fresh row for a distinct request.

`SET LOCAL lock_timeout` on the upgrade: two nullable column adds and one nullable column add on
an existing, actively-read table (`w1_connection_health`, which the health-card poll and every
webhook callback touch) still take Postgres's ACCESS EXCLUSIVE lock, however briefly, and a wait
behind a long-held transaction on `provider_accounts` would queue every webhook request behind it.
Mirrors `services/auth/migrations/versions/9c3e7a1d5b28_mfa_factors_confirm_failed_attempts.py`.

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
APP_ROLE = "opsgrids_notification"

#: See `9c3e7a1d5b28`'s own docstring for why this is a `SET LOCAL` rather than a bare hope that
#: nothing is holding a conflicting lock when this runs.
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

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

    op.add_column(
        "w1_connection_health",
        sa.Column("identity_revoked_at", sa.DateTime(timezone=True), nullable=True),
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
    # Append-only, as a table privilege (ADR-0000 §9) -- matches every other ledger in this
    # schema (`consent_events`, `d5a7c3e91f48`). Guarded on the role existing, because a database
    # built without the per-service roles has nothing to revoke from.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                REVOKE UPDATE, DELETE ON {SCHEMA}.w1_meta_deletion_requests FROM "{APP_ROLE}";
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_table("w1_meta_deletion_requests", schema=SCHEMA)
    op.drop_column("w1_connection_health", "identity_revoked_at", schema=SCHEMA)
    op.drop_column("w1_setup_runs", "meta_user_id_hint", schema=SCHEMA)
    op.drop_index(
        "ix_provider_accounts_meta_user_id", table_name="provider_accounts", schema=SCHEMA
    )
    op.drop_column("provider_accounts", "meta_user_id", schema=SCHEMA)
