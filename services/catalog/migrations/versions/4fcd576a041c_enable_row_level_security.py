"""enable row level security

The same shape as product-service, and the reason it is worth repeating rather than sharing:
which tables are tenant-scoped is a security decision per service, and a shared helper that
guessed from column names would get the definitional tables wrong (ADR-0002 §5).

`plans`, `plan_versions`, `prices` and `features` are the platform's **vocabulary**, not a
customer's — the same plans and prices are offered to everybody, exactly like
`product.products`. They carry no `organization_id` and must **not** get a tenancy policy;
policying them would empty the catalogue for every customer and the failure would read as a
missing seed rather than a wrong policy. The one audience-restricted case, a bespoke version,
is an application filter on the nullable `restricted_to_org_id` pointer, defended by a
negative-authz test — not row-level security a global table cannot carry.

`processed_events` is exempt for the usual reason: no organization column, so any policy makes
every idempotency lookup return nothing and redelivered events get handled twice in silence.

Only `audit_logs` and `outbox` are tenant-scoped here. They record catalog's own platform
acts, stamped with the platform sentinel organization (`PLATFORM_ORG`), and the operator
session binds that same sentinel — so the policy holds without a real tenant ever being
involved.

Revision ID: 4fcd576a041c
Revises: 549e7957161c
Create Date: 2026-08-24 18:28:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '4fcd576a041c'  # pragma: allowlist secret
down_revision: str | None = '549e7957161c'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "catalog"
APP_ROLE = "atmora_catalog"

#: Tenant-scoped — the two platform tables that carry an organization column.
TENANT_TABLES = ("audit_logs", "outbox")

#: Global by design — see the module docstring. Listed so the choice is explicit and reviewable.
UNPOLICIED = ("plans", "plan_versions", "prices", "features", "processed_events")


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
