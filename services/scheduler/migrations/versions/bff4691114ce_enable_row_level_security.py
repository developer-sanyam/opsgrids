"""enable row level security

Same shape as subscription- and approval-service's own RLS migrations (ADR-0002 §5), repeated
rather than shared because which tables are tenant-scoped is a per-service decision. Every
business table here belongs to exactly one organization — `scheduled_jobs`, plus the two platform
tables that carry `organization_id` (`audit_logs`, `outbox`). `processed_events` is exempt for the
usual reason: it has no organization column, and a policy on it would make every idempotency
lookup return nothing.

Revision ID: bff4691114ce
Revises: 13ef09d48a2b
Create Date: 2026-09-23 09:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'bff4691114ce'  # pragma: allowlist secret
down_revision: str | None = '13ef09d48a2b'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "scheduler"
APP_ROLE = "opsgrids_scheduler"

TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "scheduled_jobs",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy
#: would silently defeat consumer idempotency (ADR-0002).
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
        # WITH CHECK is not optional: USING is not applied to the new row of an INSERT, and
        # without it a tenant could insert a row belonging to somebody else.
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
