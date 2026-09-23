"""enable row level security

Tenant isolation stops being a convention every query has to remember and becomes something the
database enforces, exactly as it is on `attendance`, `employee` and `approval` (ADR-0002).

Three things had to be true before this could do anything, and all three are already in place
platform-wide: policies exist (this file); the API connects as a **NOBYPASSRLS** role
(`opsgrids_leave`), so it cannot bypass row security the way a superuser would; and something
sets the tenant GUC — `get_tenant_session` binds every request transaction to the caller's
organization as its first statement, and the consumer's `session_scope(organization_id=...)`
does the same per message.

`processed_events` is deliberately exempt: it has no organization column, so a policy on it
would silently defeat consumer idempotency (ADR-0002 §5).

Revision ID: d2a4f6b8c1e3
Revises: cca6e177f68a
Create Date: 2026-09-23 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'd2a4f6b8c1e3'  # pragma: allowlist secret
down_revision: str | None = 'cca6e177f68a'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "leave"
APP_ROLE = "opsgrids_leave"

#: Every one carries `organization_id`. `employee_directory` and `leave_settings` are
#: natural-composite-key reference tables (data model shape approval's `hierarchy_nodes` and
#: `approval_policies` use) — no surrogate `id`, but a real tenant column all the same.
TENANT_TABLES = (
    "leave_types",
    "leave_settings",
    "employee_directory",
    "leave_requests",
    "leave_ledger_entries",
    "audit_logs",
    "outbox",
)

#: Deliberately **not** policied: `processed_events` has no organization column.
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    """Enable, and FORCE — see `attendance`'s identical migration for why FORCE matters even
    though a NOBYPASSRLS non-owner role is already filtered by a plain ENABLE."""
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')` reads a GUC left over from an ended transaction on the same pooled
    # connection as an empty string rather than NULL — fail-quiet here, with the application
    # asserting loudly instead (ADR-0002 §4).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )

    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional — USING alone is not applied to a new row of an INSERT.
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
