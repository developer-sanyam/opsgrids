"""whatsapp conversations and inbound messages

The inbound half of the WhatsApp pipeline (docs/whatsapp/audit.md C5/C6/C9). Three new tables and
two columns on an existing one:

- `conversations` — one row per customer number this organization has ever exchanged WhatsApp
  with. The number is sealed like any other PII (`wa_address_enc`/`wa_address_masked`), with a
  keyed blind index (`wa_address_index`) so the webhook fold can find "does this conversation
  already exist" without decrypting a single row. `last_customer_message_at` is the 24-hour
  customer service window's clock.
- `inbound_messages` — one row per customer message folded from a webhook, `provider_message_id`
  unique per organization. **Nothing here is plaintext PII**: `body_enc` (text only — Meta's other
  shapes are not parsed), `raw_enc` (the provider's whole message object, including `from`), and
  `profile_name_enc` are all sealed; `body_masked` and `profile_name_masked` are fixed-shape
  placeholders that reveal neither the real value nor its length.
- `inbound_dead_letters` — one row per webhook item that failed to fold, so one bad item cannot
  fail the whole `POST` or repeat-fail on Meta's retry of the entire body. No PII, no payload.
- `messages.direction` and `messages.conversation_id` — so an outbound reply inside a
  conversation can be told apart from a rule-driven notification or a campaign email, and can be
  joined into the thread it belongs to.

Policies are hand-written in the same shape as every other tenant table in this schema; grants
come from `apply_schema_grants` in `migrations/env.py`.

Revision ID: b6f1c2a7d8e9
Revises: a1c7e93f5b02
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6f1c2a7d8e9"  # pragma: allowlist secret
down_revision: str | None = "a1c7e93f5b02"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"

TENANT_TABLES = ("conversations", "inbound_messages", "inbound_dead_letters")


def _tenant_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
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
    ]


def _enable_rls(table: str) -> None:
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


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("wa_address_enc", sa.LargeBinary(), nullable=False),
        sa.Column("wa_address_masked", sa.String(length=40), nullable=False),
        sa.Column("wa_address_index", sa.String(length=64), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("last_customer_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("assignee_membership_id", sa.UUID(), nullable=True),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "organization_id",
            "channel",
            "wa_address_index",
            name="uq_conversations_org_channel_address",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_conversations_organization_id"),
        "conversations",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_org_status",
        "conversations",
        ["organization_id", "status", "last_customer_message_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_org_contact",
        "conversations",
        ["organization_id", "contact_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "inbound_messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("body_enc", sa.LargeBinary(), nullable=True),
        # A fixed-shape placeholder (`services._BODY_MASK_PLACEHOLDER`), never a function of the
        # real body's length — 40 is generous headroom for that constant, not a text preview.
        sa.Column("body_masked", sa.String(length=40), nullable=False),
        # The provider's whole message object, sealed — see the module docstring on why there is
        # no plaintext `raw` column at all.
        sa.Column("raw_enc", sa.LargeBinary(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_name_enc", sa.LargeBinary(), nullable=True),
        sa.Column("profile_name_masked", sa.String(length=8), nullable=True),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{SCHEMA}.conversations.id"],
            name=op.f("fk_inbound_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbound_messages")),
        sa.UniqueConstraint(
            "organization_id", "provider_message_id", name="uq_inbound_messages_org_provider_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_inbound_messages_organization_id"),
        "inbound_messages",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inbound_messages_org_conversation",
        "inbound_messages",
        ["organization_id", "conversation_id", "occurred_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "inbound_dead_letters",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("provider_message_id", sa.String(length=200), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbound_dead_letters")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_inbound_dead_letters_organization_id"),
        "inbound_dead_letters",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inbound_dead_letters_org_created",
        "inbound_dead_letters",
        ["organization_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )

    for table in TENANT_TABLES:
        _enable_rls(table)

    op.add_column(
        "messages",
        sa.Column("direction", sa.String(length=10), nullable=False, server_default="outbound"),
        schema=SCHEMA,
    )
    op.alter_column("messages", "direction", server_default=None, schema=SCHEMA)
    op.add_column(
        "messages", sa.Column("conversation_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_index(
        "ix_messages_org_conversation",
        "messages",
        ["organization_id", "conversation_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_org_conversation", table_name="messages", schema=SCHEMA)
    op.drop_column("messages", "conversation_id", schema=SCHEMA)
    op.drop_column("messages", "direction", schema=SCHEMA)

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")

    op.drop_index(
        "ix_inbound_dead_letters_org_created", table_name="inbound_dead_letters", schema=SCHEMA
    )
    op.drop_index(
        op.f("ix_notification_inbound_dead_letters_organization_id"),
        table_name="inbound_dead_letters",
        schema=SCHEMA,
    )
    op.drop_table("inbound_dead_letters", schema=SCHEMA)

    op.drop_index(
        "ix_inbound_messages_org_conversation", table_name="inbound_messages", schema=SCHEMA
    )
    op.drop_index(
        op.f("ix_notification_inbound_messages_organization_id"),
        table_name="inbound_messages",
        schema=SCHEMA,
    )
    op.drop_table("inbound_messages", schema=SCHEMA)

    op.drop_index("ix_conversations_org_contact", table_name="conversations", schema=SCHEMA)
    op.drop_index("ix_conversations_org_status", table_name="conversations", schema=SCHEMA)
    op.drop_index(
        op.f("ix_notification_conversations_organization_id"),
        table_name="conversations",
        schema=SCHEMA,
    )
    op.drop_table("conversations", schema=SCHEMA)
