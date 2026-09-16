"""enable row level security

The same shape as subscription-, employee- and product-service, repeated rather than shared
because which tables are tenant-scoped is a security decision per service (ADR-0002 §5, data
model §3.2). `hierarchy_nodes` belongs to exactly one organization, and so do the two platform
tables that carry an `organization_id`, `audit_logs` and `outbox` — all three are policied.
`processed_events` is exempt for the usual reason: it has no organization column, so any policy
would make every idempotency lookup return nothing and a redelivered event would be handled
twice in silence (ADR-0002 §5).

A separate migration from the one that creates the tables (data model §6's migration order: "3 —
drop policies, NO FORCE, DISABLE, drop current_org(). Touches no rows. Drop order matters: the
function must go after the policies that call it") — so this migration's own downgrade can be
exact without touching a single row, and dropping `current_org()` never has to race a `DROP
TABLE` in the same rollback.

Revision ID: 6f3f94f4916a
Revises: 3d96b212cc73
Create Date: 2026-09-16 05:21:38.424854
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '6f3f94f4916a'
down_revision: str | None = '3d96b212cc73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "approval"
APP_ROLE = "opsgrids_approval"

#: Tenant-scoped. `hierarchy_nodes` plus the two platform tables this slice's changes land in.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "hierarchy_nodes",
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
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not applied
        # to the new row of an INSERT, so without it a tenant could insert a row belonging to
        # somebody else — invisible to them, fully visible to the victim (data model §3.2).
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
