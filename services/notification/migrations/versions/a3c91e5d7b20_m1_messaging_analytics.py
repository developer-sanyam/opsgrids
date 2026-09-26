"""messaging analytics: the rate card and the indexes its queries need

`messaging_rate_cards` prices a message count into a cost *estimate*. Two kinds of row share it:
the platform default (`organization_id` = the nil UUID, the platform tenant) and an organization's
own override. The shipped defaults are **placeholders** — labelled `placeholder-2026-09`,
`is_placeholder = true` — and are not Meta's published rates for any country. Real rates for the
organization's country must be entered through `PUT /engage/analytics/rate-card`.

Row-level security is hand-written in the shape of every other tenant table here (`ENABLE` and
`FORCE`, `WITH CHECK` as well as `USING`), plus one extra read-only policy so every tenant can
*read* the platform default row without being able to write it.

Three indexes on existing tables, each for one query the analytics endpoints run (plans checked
with EXPLAIN on seeded data):

  * `ix_messages_org_channel_created` — the outbound series, by-category and failure queries.
  * `ix_inbound_messages_org_occurred` — replies and inbound volume across a date range.
  * `ix_conversations_org_created` — new conversations in a window (recent activity).

Downgrade drops the table (including any organization's overrides) and the three indexes.

Revision ID: a3c91e5d7b20
Revises: d5a7c3e91f48
Create Date: 2026-09-26 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c91e5d7b20"  # pragma: allowlist secret
down_revision: str | None = "d5a7c3e91f48"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"
PLATFORM_ORG = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_LABEL = "placeholder-2026-09"

#: INR paise per 1000 messages. PLACEHOLDERS, not Meta's rates: marketing 784.60 rupees per
#: 1000, utility and authentication 115.00, service (free-text reply inside the 24h window) free.
PLACEHOLDER_RATES = (
    ("marketing", 78460),
    ("utility", 11500),
    ("authentication", 11500),
    ("service", 0),
)


def upgrade() -> None:
    op.create_table(
        "messaging_rate_cards",
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("amount_minor_per_1000_messages", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("is_placeholder", sa.Boolean(), nullable=False),
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
            "category IN ('marketing', 'utility', 'authentication', 'service')",
            name=op.f("ck_messaging_rate_cards_category"),
        ),
        sa.CheckConstraint("channel IN ('whatsapp')", name=op.f("ck_messaging_rate_cards_channel")),
        sa.CheckConstraint(
            "amount_minor_per_1000_messages >= 0",
            name=op.f("ck_messaging_rate_cards_amount_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messaging_rate_cards")),
        sa.UniqueConstraint(
            "organization_id",
            "channel",
            "category",
            "effective_from",
            name="uq_messaging_rate_cards_org_price_point",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_messaging_rate_cards_organization_id"),
        "messaging_rate_cards",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.execute(f"ALTER TABLE {SCHEMA}.messaging_rate_cards ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.messaging_rate_cards FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.messaging_rate_cards
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )
    # Read-only: every tenant may see the platform default, none may write it. Permissive
    # policies OR together, and this one is `FOR SELECT`, so it grants no write of any kind.
    op.execute(
        f"""
        CREATE POLICY platform_default_read ON {SCHEMA}.messaging_rate_cards
            FOR SELECT TO {APP_ROLE}
            USING (organization_id = '{PLATFORM_ORG}'::uuid)
        """
    )

    for category, amount in PLACEHOLDER_RATES:
        op.execute(
            sa.text(
                """
                INSERT INTO notification.messaging_rate_cards
                    (id, organization_id, channel, category, amount_minor_per_1000_messages,
                     currency, effective_from, label, is_placeholder, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), CAST(:org AS uuid), 'whatsapp', :category, :amount,
                     'INR', DATE '2026-09-01', :label, true, now(), now())
                ON CONFLICT (organization_id, channel, category, effective_from) DO NOTHING
                """
            ).bindparams(
                org=PLATFORM_ORG, category=category, amount=amount, label=PLACEHOLDER_LABEL
            )
        )

    op.create_index(
        "ix_messages_org_channel_created",
        "messages",
        ["organization_id", "channel", "created_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_include=["state", "direction"],
    )
    op.create_index(
        "ix_inbound_messages_org_occurred",
        "inbound_messages",
        ["organization_id", "occurred_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_org_created",
        "conversations",
        ["organization_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_org_created", table_name="conversations", schema=SCHEMA)
    op.drop_index("ix_inbound_messages_org_occurred", table_name="inbound_messages", schema=SCHEMA)
    op.drop_index(
        "ix_messages_org_channel_created",
        table_name="messages",
        schema=SCHEMA,
        postgresql_include=["state", "direction"],
    )
    op.execute(f"DROP POLICY IF EXISTS platform_default_read ON {SCHEMA}.messaging_rate_cards")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.messaging_rate_cards")
    op.drop_index(
        op.f("ix_notification_messaging_rate_cards_organization_id"),
        table_name="messaging_rate_cards",
        schema=SCHEMA,
    )
    op.drop_table("messaging_rate_cards", schema=SCHEMA)
