"""billing run: prorations, closing periods, voids, backoff, and deferred downgrades

The review of the first billing run (ADR-0005 addendum, 2026-09-23, "Review fixes") widened what a
`billing_periods` row can be and what a subscription remembers between periods. All additive:

* **Two more kinds.** `proration` — an upgrade's prorated difference for the rest of the current
  period, keyed on the change (`change_id`, and the pin and items it moved off); `close` — the
  usage-only closing period a cancellation or suspension leaves. The unique key grows `kind`,
  because a close starts where the renewal that opened the period started.
* **Two more states.** `void_pending` / `voided`: an invoice raised for a period the subscription
  moved past between claim and record is voided through billing, not just logged.
* **Backoff apart from the lease** (`next_attempt_at`), a `permanent_failure` flag for the health
  probe, and `period_end` nullable for the one failure row that has no period (`one_time`).
* **A conversion charge that failed before the record step** (`payment_failed_at`, reason).
* **On `subscriptions`:** `pending_change` / `pending_plan_version_id` (a downgrade waiting for the
  next period) and `usage_billed_through` (so a suspension's closing usage is not billed again).

Revision ID: e4f6a8c0b2d4
Revises: d8b3f5a7c9e1
Create Date: 2026-09-24 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4f6a8c0b2d4"  # pragma: allowlist secret
down_revision: str | None = "d8b3f5a7c9e1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "billing_periods"


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("pending_change", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "subscriptions",
        sa.Column("pending_plan_version_id", sa.UUID(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "subscriptions",
        sa.Column("usage_billed_through", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    for column in (
        sa.Column("change_id", sa.UUID(), nullable=True),
        sa.Column("from_plan_version_id", sa.UUID(), nullable=True),
        sa.Column("from_items", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("full_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "permanent_failure", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("payment_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_failure_reason", sa.String(length=200), nullable=True),
    ):
        op.add_column(TABLE, column, schema=SCHEMA)
    op.alter_column(TABLE, "period_end", nullable=True, schema=SCHEMA)

    op.drop_constraint(
        "uq_billing_periods_org_subscription_start", TABLE, schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_billing_periods_org_subscription_kind_start",
        TABLE,
        ["organization_id", "subscription_id", "kind", "period_start"],
        schema=SCHEMA,
    )
    for name in ("state", "kind", "period_is_forward"):
        op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_state"),
        TABLE,
        "state in ('pending', 'invoiced', 'skipped', 'void_pending', 'voided')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_kind"),
        TABLE,
        "kind in ('trial_conversion', 'renewal', 'proration', 'close')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_period_is_forward"),
        TABLE,
        "period_end is null or period_end > period_start",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_billing_periods_actionable",
        TABLE,
        ["created_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state in ('pending', 'void_pending')"),
    )


def downgrade() -> None:
    # Rows the older shape cannot hold are the new kinds and states and the period-less failure
    # rows; they are work items, not financial facts (the invoices are billing's), so they go.
    op.execute(
        f"DELETE FROM {SCHEMA}.{TABLE} WHERE kind IN ('proration', 'close') "
        "OR state IN ('void_pending', 'voided') OR period_end IS NULL"
    )
    op.drop_index(
        "ix_billing_periods_actionable",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("state in ('pending', 'void_pending')"),
    )
    for name in ("state", "kind", "period_is_forward"):
        op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_state"), TABLE, "state in ('pending', 'invoiced', 'skipped')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_kind"), TABLE, "kind in ('trial_conversion', 'renewal')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_period_is_forward"), TABLE, "period_end > period_start", schema=SCHEMA
    )
    op.drop_constraint(
        "uq_billing_periods_org_subscription_kind_start", TABLE, schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_billing_periods_org_subscription_start",
        TABLE,
        ["organization_id", "subscription_id", "period_start"],
        schema=SCHEMA,
    )
    op.alter_column(TABLE, "period_end", nullable=False, schema=SCHEMA)
    for name in (
        "payment_failure_reason",
        "payment_failed_at",
        "permanent_failure",
        "next_attempt_at",
        "full_period_start",
        "from_items",
        "from_plan_version_id",
        "change_id",
    ):
        op.drop_column(TABLE, name, schema=SCHEMA)
    for name in ("usage_billed_through", "pending_plan_version_id", "pending_change"):
        op.drop_column("subscriptions", name, schema=SCHEMA)
