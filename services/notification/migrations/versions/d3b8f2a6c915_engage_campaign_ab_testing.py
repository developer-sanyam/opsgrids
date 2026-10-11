"""Engage: A/B testing for email campaigns

Expand-only. `campaigns` gains version B (`subject_b`, `html_body_b`, `text_body_b`), the test
settings (`ab_test`, `ab_split_percent`, `ab_metric`, `ab_wait_minutes`) and the decision
(`ab_winner`, `ab_decided_at`); every new column is nullable or has a server default, so existing
rows read as "not an A/B test". `campaign_recipients.variant` (nullable) records which test group a
recipient was drawn into. Check constraints keep the settings in range.

Downgrade drops the constraints and columns (any A/B configuration is lost with them).

Revision ID: d3b8f2a6c915
Revises: c7d2a9e4f1b6
Create Date: 2026-10-12 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b8f2a6c915"  # pragma: allowlist secret
down_revision: str | None = "c7d2a9e4f1b6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
LOCK_TIMEOUT = "5s"

CAMPAIGN_CHECKS = (
    ("ab_split_percent", "ab_split_percent BETWEEN 1 AND 50"),
    ("ab_metric", "ab_metric IN ('open', 'click')"),
    ("ab_wait_minutes", "ab_wait_minutes BETWEEN 15 AND 10080"),
    ("ab_winner", "ab_winner IS NULL OR ab_winner IN ('a', 'b')"),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    columns = [
        sa.Column("ab_test", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("subject_b", sa.String(length=255), server_default="", nullable=False),
        sa.Column("html_body_b", sa.Text(), server_default="", nullable=False),
        sa.Column("text_body_b", sa.Text(), server_default="", nullable=False),
        sa.Column("ab_split_percent", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("ab_metric", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("ab_wait_minutes", sa.Integer(), server_default=sa.text("240"), nullable=False),
        sa.Column("ab_winner", sa.String(length=1), nullable=True),
        sa.Column("ab_decided_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in columns:
        op.add_column("campaigns", column, schema=SCHEMA)
    for name, condition in CAMPAIGN_CHECKS:
        op.create_check_constraint(
            op.f(f"ck_campaigns_{name}"), "campaigns", condition, schema=SCHEMA
        )
    op.add_column(
        "campaign_recipients",
        sa.Column("variant", sa.String(length=1), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_campaign_recipients_variant"),
        "campaign_recipients",
        "variant IS NULL OR variant IN ('a', 'b')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_constraint(
        op.f("ck_campaign_recipients_variant"), "campaign_recipients", schema=SCHEMA
    )
    op.drop_column("campaign_recipients", "variant", schema=SCHEMA)
    for name, _ in CAMPAIGN_CHECKS:
        op.drop_constraint(op.f(f"ck_campaigns_{name}"), "campaigns", schema=SCHEMA)
    for name in (
        "ab_decided_at",
        "ab_winner",
        "ab_wait_minutes",
        "ab_metric",
        "ab_split_percent",
        "text_body_b",
        "html_body_b",
        "subject_b",
        "ab_test",
    ):
        op.drop_column("campaigns", name, schema=SCHEMA)
