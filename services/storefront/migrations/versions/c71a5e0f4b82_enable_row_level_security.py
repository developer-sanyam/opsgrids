"""enable row level security

The same shape as document-, approval-, subscription-, employee- and product-service, repeated
rather than shared because which tables are tenant-scoped is a security decision per service
(ADR-0002 §5). All four domain tables belong to exactly one organization, and so do the two
platform tables that carry an `organization_id` — `audit_logs` and `outbox`.

`processed_events` is exempt for the usual reason: it has no organization column, so any policy
would make every idempotency lookup return nothing and a redelivered event would be handled
twice in silence (ADR-0002 §5).

`ENABLE` **and** `FORCE`, `USING` **and** `WITH CHECK`, written out explicitly. `WITH CHECK` is
not optional: `USING` is evaluated against existing rows and is not applied to the new row of an
INSERT, so without it one tenant could insert a row belonging to somebody else — invisible to
them, fully visible to the victim. That matters more here than anywhere else in the platform,
because one of these tables is written by an **unauthenticated** request: the demand counter is
inserted by a stranger's serviceability check, inside `session_scope(organization_id=…)`, and
`WITH CHECK` is what makes the tenant on that row a fact rather than a convention.

**What this protects, and the one thing it deliberately does not.** RLS is what stops one tenant
reading or writing another's zones, postal codes, storefront row and demand counters — including
on the anonymous path, which runs on the ordinary NOBYPASSRLS application role exactly like an
authenticated one. What it cannot protect is the slug lookup that happens *before* a tenant
exists to bind: that runs on `opsgrids_storefront_directory`, which bypasses every policy here by
design, holds SELECT on `storefronts` and nothing else, and is granted in `migrations/env.py`.
Confining that role to one table is what keeps the bypass auditable; the alternative considered
and rejected was un-policying `storefronts`, which would have let the *whole* application role
read every tenant's store row.

A separate migration from the one that creates the tables, so this downgrade is exact without
touching a single row, and dropping `current_org()` never races a `DROP TABLE` in the same
rollback.

Revision ID: c71a5e0f4b82
Revises: 8873b3d3c312
Create Date: 2026-09-19 04:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c71a5e0f4b82'
down_revision: str | None = '8873b3d3c312'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "storefront"
APP_ROLE = "opsgrids_storefront"

#: Tenant-scoped: the four domain tables plus the two platform tables that carry an org column.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "storefronts",
    "delivery_zones",
    "zone_postal_codes",
    "serviceability_demand",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy
#: would silently defeat consumer idempotency (ADR-0002 §5).
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
