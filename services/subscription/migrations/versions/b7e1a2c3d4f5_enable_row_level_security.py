"""enable row level security

The same shape as employee- and product-service, repeated rather than shared because which tables
are tenant-scoped is a security decision per service (ADR-0002 §5). Here every business table is a
customer's: a subscription, its items and its transitions all belong to exactly one organization —
unlike catalog, whose definitional tables are the platform's vocabulary and stay un-policied.

So all three business tables are policied, alongside the two platform tables that carry an
`organization_id` (`audit_logs`, `outbox`). `processed_events` is exempt for the usual reason: it
has no organization column, so any policy would make every idempotency lookup return nothing and a
redelivered event would be handled twice in silence.

This is the net the projection downstream in product-service leans on from the other side: both
services connect as NOBYPASSRLS roles, so a write only lands when the transaction is bound to its
tenant — here on the customer chokepoint and the internal suspend handler, there on the consumer's
per-message `session_scope`.

Revision ID: b7e1a2c3d4f5
Revises: c40e74592ec8
Create Date: 2026-08-24 20:56:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b7e1a2c3d4f5'  # pragma: allowlist secret
down_revision: str | None = 'c40e74592ec8'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "subscription"
APP_ROLE = "opsgrids_subscription"

#: Tenant-scoped. Every one carries `organization_id`: the three business tables plus the two
#: platform tables that record this service's own changes and events.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "subscriptions",
    "subscription_items",
    "subscription_transitions",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy would
#: silently defeat consumer idempotency (ADR-0002).
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )
    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not applied
        # to the new row of an INSERT, so without it a tenant could insert a row belonging to
        # somebody else — invisible to them, fully visible to the victim.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.current_org()")
