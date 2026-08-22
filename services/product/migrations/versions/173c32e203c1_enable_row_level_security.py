"""enable row level security

The same shape as account-service, and the reason it is worth repeating rather than sharing:
which tables are tenant-scoped is a security decision per service, and a shared helper that
guessed from column names would get `products` wrong.

`products` is the platform catalogue — global on purpose. Every organization reads all of it,
because a marketplace is a list of things you do not have yet. Policying it would leave the
marketplace empty and the failure would look like a missing seed rather than a wrong policy.

`processed_events` is exempt for the usual reason: it has no organization column, and any
policy makes every idempotency lookup return nothing, so redelivered events get handled twice
in silence.

Revision ID: 173c32e203c1
Revises: db18d2563087
Create Date: 2026-08-23 00:25:04.811595
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = '173c32e203c1'
down_revision: str | None = 'db18d2563087'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "product"
APP_ROLE = "atmora_product"

#: Tenant-scoped. `activations` is the row the gateway's 402 depends on.
TENANT_TABLES = ("activations", "audit_logs", "outbox")

#: Global by design — see the module docstring.
UNPOLICIED = ("products", "processed_events")


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
