"""email open/click tracking (slice E1)

`campaigns.track_opens` / `campaigns.track_clicks`: off by default, so no existing campaign
changes behaviour. `email_links`: the destinations a signed click token can redirect to.
`email_events`: the append-only open/click record, with no IP address and only a user-agent
class. Both tables are tenant-scoped with FORCE row-level security and `WITH CHECK`.

Append-only is enforced by the database: `UPDATE` and `DELETE` are revoked from the app role
on `email_events` here, and `migrations/env.py` re-applies the revoke after every run of the
blanket re-grant, the same shape as `consent_events`.

Revision ID: e1a4c7d92b58
Revises: d5a7c3e91f48
Create Date: 2026-09-26 11:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a4c7d92b58"  # pragma: allowlist secret
down_revision: str | None = "d5a7c3e91f48"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("track_opens", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "campaigns",
        sa.Column("track_clicks", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "email_links",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_links")),
        sa.UniqueConstraint(
            "organization_id", "campaign_id", "url_hash", name=op.f("uq_email_link_url")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_email_links_organization_id"),
        "email_links",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "email_events",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("recipient_id", sa.UUID(), nullable=False),
        sa.Column("link_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("user_agent_class", sa.String(length=10), nullable=False),
        sa.Column("is_machine", sa.Boolean(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("kind IN ('open', 'click')", name=op.f("ck_email_events_kind")),
        sa.CheckConstraint(
            "user_agent_class IN ('desktop', 'mobile', 'tablet', 'bot', 'unknown')",
            name=op.f("ck_email_events_user_agent_class"),
        ),
        sa.CheckConstraint(
            "(kind = 'click') = (link_id IS NOT NULL)", name=op.f("ck_email_events_click_has_link")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_events")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_email_events_organization_id"),
        "email_events",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_events_org_campaign",
        "email_events",
        ["organization_id", "campaign_id", "kind"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_events_org_occurred",
        "email_events",
        ["organization_id", "occurred_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_events_org_recipient",
        "email_events",
        ["organization_id", "recipient_id", "kind"],
        unique=False,
        schema=SCHEMA,
    )

    _rls("email_links")
    _rls("email_events")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                REVOKE UPDATE, DELETE ON {SCHEMA}.email_events FROM "{APP_ROLE}";
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("email_events", schema=SCHEMA)
    op.drop_table("email_links", schema=SCHEMA)
    op.drop_column("campaigns", "track_clicks", schema=SCHEMA)
    op.drop_column("campaigns", "track_opens", schema=SCHEMA)
