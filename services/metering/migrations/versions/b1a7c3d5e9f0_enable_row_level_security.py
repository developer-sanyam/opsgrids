"""enable row level security

Tenant isolation stops being a convention every query has to remember and becomes something the
database enforces, exactly as it is on the `account`, `product`, `employee` and `catalog`
schemas. ADR-0002.

Classification is written out per table rather than looped over "every table with an
`organization_id` column", because which tables are tenant-scoped is a security decision (ADR-0002
§5):

* **`usage_events`, `usage_rollups`, `audit_logs`, `outbox`** are tenant-scoped and get the
  tenant-isolation policy. `usage_events` is the append-only sink; `usage_rollups` is its
  rebuildable aggregate; both carry `organization_id` and must never serve one tenant's rows to
  another.
* **`meters`** is **global / UNPOLICIED reference data** — the platform's meter vocabulary, read
  by every tenant and authored by the platform, exactly like `product.products` and
  `catalog.plans`. It carries no `organization_id`; a tenancy policy on it would empty the meter
  catalogue for every customer, and the failure would read as a missing seed rather than a wrong
  policy (ADR-0002 §5, "platform vocabulary must NOT get an org policy").
* **`processed_events`** is exempt for the usual reason: no organization column, so any policy
  makes every idempotency lookup return nothing and redelivered events get handled twice in
  silence.

The append-only guarantee on `usage_events` is a *separate* control, in its own migration
(`REVOKE UPDATE, DELETE`): row-level security decides which rows you may touch, table privileges
decide which verbs you may use. Append-only is a verb question, so it is table privileges, the
same tool `audit_logs` uses.

Revision ID: b1a7c3d5e9f0
Revises: a82206a2d43e
Create Date: 2026-08-24 21:02:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b1a7c3d5e9f0'  # pragma: allowlist secret
down_revision: str | None = 'a82206a2d43e'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "metering"
APP_ROLE = "opsgrids_metering"

#: Tenant-scoped. The append-only sink, its rebuildable aggregate, and the two platform tables.
TENANT_TABLES = ("audit_logs", "outbox", "usage_events", "usage_rollups")

#: Global / reference, deliberately **not** policied — see the module docstring. `meters` is
#: platform vocabulary; `processed_events` has no organization column.
UNPOLICIED = ("meters", "processed_events")


def _enable(table: str) -> None:
    """Enable, and FORCE.

    `FORCE` is not what makes this work — a NOBYPASSRLS role that does not own the table is
    filtered by a plain `ENABLE`. It is set for the two futures where it matters: a table created
    accidentally by the app role, which would then own it and silently bypass its own policy, and
    a later decision to drop `BYPASSRLS` from the admin role.
    """
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')` is not tidying. Once a transaction that set the GUC locally has ended, the
    # next transaction on the same session reads it back as an empty string rather than NULL.
    # Without `nullif`, `''::uuid` raises a cast error; with it the predicate is false and the
    # query returns nothing. Fail-quiet is the deliberate choice, with the application asserting
    # loudly instead (ADR-0002 §4).
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
        # somebody else — invisible to them, fully visible to the victim. For an append-only sink
        # this matters twice: the only write is an INSERT, so WITH CHECK is the policy clause that
        # actually guards ingest.
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
