"""enable row level security

Tenant isolation on console's projections stops being a convention every query remembers and
becomes something the database enforces, exactly as it is on `account`, `product`, `catalog` and
`metering`. ADR-0002.

Classification is written out per table rather than looped over "every table with an
`organization_id` column", because which tables are tenant-scoped is a security decision (ADR-0002
§5):

* **`org_commercial_state`, `revenue_events`, `usage_summary`, `audit_logs`, `outbox`** are
  tenant-scoped and get the tenant-isolation policy. The three projections are the operator read
  model, written one organization at a time by the consumer with the tenant GUC set per message —
  the policy is exactly what makes an org-A fold unable to write org-B's row, and what makes the
  cross-tenant operator read a *deliberate* BYPASSRLS bypass rather than the app role seeing
  everything anyway.
* **`processed_events`** is exempt for the usual reason: no organization column, so any policy makes
  every idempotency lookup return nothing and redelivered events get folded twice in silence.

The append-only guarantee on `revenue_events` is a *separate* control, in its own migration
(`REVOKE UPDATE, DELETE`): row-level security decides which rows you may touch, table privileges
decide which verbs you may use. Append-only is a verb question, so it is table privileges — the same
tool `audit_logs` and metering's `usage_events` use.

Revision ID: c1d0e2f4a6b8
Revises: b1c26d81e82e
Create Date: 2026-08-24 22:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c1d0e2f4a6b8'  # pragma: allowlist secret
down_revision: str | None = 'b1c26d81e82e'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "console"
APP_ROLE = "opsgrids_console"

#: Tenant-scoped: the three projections plus the two platform tables.
TENANT_TABLES = (
    "org_commercial_state",
    "revenue_events",
    "usage_summary",
    "audit_logs",
    "outbox",
)

#: Global / no organization column, deliberately **not** policied — `processed_events` has no
#: organization column, so a policy would break idempotency.
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    """Enable, and FORCE.

    `FORCE` is not what makes this work — a NOBYPASSRLS role that does not own the table is filtered
    by a plain `ENABLE`. It is set for the two futures where it matters: a table created accidentally
    by the app role, which would then own it and silently bypass its own policy, and a later decision
    to drop `BYPASSRLS` from the admin role.
    """
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')` is not tidying. Once a transaction that set the GUC locally has ended, the
    # next transaction on the same session reads it back as an empty string rather than NULL. Without
    # `nullif`, `''::uuid` raises a cast error; with it the predicate is false and the query returns
    # nothing. Fail-quiet is the deliberate choice, with the application asserting loudly instead
    # (ADR-0002 §4).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )

    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not applied to
        # the new row of an INSERT, so without it the consumer could write a projection row belonging
        # to another organization — invisible to that tenant, and wrong in every metric.
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
