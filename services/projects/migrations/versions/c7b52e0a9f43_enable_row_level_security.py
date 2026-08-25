"""enable row level security

Tenant isolation stops being a convention every query has to remember and becomes something the
database enforces, exactly as it is on the other product schemas (ADR-0002).

Three things have to be true before this does anything, and all three are in place platform-wide:
policies exist (this file); the API connects as a **NOBYPASSRLS** role (`atmora_projects`), so it
cannot bypass row security the way a superuser would; and something sets the tenant GUC —
`get_tenant_session` binds every request transaction to the caller's organization as its first
statement.

`processed_events` is deliberately **not** policied — it has no organization column, so any policy
makes every idempotency lookup return nothing and a redelivered event is handled twice in silence.

Revision ID: c7b52e0a9f43
Revises: a3f1c9d24e17
Create Date: 2026-08-25 10:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7b52e0a9f43"  # pragma: allowlist secret
down_revision: str | None = "a3f1c9d24e17"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "projects"
APP_ROLE = "atmora_projects"

#: Tenant-scoped: every one carries `organization_id`.
TENANT_TABLES = (
    "members",
    "projects",
    "milestones",
    "tasks",
    "audit_logs",
    "outbox",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy
#: would silently defeat consumer idempotency (ADR-0002).
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    """Enable, and FORCE — so a table the app role somehow comes to own does not silently bypass
    its own policy, and so dropping BYPASSRLS from the admin role later still leaves it enforced."""
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')` matters: once a transaction that set the GUC locally has ended, the next
    # transaction on the same pooled connection reads it back as '' rather than NULL. Without
    # nullif, `''::uuid` raises; with it the predicate is false and the query returns nothing —
    # fail-quiet, with the application asserting loudly instead (ADR-0002 §4).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )

    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional: USING gates existing rows and is not applied to the new row
        # of an INSERT, so without it a tenant could plant a row in somebody else's organization.
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
