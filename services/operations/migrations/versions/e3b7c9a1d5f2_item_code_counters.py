"""item_code_counters: the per-organization counter behind auto-generated item codes

Additive only: one new table, nothing existing is touched, so old code keeps running against the
new schema and the new code, finding no counter row, creates one on first use. The counter starts
at zero for every organization; the generator skips any `ITM-<n>` code already taken, so existing
items need no backfill. ENABLE + FORCE RLS and the standard `tenant_isolation` policy, as every
migration since `c3f1a7e5b2d9`.

Revision ID: e3b7c9a1d5f2
Revises: d4e1a7c3b952
Create Date: 2026-10-14 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c9a1d5f2"  # pragma: allowlist secret
down_revision: str | None = "d4e1a7c3b952"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
APP_ROLE = "opsgrids_operations"
TABLE = "item_code_counters"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("series", sa.String(length=20), nullable=False),
        sa.Column("last_value", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("last_value >= 0", name=op.f(f"ck_{TABLE}_last_value_not_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_{TABLE}_organization_id"),
        TABLE,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_item_code_counters_org_series",
        TABLE,
        ["organization_id", "series"],
        unique=True,
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{TABLE}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.drop_table(TABLE, schema=SCHEMA)
