"""engage contacts and suppressions

Engage's audience: the contacts an organization's campaigns may reach, and the addresses that
must never be mailed. The address is PII, so neither table holds it in the clear — `email_enc`
is sealed by `platform_core.pii.seal`, `email_masked` is what reads return, and `email_index` is
the keyed blind index both uniqueness and the suppression anti-join run on.

Suppressions are keyed by address rather than by contact on purpose: an unsubscribe must outlive
the contact row, so deleting and re-importing someone cannot make them mailable again.

Policies are hand-written in the same shape as every other tenant table in this schema; grants
come from `apply_schema_grants` in `migrations/env.py`.

Revision ID: e7d2f4a91b3c
Revises: c3a1ecc45df8
Create Date: 2026-09-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7d2f4a91b3c"  # pragma: allowlist secret
down_revision: str | None = "c3a1ecc45df8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"

TENANT_TABLES = ("contacts", "suppressions")


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


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("email_enc", sa.LargeBinary(), nullable=False),
        sa.Column("email_masked", sa.String(length=254), nullable=False),
        sa.Column("email_index", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("organization_id", "email_index", name="uq_contact_org_email"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_contacts_organization_id"),
        "contacts",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contacts_org_name", "contacts", ["organization_id", "name"], unique=False, schema=SCHEMA
    )

    op.create_table(
        "suppressions",
        sa.Column("email_enc", sa.LargeBinary(), nullable=False),
        sa.Column("email_masked", sa.String(length=254), nullable=False),
        sa.Column("email_index", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "reason IN ('unsubscribed', 'bounced', 'complained', 'manual')",
            name=op.f("ck_suppressions_reason"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppressions")),
        sa.UniqueConstraint("organization_id", "email_index", name="uq_suppression_org_email"),
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
        op.f("ix_notification_suppressions_organization_id"),
        table_name="suppressions",
        schema=SCHEMA,
    )
    op.drop_table("suppressions", schema=SCHEMA)

    op.drop_index("ix_contacts_org_name", table_name="contacts", schema=SCHEMA)
    op.drop_index(
        op.f("ix_notification_contacts_organization_id"), table_name="contacts", schema=SCHEMA
    )
    op.drop_table("contacts", schema=SCHEMA)
