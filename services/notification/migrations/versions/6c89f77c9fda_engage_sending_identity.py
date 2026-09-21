"""engage sending identity

The three tables an organization needs before Engage can send anything as itself: the provider
connection, the domains it may send from, and the named senders mail goes out as.

They are new tables rather than rows in `provider_accounts`. That table's only unique constraint
is `phone_number_id` — Meta's webhook routing key, unique across the platform on purpose — and
`waba_id` and `phone_number_id` are both NOT NULL. Email has no routing key of that kind and
needs transport columns that table has none of, so reusing it would mean making a constraint
WhatsApp's inbound path depends on nullable, in order to borrow three columns.

`senders.domain_id` points at `email_domains` without a foreign key. A constraint inside one
schema would be legal, but the rule that keeps a later database-per-service split a config change
is applied uniformly rather than case by case (ADR-0000 §10) — referential integrity for it is
the service's own check on write.

Autogenerate writes tables and indexes and nothing else, so the policies below are hand-written
in the same shape as every other tenant table in this schema. Table privileges are deliberately
absent: `migrations/env.py` calls `apply_schema_grants` after every run, and its ALTER DEFAULT
PRIVILEGES already covers tables a later migration creates.

Revision ID: 6c89f77c9fda
Revises: b8c1d2e3f4a5
Create Date: 2026-09-21 16:13:35.158705
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c89f77c9fda"  # pragma: allowlist secret
down_revision: str | None = "b8c1d2e3f4a5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"

#: Every one of them carries `organization_id`, so every one of them gets the policy. Written
#: out rather than derived, for the same reason the schema's original RLS migration writes its
#: list out: the one table in here that must *not* be policied (`processed_events`) is a
#: judgement, and a loop over "every table with an organization column" hides that there was one.
TENANT_TABLES = ("email_connections", "email_domains", "senders")


def upgrade() -> None:
    op.create_table(
        "email_connections",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("use_tls", sa.Boolean(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        # The app password or API key, sealed by `platform_core.pii.seal`. Bytes, because what
        # is stored is an envelope and not text anybody should be tempted to read.
        sa.Column("secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_connections")),
        # One per organization. A second would make "which connection sent this" ambiguous
        # before anything in the schema records the answer.
        sa.UniqueConstraint("organization_id", name="uq_email_connection_org"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_email_connections_organization_id"),
        "email_connections",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "email_domains",
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        # The generated record set, stored rather than regenerated on each view: a fresh DKIM
        # selector would invalidate the value the customer has already pasted into their
        # registrar, and the setup screen would be asking for something different every visit.
        sa.Column("records", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_domains")),
        sa.UniqueConstraint("organization_id", "domain", name="uq_email_domain_org_domain"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_domains_org_status",
        "email_domains",
        ["organization_id", "status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_email_domains_organization_id"),
        "email_domains",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "senders",
        sa.Column("domain_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=254), nullable=False),
        sa.Column("reply_to", sa.String(length=254), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("department", sa.String(length=80), nullable=False),
        # Written by a successful test send and by nothing else. Null is what makes a sender
        # unusable, so it has no default and no server default to drift away from.
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_senders")),
        sa.UniqueConstraint("organization_id", "address", name="uq_sender_org_address"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_senders_organization_id"),
        "senders",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_senders_org_domain",
        "senders",
        ["organization_id", "domain_id"],
        unique=False,
        schema=SCHEMA,
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        # FORCE covers the two futures where ENABLE alone is not enough: a table the app role
        # ends up owning, which would silently bypass its own policy, and a later decision to
        # drop BYPASSRLS from the admin role.
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not
        # applied to the new row of an INSERT, so without it one tenant could write a row
        # belonging to another — invisible to them, fully visible to the victim (ADR-0002).
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

    op.drop_index("ix_senders_org_domain", table_name="senders", schema=SCHEMA)
    op.drop_index(
        op.f("ix_notification_senders_organization_id"), table_name="senders", schema=SCHEMA
    )
    op.drop_table("senders", schema=SCHEMA)

    op.drop_index(
        op.f("ix_notification_email_domains_organization_id"),
        table_name="email_domains",
        schema=SCHEMA,
    )
    op.drop_index("ix_email_domains_org_status", table_name="email_domains", schema=SCHEMA)
    op.drop_table("email_domains", schema=SCHEMA)

    op.drop_index(
        op.f("ix_notification_email_connections_organization_id"),
        table_name="email_connections",
        schema=SCHEMA,
    )
    op.drop_table("email_connections", schema=SCHEMA)
