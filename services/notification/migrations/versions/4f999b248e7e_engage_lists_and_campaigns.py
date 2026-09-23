"""engage lists and campaigns

Contact lists and their memberships, campaigns, and one row per campaign recipient. Recipient
addresses are copied sealed (`email_enc`, `email_masked`, `email_index`) exactly as contacts hold
them. Foreign keys stay inside this schema. Policies are hand-written in the same shape as every
other tenant table here; grants come from `apply_schema_grants` in `migrations/env.py`.

Revision ID: 4f999b248e7e
Revises: e7d2f4a91b3c
Create Date: 2026-09-22 01:10:55.929211
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "4f999b248e7e"  # pragma: allowlist secret
down_revision: str | None = "e7d2f4a91b3c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"

TENANT_TABLES = ("contact_lists", "contact_list_members", "campaigns", "campaign_recipients")


def upgrade() -> None:
    op.create_table(
        "contact_lists",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_lists")),
        sa.UniqueConstraint("organization_id", "name", name="uq_contact_list_org_name"),
        schema="notification",
    )
    op.create_index(
        op.f("ix_notification_contact_lists_organization_id"),
        "contact_lists",
        ["organization_id"],
        unique=False,
        schema="notification",
    )
    op.create_table(
        "campaigns",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sender_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("list_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(length=300), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("suppressed_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(audience = 'list') = (list_id IS NOT NULL)", name=op.f("ck_campaigns_audience_list")
        ),
        sa.CheckConstraint(
            "audience IN ('list', 'all_sendable')", name=op.f("ck_campaigns_audience")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'sending', 'paused', 'sent', 'cancelled')",
            name=op.f("ck_campaigns_status"),
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["notification.contact_lists.id"],
            name=op.f("fk_campaigns_list_id_contact_lists"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
        schema="notification",
    )
    op.create_index(
        "ix_campaigns_org_status",
        "campaigns",
        ["organization_id", "status"],
        unique=False,
        schema="notification",
    )
    op.create_index(
        op.f("ix_notification_campaigns_organization_id"),
        "campaigns",
        ["organization_id"],
        unique=False,
        schema="notification",
    )
    op.create_table(
        "contact_list_members",
        sa.Column("list_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["notification.contacts.id"],
            name=op.f("fk_contact_list_members_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["notification.contact_lists.id"],
            name=op.f("fk_contact_list_members_list_id_contact_lists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_list_members")),
        sa.UniqueConstraint("list_id", "contact_id", name="uq_contact_list_member"),
        schema="notification",
    )
    op.create_index(
        "ix_contact_list_members_org_list",
        "contact_list_members",
        ["organization_id", "list_id", "contact_id"],
        unique=False,
        schema="notification",
    )
    op.create_index(
        op.f("ix_notification_contact_list_members_organization_id"),
        "contact_list_members",
        ["organization_id"],
        unique=False,
        schema="notification",
    )
    op.create_table(
        "campaign_recipients",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("email_enc", sa.LargeBinary(), nullable=False),
        sa.Column("email_masked", sa.String(length=254), nullable=False),
        sa.Column("email_index", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'failed', 'suppressed')",
            name=op.f("ck_campaign_recipients_status"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["notification.campaigns.id"],
            name=op.f("fk_campaign_recipients_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_recipients")),
        sa.UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipient"),
        schema="notification",
    )
    op.create_index(
        "ix_campaign_recipients_org_campaign",
        "campaign_recipients",
        ["organization_id", "campaign_id", "status"],
        unique=False,
        schema="notification",
    )
    op.create_index(
        "ix_campaign_recipients_org_sent_at",
        "campaign_recipients",
        ["organization_id", "sent_at"],
        unique=False,
        schema="notification",
    )
    op.create_index(
        op.f("ix_notification_campaign_recipients_organization_id"),
        "campaign_recipients",
        ["organization_id"],
        unique=False,
        schema="notification",
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        # WITH CHECK as well as USING, or one tenant could insert rows into another (ADR-0002).
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")

    op.drop_index(
        op.f("ix_notification_campaign_recipients_organization_id"),
        table_name="campaign_recipients",
        schema="notification",
    )
    op.drop_index(
        "ix_campaign_recipients_org_sent_at",
        table_name="campaign_recipients",
        schema="notification",
    )
    op.drop_index(
        "ix_campaign_recipients_org_campaign",
        table_name="campaign_recipients",
        schema="notification",
    )
    op.drop_table("campaign_recipients", schema="notification")
    op.drop_index(
        op.f("ix_notification_contact_list_members_organization_id"),
        table_name="contact_list_members",
        schema="notification",
    )
    op.drop_index(
        "ix_contact_list_members_org_list", table_name="contact_list_members", schema="notification"
    )
    op.drop_table("contact_list_members", schema="notification")
    op.drop_index(
        op.f("ix_notification_campaigns_organization_id"),
        table_name="campaigns",
        schema="notification",
    )
    op.drop_index("ix_campaigns_org_status", table_name="campaigns", schema="notification")
    op.drop_table("campaigns", schema="notification")
    op.drop_index(
        op.f("ix_notification_contact_lists_organization_id"),
        table_name="contact_lists",
        schema="notification",
    )
    op.drop_table("contact_lists", schema="notification")
