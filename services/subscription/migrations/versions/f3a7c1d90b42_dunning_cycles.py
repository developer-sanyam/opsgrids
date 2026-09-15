"""dunning cycles

The table ADR-0005 needs: one row per failed collection, holding the grace window and the rung of
the retry ladder the subscription has reached. Two things here are decisions rather than shape.

**It is tenant-scoped and policied like everything else in this schema.** A dunning cycle belongs
to exactly one organization, and the sweep writes to it as the NOBYPASSRLS app role under a bound
tenant — so it joins `TENANT_TABLES` with the same `USING`/`WITH CHECK` policy the other business
tables carry (see the row-level-security migration for why `WITH CHECK` is not optional).

**Two indexes with opposite shapes, on purpose.** `ix_dunning_cycles_org_subscription` leads with
the tenant, like every other index in this service, because the RLS qual is ANDed into every
tenant-bound query. `ix_dunning_cycles_due` deliberately does **not**: it serves the sweep's
cross-tenant discovery query, which exists to find *which* tenants have work due and therefore has
no organization to bind while asking — the outbox relay's shape exactly, on the outbox relay's
role. Both are partial on `state = 'open'`, because a closed cycle is history and nothing scans it.

The partial unique index is the one that prevents a class of bug rather than a slow query: at most
one *open* cycle per subscription, so two failures cannot open two windows racing to suspend the
same customer on two schedules.

Revision ID: f3a7c1d90b42
Revises: e2f3a4b5c6d7
Create Date: 2026-08-25 09:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - parity with the generated migrations

revision: str = 'f3a7c1d90b42'  # pragma: allowlist secret
down_revision: str | None = 'e2f3a4b5c6d7'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "dunning_cycles"
APP_ROLE = "opsgrids_subscription"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('invoice_id', sa.UUID(), nullable=True),
        sa.Column('amount_minor', sa.BigInteger(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column(
            'opened_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('grace_ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_action_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts_made', sa.Integer(), nullable=False),
        sa.Column('failures_seen', sa.Integer(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_reason', sa.String(length=60), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state in ('open', 'recovered', 'exhausted', 'abandoned')",
            name=op.f('ck_dunning_cycles_state'),
        ),
        sa.CheckConstraint('attempts_made >= 0', name=op.f('ck_dunning_cycles_attempts_non_negative')),
        sa.CheckConstraint('failures_seen >= 1', name=op.f('ck_dunning_cycles_failures_seen_positive')),
        sa.CheckConstraint(
            'amount_minor is null or amount_minor >= 0',
            name=op.f('ck_dunning_cycles_amount_non_negative'),
        ),
        sa.CheckConstraint(
            'currency is null or length(currency) = 3', name=op.f('ck_dunning_cycles_currency_iso')
        ),
        sa.CheckConstraint(
            'grace_ends_at > opened_at', name=op.f('ck_dunning_cycles_window_is_forward')
        ),
        sa.ForeignKeyConstraint(
            ['subscription_id'],
            [f'{SCHEMA}.subscriptions.id'],
            name='fk_dunning_cycles_subscription_id',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_dunning_cycles')),
        schema=SCHEMA,
    )
    op.create_index(
        op.f('ix_subscription_dunning_cycles_organization_id'),
        TABLE,
        ['organization_id'],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        'ix_dunning_cycles_org_subscription',
        TABLE,
        ['organization_id', 'subscription_id'],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        'uq_dunning_cycles_open_per_subscription',
        TABLE,
        ['organization_id', 'subscription_id'],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_index(
        'ix_dunning_cycles_due',
        TABLE,
        ['next_action_at'],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'open'"),
    )

    # Policied with the rest of this service's business tables. Without the policy the sweep's
    # tenant binding would be decoration, and a bug that lost the binding would suspend across
    # tenants rather than fail.
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
    op.drop_index('ix_dunning_cycles_due', table_name=TABLE, schema=SCHEMA)
    op.drop_index('uq_dunning_cycles_open_per_subscription', table_name=TABLE, schema=SCHEMA)
    op.drop_index('ix_dunning_cycles_org_subscription', table_name=TABLE, schema=SCHEMA)
    op.drop_index(
        op.f('ix_subscription_dunning_cycles_organization_id'), table_name=TABLE, schema=SCHEMA
    )
    op.drop_table(TABLE, schema=SCHEMA)
