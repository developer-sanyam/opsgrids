"""engage audience

Contacts, lists, list membership and suppression — who a campaign may target and who it may
not. `sendable_contacts` (`app/selectors.py`) is the one query a future campaign send reads
through; every writer in `app/services.py` exists to keep it honest, and it is tested now
rather than when campaigns land.

Two foreign keys, both inside this schema and therefore both allowed (ADR-0000 §10):
`contact_list_members.list_id` -> `contact_lists.id` and `contact_list_members.contact_id` /
`suppressions.contact_id` -> `contacts.id`, all `ON DELETE CASCADE` — a membership or a
suppression row has no meaning once the contact or the list it names is gone.

`email` is stored in plaintext on `contacts`, matching customer-service's own precedent for a
work contact address (`customer.Contact`): it is the record's display identity and the thing a
campaign searches and deduplicates on, not a secret to seal.

Autogenerate writes tables and indexes and nothing else, so the RLS policies below are
hand-written in the same shape as `6c89f77c9fda_engage_sending_identity.py`. Table privileges
are deliberately absent: `migrations/env.py` calls `apply_schema_grants` after every run.

Revision ID: e7c2a1f4b6d9
Revises: c3a1ecc45df8
Create Date: 2026-09-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7c2a1f4b6d9"  # pragma: allowlist secret
down_revision: str | None = "c3a1ecc45df8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"

#: Every one of them carries `organization_id`, so every one of them gets the policy — the same
#: discipline `6c89f77c9fda`'s own list follows.
TENANT_TABLES = ("suppressions", "contact_list_members", "contact_lists", "contacts")


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("organization_id", "email", name="uq_contacts_org_email"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contacts_org_email", "contacts", ["organization_id", "email"], unique=False, schema=SCHEMA
    )
    op.create_index(
        op.f("ix_notification_contacts_organization_id"),
        "contacts",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "contact_lists",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_lists")),
        sa.UniqueConstraint("organization_id", "name", name="uq_contact_lists_org_name"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_contact_lists_organization_id"),
        "contact_lists",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "contact_list_members",
        sa.Column("list_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["notification.contact_lists.id"],
            name=op.f("fk_contact_list_members_list_id_contact_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["notification.contacts.id"],
            name=op.f("fk_contact_list_members_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_list_members")),
        sa.UniqueConstraint(
            "list_id", "contact_id", name="uq_contact_list_members_list_contact"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contact_list_members_org_list",
        "contact_list_members",
        ["organization_id", "list_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contact_list_members_org_contact",
        "contact_list_members",
        ["organization_id", "contact_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_contact_list_members_organization_id"),
        "contact_list_members",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "suppressions",
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["notification.contacts.id"],
            name=op.f("fk_suppressions_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppressions")),
        sa.UniqueConstraint("organization_id", "contact_id", name="uq_suppressions_org_contact"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_suppressions_org_contact",
        "suppressions",
        ["organization_id", "contact_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_suppressions_organization_id"),
        "suppressions",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    for table in TENANT_TABLES:
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


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")

    op.drop_index("ix_suppressions_org_contact", table_name="suppressions", schema=SCHEMA)
    op.drop_index(
        op.f("ix_notification_suppressions_organization_id"),
        table_name="suppressions",
        schema=SCHEMA,
    )
    op.drop_table("suppressions", schema=SCHEMA)

    op.drop_index(
        "ix_contact_list_members_org_contact",
        table_name="contact_list_members",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_contact_list_members_org_list", table_name="contact_list_members", schema=SCHEMA
    )
    op.drop_index(
        op.f("ix_notification_contact_list_members_organization_id"),
        table_name="contact_list_members",
        schema=SCHEMA,
    )
    op.drop_table("contact_list_members", schema=SCHEMA)

    op.drop_index(
        op.f("ix_notification_contact_lists_organization_id"),
        table_name="contact_lists",
        schema=SCHEMA,
    )
    op.drop_table("contact_lists", schema=SCHEMA)

    op.drop_index(
        op.f("ix_notification_contacts_organization_id"), table_name="contacts", schema=SCHEMA
    )
    op.drop_index("ix_contacts_org_email", table_name="contacts", schema=SCHEMA)
    op.drop_table("contacts", schema=SCHEMA)
