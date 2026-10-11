"""engage segments and whatsapp campaigns

Expand-only. A new tenant table `segments` (a saved filter over the contact book), and on
`campaigns`: `channel` (default `email`, so every existing row keeps its meaning),
`segment_id`, `provider_template_id`, `template_values`, a third audience `segment`, and
`sender_id` relaxed to nullable — a WhatsApp campaign has no email sender. The CHECK
`(channel = 'email') = (sender_id IS NOT NULL)` holds for every existing row, which all carry
a sender. Foreign keys stay inside this schema.

Downgrade deletes WhatsApp and segment campaigns (they cannot be represented once `sender_id`
is NOT NULL again), then reverses the rest.

Revision ID: b3d7e1f9a2c4
Revises: f1c8a2d5b7e4
Create Date: 2026-10-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d7e1f9a2c4"  # pragma: allowlist secret
down_revision: str | None = "f1c8a2d5b7e4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.create_table(
        "segments",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("list_id", sa.UUID(), nullable=True),
        sa.Column("created_within_days", sa.Integer(), nullable=True),
        sa.Column("active_within_days", sa.Integer(), nullable=True),
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
            ["list_id"],
            [f"{SCHEMA}.contact_lists.id"],
            name=op.f("fk_segments_list_id_contact_lists"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_segments")),
        sa.UniqueConstraint("organization_id", "name", name="uq_segment_org_name"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_segments_organization_id"),
        "segments",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.segments ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.segments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.segments
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )

    op.add_column(
        "campaigns",
        sa.Column(
            "channel", sa.String(length=20), server_default=sa.text("'email'"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column("campaigns", sa.Column("segment_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column(
        "campaigns", sa.Column("provider_template_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "template_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.alter_column("campaigns", "sender_id", nullable=True, schema=SCHEMA)
    op.create_foreign_key(
        op.f("fk_campaigns_segment_id_segments"),
        "campaigns",
        "segments",
        ["segment_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_campaigns_provider_template_id_provider_templates"),
        "campaigns",
        "provider_templates",
        ["provider_template_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.drop_constraint(op.f("ck_campaigns_audience"), "campaigns", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_campaigns_audience"),
        "campaigns",
        "audience IN ('list', 'all_sendable', 'segment')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_campaigns_audience_segment"),
        "campaigns",
        "(audience = 'segment') = (segment_id IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_campaigns_channel"), "campaigns", "channel IN ('email', 'whatsapp')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_campaigns_channel_sender"),
        "campaigns",
        "(channel = 'email') = (sender_id IS NOT NULL)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"DELETE FROM {SCHEMA}.campaigns WHERE channel <> 'email' OR audience = 'segment'")
    op.drop_constraint(
        op.f("ck_campaigns_channel_sender"), "campaigns", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(op.f("ck_campaigns_channel"), "campaigns", schema=SCHEMA, type_="check")
    op.drop_constraint(
        op.f("ck_campaigns_audience_segment"), "campaigns", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(op.f("ck_campaigns_audience"), "campaigns", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_campaigns_audience"),
        "campaigns",
        "audience IN ('list', 'all_sendable')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("fk_campaigns_provider_template_id_provider_templates"),
        "campaigns",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_campaigns_segment_id_segments"), "campaigns", schema=SCHEMA, type_="foreignkey"
    )
    op.alter_column("campaigns", "sender_id", nullable=False, schema=SCHEMA)
    op.drop_column("campaigns", "template_values", schema=SCHEMA)
    op.drop_column("campaigns", "provider_template_id", schema=SCHEMA)
    op.drop_column("campaigns", "segment_id", schema=SCHEMA)
    op.drop_column("campaigns", "channel", schema=SCHEMA)
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.segments")
    op.drop_index(
        op.f("ix_notification_segments_organization_id"), table_name="segments", schema=SCHEMA
    )
    op.drop_table("segments", schema=SCHEMA)
