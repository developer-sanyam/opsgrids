"""provider accounts and receipts

Two things, both needed before an inbound webhook can be acted on.

`provider_accounts` is the routing table. Meta posts every organization's webhooks to one URL,
and `metadata.phone_number_id` is the only thing in the payload that says whose they are — so
that column is unique **globally**, not per organization. Two tenants claiming one number would
make an inbound message unattributable, and a per-organization constraint would allow exactly
that.

The columns on `messages` are the other half: a receipt reports `delivered` or `read`, and
"when was it read" is a question analytics asks that a single state column cannot answer.

Revision ID: a4b7c9e1f206
Revises: f39a16d28e5c
Create Date: 2026-09-19 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b7c9e1f206"  # pragma: allowlist secret
down_revision: str | None = "f39a16d28e5c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"
DIRECTORY_ROLE = "opsgrids_notification_directory"


def upgrade() -> None:
    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("waba_id", sa.String(length=80), nullable=False),
        sa.Column("phone_number_id", sa.String(length=80), nullable=False),
        sa.Column("display_number", sa.String(length=40), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_accounts")),
        # Global, deliberately: this is the webhook routing key.
        sa.UniqueConstraint("phone_number_id", name="uq_provider_accounts_phone_number_id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_provider_accounts_organization_id"),
        "provider_accounts",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_provider_accounts_org_channel",
        "provider_accounts",
        ["organization_id", "channel"],
        unique=False,
        schema=SCHEMA,
    )

    # Tenant isolation for the ordinary API reads. The webhook's own lookup deliberately
    # bypasses this — it is resolving *which* tenant, which a policy cannot express — and runs
    # as the SELECT-only directory reader granted below.
    op.execute(f"ALTER TABLE {SCHEMA}.provider_accounts ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.provider_accounts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.provider_accounts
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )

    # The directory reader gets SELECT on this one table and nothing else, so a bug in the
    # webhook path cannot read another table cross-tenant, let alone write one. Guarded because
    # the role is created by `tools/dev-bootstrap/init-db.sql`, which a bare database may not
    # have run — the grant is then skipped rather than failing the migration.
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DIRECTORY_ROLE}') THEN
            GRANT USAGE ON SCHEMA {SCHEMA} TO {DIRECTORY_ROLE};
            GRANT SELECT ON {SCHEMA}.provider_accounts TO {DIRECTORY_ROLE};
          END IF;
        END
        $$;
        """
    )

    for column in (
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_error_code", sa.String(length=40), nullable=True),
        sa.Column("provider_error_detail", sa.Text(), nullable=True),
    ):
        op.add_column("messages", column, schema=SCHEMA)

    op.create_index(
        "ix_messages_org_provider_message_id",
        "messages",
        ["organization_id", "provider_message_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_org_provider_message_id", "messages", schema=SCHEMA)
    for name in ("provider_error_detail", "provider_error_code", "read_at", "delivered_at"):
        op.drop_column("messages", name, schema=SCHEMA)

    # Messages left in `delivered` or `read` cannot be expressed once those columns are gone,
    # and the state column has no constraint to catch it — so they are walked back to the last
    # state the older schema knew. Data loss, and the honest consequence of removing receipts.
    op.execute(
        f"UPDATE {SCHEMA}.messages SET state = 'sent' WHERE state IN ('delivered', 'read')"
    )

    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.provider_accounts")
    op.drop_table("provider_accounts", schema=SCHEMA)
