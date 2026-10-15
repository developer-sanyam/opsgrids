"""integrations framework: channel kind, carrier link on shipments, the integration-event inbox

One additive revision, no row rewritten and no backfill:

1. `channels.kind` (`sales | erp | pos | carrier`, NOT NULL, server default `sales`): every existing
   row is a sales channel, which is what the default says.
2. `shipments.channel_id` (in-schema FK to `channels`, RESTRICT) and `external_shipment_id`, both
   nullable: a manually-entered carrier (`carrier_name`) keeps both NULL. A CHECK refuses an
   external id without a channel; a partial unique index makes a carrier's shipment id name one
   shipment per connection.
3. `integration_events`: the append-only inbound-webhook inbox, modelled on
   `payment.provider_events`. `organization_id` on every row, ENABLE + FORCE RLS with the standard
   `tenant_isolation` policy, unique `(organization_id, provider, event_id)`. Append-only is a
   privilege (`UPDATE`/`DELETE` revoked from the app role by `migrations/env.py` on every run), and
   the outbox role's column grant on `channels` gains `kind` and `provider` there too.

Downgrade **refuses** while it would lose data: any `integration_events` row, any shipment booked
through a channel, any channel that is not a sales one. Nothing else in the previous code reads
these columns, so rolling the *image* back needs no downgrade at all.

Revision ID: f4a8c2e6b0d1
Revises: e3b7c9a1d5f2
Create Date: 2026-10-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a8c2e6b0d1"  # pragma: allowlist secret
down_revision: str | None = "e3b7c9a1d5f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
TABLE = "integration_events"


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("kind", sa.String(length=10), server_default="sales", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_channels_kind_known"),
        "channels",
        "kind IN ('sales', 'erp', 'pos', 'carrier')",
        schema=SCHEMA,
    )

    op.add_column("shipments", sa.Column("channel_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column(
        "shipments",
        sa.Column("external_shipment_id", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_shipments_channel_id_channels"),
        "shipments",
        "channels",
        ["channel_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_shipments_external_shipment_needs_channel"),
        "shipments",
        "channel_id IS NOT NULL OR external_shipment_id IS NULL",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_shipments_org_channel_external",
        "shipments",
        ["organization_id", "channel_id", "external_shipment_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("external_shipment_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        TABLE,
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_name", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("outcome_reason", sa.String(length=40), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(outcome = 'ignored') = (outcome_reason IS NOT NULL)",
            name=op.f("ck_integration_events_reason_iff_ignored"),
        ),
        sa.CheckConstraint(
            "event_id <> ''", name=op.f("ck_integration_events_event_id_not_empty")
        ),
        sa.CheckConstraint(
            "outcome IN ('received', 'ignored')", name=op.f("ck_integration_events_outcome_known")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{SCHEMA}.channels.id"],
            name=op.f("fk_integration_events_channel_id_channels"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_events")),
        sa.UniqueConstraint(
            "organization_id", "provider", "event_id", name="uq_integration_events"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_integration_events_org_channel_at",
        TABLE,
        ["organization_id", "channel_id", "received_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_operations_integration_events_organization_id"),
        TABLE,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{TABLE}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    for what, sql in (
        ("integration_events rows", f"SELECT count(*) FROM {SCHEMA}.{TABLE}"),
        (
            "shipments booked through a channel",
            f"SELECT count(*) FROM {SCHEMA}.shipments WHERE channel_id IS NOT NULL",
        ),
        (
            "non-sales channels",
            f"SELECT count(*) FROM {SCHEMA}.channels WHERE kind <> 'sales'",
        ),
    ):
        count = bind.execute(sa.text(sql)).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {count} {what} exist and dropping the columns or table "
                "would lose them. Export or clear them deliberately first."
            )
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.drop_table(TABLE, schema=SCHEMA)
    op.drop_index(
        "uq_shipments_org_channel_external",
        table_name="shipments",
        schema=SCHEMA,
        postgresql_where=sa.text("external_shipment_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_constraint(
        op.f("ck_shipments_external_shipment_needs_channel"),
        "shipments",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_shipments_channel_id_channels"), "shipments", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("shipments", "external_shipment_id", schema=SCHEMA)
    op.drop_column("shipments", "channel_id", schema=SCHEMA)
    op.drop_constraint(op.f("ck_channels_kind_known"), "channels", schema=SCHEMA, type_="check")
    op.drop_column("channels", "kind", schema=SCHEMA)
