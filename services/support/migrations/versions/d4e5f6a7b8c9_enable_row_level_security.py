"""enable row level security

Tenant isolation stops being a convention every query has to remember and becomes something the
database enforces, exactly as it is on the `account`, `product` and `employee` schemas
(ADR-0002).

Three things have to be true for this to do anything, and all three hold platform-wide: policies
exist (this file); the API connects as a **NOBYPASSRLS** role (`atmora_support`), so it cannot
bypass row security the way a superuser would; and something sets the tenant GUC —
`get_tenant_session` binds every request transaction to the caller's organization as its first
statement.

Classification is written out per table rather than looped over "every table with an
`organization_id` column". The only judgement is `processed_events`, which has no organization
column at all: any policy on it makes every idempotency lookup return nothing, so a redelivered
event would be handled twice in silence. It is exempt deliberately.

Revision ID: d4e5f6a7b8c9
Revises: 2c4c35b8522c
Create Date: 2026-08-25 03:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | None = "2c4c35b8522c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "support"
APP_ROLE = "atmora_support"

#: Tenant-scoped. Every one carries `organization_id`: the four domain tables plus the two
#: platform tables every service has.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "agents",
    "tickets",
    "ticket_messages",
    "sla_policies",
)

#: Deliberately **not** policied: `processed_events` has no organization column, so a policy
#: would silently defeat consumer idempotency (ADR-0002).
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    """Enable, and FORCE.

    `FORCE` is set for the two futures where it matters: a table created accidentally by the app
    role, which would then own it and silently bypass its own policy, and a later decision to
    drop `BYPASSRLS` from the admin role.
    """
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')` is not tidying. Once a transaction that set the GUC locally has ended,
    # the next transaction on the same pooled connection reads it back as an empty string rather
    # than NULL. Without `nullif`, `''::uuid` raises a cast error; with it the predicate is false
    # and the query returns nothing — fail-quiet by design, with the application asserting loudly
    # instead (ADR-0002 §4).
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
    """The deliberate undo. For an incident there are faster levers: `ALTER ROLE atmora_support
    BYPASSRLS` restores service in one statement and leaves the policies intact for diagnosis,
    and pointing the DSN at the admin role needs no database change at all. This one takes a
    brief ACCESS EXCLUSIVE lock per table and touches no rows."""
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")

    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.current_org()")
