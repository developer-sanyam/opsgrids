"""enable row level security on the routing engine tables

The same shape as `6f3f94f4916a` (slice 1's `hierarchy_nodes`): `current_org()` already exists,
so this migration only adds the `tenant_isolation` policy — `USING` and `WITH CHECK` — to each of
the four tables the previous migration created (ADR-0006 §3, data model §3.2). A separate
migration from the one that creates the tables, so this one's own downgrade is exact without
touching a row, and dropping a policy never has to race a `DROP TABLE` in the same rollback.

`approval_policies`, `approval_requests`, `approval_steps` and `delegations` are all tenant-owned
— every row belongs to exactly one organization — so all four are policied. There is nothing here
un-policied the way `processed_events` is; that table's migration already exists and is untouched.

Revision ID: b1c4d7e29f3a
Revises: 0a7f41108309
Create Date: 2026-09-16 11:32:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b1c4d7e29f3a'
down_revision: str | None = '0a7f41108309'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = "approval"
APP_ROLE = "opsgrids_approval"

TENANT_TABLES = (
    "approval_policies",
    "approval_requests",
    "approval_steps",
    "delegations",
)


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional — see `6f3f94f4916a`'s identical note: USING alone does not
        # apply to the new row of an INSERT, so without it a tenant could insert a row belonging
        # to somebody else, invisible to them and fully visible to the victim.
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
