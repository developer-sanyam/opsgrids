"""billing periods: the billing run's claim-and-record rows, and its discovery indexes

Trials never converted and nothing recurred: no process ever looked at `trial_ends_at` or
`current_period_end`. The billing run (ADR-0005 addendum, 2026-09-23) is a second sweep in the
worker that does, and this is its schema.

**`billing_periods`** is one row per subscription period the run owes an invoice for. It is the
claim a crash-safe claim → call → record needs: written `pending` with everything the billing call
will send, so a retry after a crash sends the identical request and billing's own per-period key
answers it with the invoice it already made. Tenant-scoped and policied like every other table
here — the run writes it on the NOBYPASSRLS app role under a bound tenant. The unique constraint
on `(organization_id, subscription_id, period_start)` means one period is one row however many
replicas claim at once.

**Two partial indexes on `subscriptions`** serve the cross-tenant discovery query — trials whose
end has passed, billable subscriptions whose period has closed — and, like `ix_dunning_cycles_due`,
deliberately do not lead with `organization_id`: the query exists to find *which* tenants have
work.

The outbox role's `SELECT` on `subscriptions` and `billing_periods` (discovery and the
`/health/billing-run` probe) is granted by `migrations/env.py` after every upgrade, beside the
existing `dunning_cycles` grant, because a grant a migration makes is lost the day the table is
re-created.

Revision ID: d8b3f5a7c9e1
Revises: a9c4e2b7d1f0
Create Date: 2026-09-23 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8b3f5a7c9e1"  # pragma: allowlist secret
down_revision: str | None = "a9c4e2b7d1f0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "billing_periods"
APP_ROLE = "opsgrids_subscription"


def upgrade() -> None:
    # The cadence, so a period can roll without a catalog read. Nullable and not backfilled: an
    # existing row's period is exactly 30 or 365 days long (subscribe opened it), and the run
    # reads the cadence off that length when this is NULL (`domain.infer_interval`).
    op.add_column(
        "subscriptions",
        sa.Column("billing_interval", sa.String(length=20), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        TABLE,
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_version_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("total_minor", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "state in ('pending', 'invoiced', 'skipped')", name=op.f("ck_billing_periods_state")
        ),
        sa.CheckConstraint(
            "kind in ('trial_conversion', 'renewal')", name=op.f("ck_billing_periods_kind")
        ),
        sa.CheckConstraint(
            "period_end > period_start", name=op.f("ck_billing_periods_period_is_forward")
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_billing_periods_attempts_non_negative")),
        sa.CheckConstraint("length(currency) = 3", name=op.f("ck_billing_periods_currency_iso")),
        sa.CheckConstraint(
            "total_minor is null or total_minor >= 0",
            name=op.f("ck_billing_periods_total_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            [f"{SCHEMA}.subscriptions.id"],
            name="fk_billing_periods_subscription_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_periods")),
        sa.UniqueConstraint(
            "organization_id",
            "subscription_id",
            "period_start",
            name="uq_billing_periods_org_subscription_start",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_subscription_billing_periods_organization_id"),
        TABLE,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_billing_periods_pending",
        TABLE,
        ["created_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_billing_periods_recorded", TABLE, ["recorded_at"], unique=False, schema=SCHEMA
    )
    op.create_index(
        "ix_subscriptions_trial_due",
        "subscriptions",
        ["trial_ends_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'trial'"),
    )
    op.create_index(
        "ix_subscriptions_period_due",
        "subscriptions",
        ["current_period_end"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state in ('active', 'past_due', 'suspended')"),
    )

    # Policied with the rest of this service's business tables: without it the run's tenant
    # binding would be decoration, and a bug that lost the binding would record one tenant's
    # invoice against another's subscription rather than fail.
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
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.drop_index(
        "ix_subscriptions_period_due",
        table_name="subscriptions",
        schema=SCHEMA,
        postgresql_where=sa.text("state in ('active', 'past_due', 'suspended')"),
    )
    op.drop_index(
        "ix_subscriptions_trial_due",
        table_name="subscriptions",
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'trial'"),
    )
    op.drop_index("ix_billing_periods_recorded", table_name=TABLE, schema=SCHEMA)
    op.drop_index(
        "ix_billing_periods_pending",
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.drop_index(
        op.f("ix_subscription_billing_periods_organization_id"), table_name=TABLE, schema=SCHEMA
    )
    op.drop_table(TABLE, schema=SCHEMA)
    op.drop_column("subscriptions", "billing_interval", schema=SCHEMA)
