"""WhatsApp Embedded Signup runs, numbers cache and connection health (slice W1)

`w1_setup_runs`: one row per `signup/complete`, the state the worker advances. Its sealed `code_enc`
and `token_enc` are wiped as soon as they have been used. `w1_numbers`: the cache of the numbers
on the organization's WABA. `w1_connection_health`: what was last learned about the token and the
webhook subscription. All three are tenant-scoped with FORCE row-level security and `WITH CHECK`.

Revision ID: b3f8d20c71a5
Revises: e1a4c7d92b58
Create Date: 2026-09-26 14:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f8d20c71a5"  # pragma: allowlist secret
down_revision: str | None = "e1a4c7d92b58"  # pragma: allowlist secret
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


def _base_columns() -> list[sa.Column]:
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
    ]


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_notification_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def upgrade() -> None:
    op.create_table(
        "w1_setup_runs",
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("code_enc", sa.LargeBinary(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("waba_hint", sa.String(length=80), nullable=True),
        sa.Column("phone_number_hint", sa.String(length=80), nullable=True),
        sa.Column("started_by", sa.String(length=80), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_w1_setup_runs")),
        sa.UniqueConstraint("organization_id", "code_hash", name=op.f("uq_w1_setup_runs_code")),
        schema=SCHEMA,
    )
    _org_index("w1_setup_runs")
    op.create_index(
        "ix_w1_setup_runs_org_status",
        "w1_setup_runs",
        ["organization_id", "status", "created_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "w1_numbers",
        sa.Column("waba_id", sa.String(length=80), nullable=False),
        sa.Column("phone_number_id", sa.String(length=80), nullable=False),
        sa.Column("display_phone_number", sa.String(length=40), nullable=False),
        sa.Column("verified_name", sa.String(length=200), nullable=True),
        sa.Column("name_status", sa.String(length=20), nullable=False),
        sa.Column("quality_rating", sa.String(length=10), nullable=False),
        sa.Column("messaging_limit", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_w1_numbers")),
        sa.UniqueConstraint(
            "organization_id", "phone_number_id", name=op.f("uq_w1_numbers_org_number")
        ),
        schema=SCHEMA,
    )
    _org_index("w1_numbers")

    op.create_table(
        "w1_connection_health",
        sa.Column("token_state", sa.String(length=10), nullable=False),
        sa.Column("token_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_subscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_setup_id", sa.UUID(), nullable=True),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_w1_connection_health")),
        sa.UniqueConstraint("organization_id", name=op.f("uq_w1_connection_health_org")),
        schema=SCHEMA,
    )
    _org_index("w1_connection_health")

    for table in ("w1_setup_runs", "w1_numbers", "w1_connection_health"):
        _rls(table)


def downgrade() -> None:
    op.drop_table("w1_connection_health", schema=SCHEMA)
    op.drop_table("w1_numbers", schema=SCHEMA)
    op.drop_table("w1_setup_runs", schema=SCHEMA)
