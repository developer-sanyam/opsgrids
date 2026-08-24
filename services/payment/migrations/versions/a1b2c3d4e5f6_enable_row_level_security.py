"""enable row level security

The same shape as employee-, product-, subscription- and billing-service, repeated rather than
shared because which tables are tenant-scoped is a security decision per service (ADR-0002 §5).
Every business table here is a customer's: a mandate, a charge, a refund and every webhook the
provider sent about them all belong to exactly one organization — unlike catalog's global
definitional tables.

So all four business tables are policied, alongside the two platform tables that carry an
`organization_id` (`audit_logs`, `outbox`). `processed_events` is exempt for the usual reason: it
has no organization column, so any policy would make every idempotency lookup return nothing and a
redelivered event would be handled twice in silence.

This is the net the whole service leans on: payment connects as `atmora_payment` (NOBYPASSRLS), so
a write only lands when the transaction is bound to its tenant — on the internal charge route
(organization from the body) and on the webhook (organization from the provider's signed `notes`).

Revision ID: a1b2c3d4e5f6
Revises: 25c38fe09f08
Create Date: 2026-08-24 22:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'a1b2c3d4e5f6'  # pragma: allowlist secret
down_revision: str | None = '25c38fe09f08'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
APP_ROLE = "atmora_payment"

#: Tenant-scoped. Every one carries `organization_id`: the four business tables plus the two
#: platform tables that record this service's own changes and events.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "mandates",
    "charges",
    "refunds",
    "provider_events",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy would
#: silently defeat consumer idempotency (ADR-0002).
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # nullif(..., '') because a transaction-local GUC reads back as '' once its setting transaction
    # has ended; without it '' ::uuid raises, with it the predicate is simply false and the query
    # returns nothing — fail-quiet, with the application asserting loudly instead (ADR-0002 §4).
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
