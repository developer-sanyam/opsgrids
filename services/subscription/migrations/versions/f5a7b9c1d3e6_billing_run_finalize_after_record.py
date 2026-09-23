"""billing run: finalize after record, and one proration per change

Review round 2 (ADR-0005 addendum, "Review fixes, round 2"):

* **Finalize after record (M5).** Billing now raises a run invoice as a `draft` with no event; the
  run records the claim — `finalize_pending` — and only then asks billing to finalize it, keyed and
  idempotent. So a period superseded between claim and record is a draft voided before anything
  was announced, and nothing can have been charged for it. `invoiced` becomes `finalized`.
* **One proration per change (B2).** A partial unique index on the change id, which is now
  deterministic when a lifecycle claim is pending, so the change-plan path and the record step's
  check can never both bill one upgrade.

Revision ID: f5a7b9c1d3e6
Revises: e4f6a8c0b2d4
Create Date: 2026-09-24 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a7b9c1d3e6"  # pragma: allowlist secret
down_revision: str | None = "e4f6a8c0b2d4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "billing_periods"


def upgrade() -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_state"), TABLE, schema=SCHEMA, type_="check")
    op.execute(f"UPDATE {SCHEMA}.{TABLE} SET state = 'finalized' WHERE state = 'invoiced'")
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_state"),
        TABLE,
        "state in ('pending', 'finalize_pending', 'finalized', 'skipped', 'void_pending', "
        "'voided')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_billing_periods_finalize_pending",
        TABLE,
        ["created_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'finalize_pending'"),
    )
    op.create_index(
        "uq_billing_periods_org_subscription_change",
        TABLE,
        ["organization_id", "subscription_id", "change_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("change_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_billing_periods_org_subscription_change",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("change_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_billing_periods_finalize_pending",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'finalize_pending'"),
    )
    op.drop_constraint(op.f(f"ck_{TABLE}_state"), TABLE, schema=SCHEMA, type_="check")
    # A recorded draft was an invoiced claim in the older shape.
    op.execute(
        f"UPDATE {SCHEMA}.{TABLE} SET state = 'invoiced' "
        "WHERE state IN ('finalized', 'finalize_pending')"
    )
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_state"),
        TABLE,
        "state in ('pending', 'invoiced', 'skipped', 'void_pending', 'voided')",
        schema=SCHEMA,
    )
