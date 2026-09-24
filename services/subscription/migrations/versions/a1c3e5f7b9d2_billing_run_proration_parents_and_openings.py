"""billing run: proration parents, and opening charges

Review round 3 (ADR-0005 addendum, "Review fixes, round 3"):

* **`against_period_id`** — the lifecycle claim a proration was priced against. A proration is
  billed only once its parent is finalized, and is skipped (or its draft voided) when the parent is
  voided or skipped: an upgrade's difference is never charged for a period whose base was not.
* **`subscriptions.suspended_at`** — when the current suspension took effect, so a period the
  customer was served part of is charged even when the run rolls it after the suspension.
* **`opening`** — a new kind: the recurring charge for time no period charge covers. A
  subscription that starts paid was never charged its first period; one resumed after a suspended
  roll was never charged the rest of that period.

Revision ID: a1c3e5f7b9d2
Revises: f5a7b9c1d3e6
Create Date: 2026-09-24 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"  # pragma: allowlist secret
down_revision: str | None = "f5a7b9c1d3e6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "billing_periods"


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("against_period_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_billing_periods_org_against",
        TABLE,
        ["organization_id", "against_period_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("against_period_id IS NOT NULL"),
    )
    op.drop_constraint(op.f(f"ck_{TABLE}_kind"), TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_kind"),
        TABLE,
        "kind in ('trial_conversion', 'renewal', 'proration', 'close', 'opening')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # An opening row is a work item, not a financial fact (the invoice is billing's).
    op.execute(f"DELETE FROM {SCHEMA}.{TABLE} WHERE kind = 'opening'")
    op.drop_constraint(op.f(f"ck_{TABLE}_kind"), TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_kind"),
        TABLE,
        "kind in ('trial_conversion', 'renewal', 'proration', 'close')",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_billing_periods_org_against",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("against_period_id IS NOT NULL"),
    )
    op.drop_column(TABLE, "against_period_id", schema=SCHEMA)
    op.drop_column("subscriptions", "suspended_at", schema=SCHEMA)
