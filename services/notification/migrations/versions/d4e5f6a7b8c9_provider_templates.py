"""provider templates

The table that makes proactive WhatsApp possible, plus the two columns that record what was
actually handed to the provider.

`provider_templates` is deliberately not `whatsapp_templates`. The register-then-reference
lifecycle is not WhatsApp's alone — SMS in India carries DLT-registered templates with the same
shape — so the channel is a column and the concept keeps one home.

`messages.provider_template_payload` stores the exact parameter object sent. That is the same
reasoning as storing the rendered `body`: the provider may edit or retire a template, and "what
did we actually send this customer" has to survive that. A reconstruction is not an answer.

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-19 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | None = "b2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"


def upgrade() -> None:
    op.create_table(
        "provider_templates",
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
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("provider_name", sa.String(length=200), nullable=False),
        sa.Column("provider_template_id", sa.String(length=200), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("parameter_format", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("preview_text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_templates")),
        sa.UniqueConstraint(
            "organization_id", "code", "channel", name="uq_provider_templates_org_code_chan"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_provider_templates_organization_id"),
        "provider_templates",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_provider_templates_org_code",
        "provider_templates",
        ["organization_id", "code"],
        unique=False,
        schema=SCHEMA,
    )
    # The send path's question: which approved templates does this organization have on this
    # channel? Leads with the tenant column like every index here, because the RLS policy qual
    # is ANDed into every query (ADR-0002).
    op.create_index(
        "ix_provider_templates_org_channel",
        "provider_templates",
        ["organization_id", "channel", "status"],
        unique=False,
        schema=SCHEMA,
    )

    op.add_column(
        "rules",
        sa.Column("provider_template_code", sa.String(length=80), nullable=True),
        schema=SCHEMA,
    )
    # A rule now names either a body we render or a template the provider holds — exactly one.
    # `template_code` stops being mandatory rather than gaining a meaningless placeholder value,
    # because a rule that sends an approved WhatsApp template has no body of ours to author, and
    # making one up would be wording that drifts from what the customer actually receives.
    op.alter_column("rules", "template_code", nullable=True, schema=SCHEMA)
    op.add_column(
        "messages",
        sa.Column("provider_template_code", sa.String(length=80), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "messages",
        sa.Column(
            "provider_template_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        schema=SCHEMA,
    )

    # Same three conditions as every other tenant table here: policy, NOBYPASSRLS role, and the
    # transaction-local GUC. WITH CHECK is not optional — USING is not applied to an INSERT's new
    # row, so without it a tenant could write a row belonging to somebody else (ADR-0002).
    op.execute(f"ALTER TABLE {SCHEMA}.provider_templates ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.provider_templates FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.provider_templates
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.provider_templates")
    op.drop_column("messages", "provider_template_payload", schema=SCHEMA)
    op.drop_column("messages", "provider_template_code", schema=SCHEMA)

    # A rule that sends a provider template has no `template_code` — that is the whole point of
    # making the column nullable on the way up. Restoring NOT NULL with such rows present fails,
    # so they are removed first.
    #
    # This is data loss, and it is the honest consequence of removing the feature: a rule whose
    # only wording came from a provider template cannot be expressed in the older schema at all,
    # and leaving it behind with an invented `template_code` would be worse — a rule that fires
    # and sends something nobody wrote.
    #
    # Written because the round-trip check runs against an empty database and therefore could
    # not have caught it; it surfaced on a scratch database that had rules in it. A downgrade
    # that only works when there is no data is not a rollback.
    op.execute(
        f"DELETE FROM {SCHEMA}.rules WHERE template_code IS NULL"
    )
    op.alter_column("rules", "template_code", nullable=False, schema=SCHEMA)
    op.drop_column("rules", "provider_template_code", schema=SCHEMA)
    op.drop_table("provider_templates", schema=SCHEMA)
