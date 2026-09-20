"""enable row level security

The same shape as approval-, subscription-, employee- and product-service, repeated rather than
shared because which tables are tenant-scoped is a security decision per service (ADR-0002 §5).
`assets` belongs to exactly one organization, and so do the two platform tables that carry an
`organization_id`, `audit_logs` and `outbox` — all three are policied.

`processed_events` is exempt for the usual reason: it has no organization column, so any policy
would make every idempotency lookup return nothing and a redelivered event would be handled
twice in silence (ADR-0002 §5).

`ENABLE` **and** `FORCE`, `USING` **and** `WITH CHECK`, written out explicitly. `WITH CHECK` is
not optional: `USING` is evaluated against existing rows and is not applied to the new row of an
INSERT, so without it one tenant could insert a row belonging to somebody else — invisible to
them, fully visible to the victim.

**What this does and does not protect, for this feature specifically.** RLS is what stops one
tenant reading, updating or deleting another tenant's asset *rows*. It knows nothing about
buckets, so it cannot stop anything at the object layer — which is exactly why no route in this
service accepts an object key from a caller (ADR-0009 §3). Reading another tenant's *object* is
not prevented and is not a violation: every object in this bucket is world-readable by design,
so "tenant A fetched tenant B's image" is the same event as "a member of the public fetched it".

A separate migration from the one that creates the table, so this downgrade is exact without
touching a single row, and dropping `current_org()` never races a `DROP TABLE` in the same
rollback.

Revision ID: b4a1f7c92d5e
Revises: e266c48a08c6
Create Date: 2026-09-19 03:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b4a1f7c92d5e'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = 'e266c48a08c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "document"
APP_ROLE = "opsgrids_document"

#: Tenant-scoped: the one domain table plus the two platform tables that carry an org column.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "assets",
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
