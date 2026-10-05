"""idempotency keys for staff actions

`console.idempotency_keys` — the claim row a staff action writes *before* it calls the owning
service (claim → call → record, docs/io-outside-transactions.md). Console's earlier
`Idempotency-Key` was optional, lookup-only, written after the downstream call and backed by no
unique index; this is the table that makes a required key real: the unique
`(organization_id, key)` means exactly one of two concurrent requests wins the insert and makes the
call, and the row outlives the call so a retry replays the recorded result.

Tenant-scoped, so it gets the same forced tenant-isolation policy every other console table has
(ADR-0002; `c1d0e2f4a6b8` created `console.current_org()`): the claim is written and read under the
app role bound to the **target** organization, never by the cross-tenant directory reader, so it is
deliberately not among the directory reader's tables. Not append-only — a claim moves
`in_progress` -> `succeeded` / `refused` / `unknown` — so it keeps the blanket DML grant.

Revision ID: e5a9c3b7d1f2
Revises: c3bc1da7c0bc
Create Date: 2026-10-06 00:42:13.423617
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5a9c3b7d1f2'  # pragma: allowlist secret
down_revision: str | None = 'c3bc1da7c0bc'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "console"
APP_ROLE = "opsgrids_console"
TABLE = "idempotency_keys"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint(
            "organization_id", "key", name="uq_idempotency_keys_organization_id_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_console_idempotency_keys_organization_id"),
        TABLE,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )

    # ENABLE and FORCE, then the policy — the same three statements, in the same order, as every
    # other console table (`c1d0e2f4a6b8`). WITH CHECK is not optional: without it a claim could
    # be written for an organization other than the one the request is bound to.
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
    op.drop_index(
        op.f("ix_console_idempotency_keys_organization_id"), table_name=TABLE, schema=SCHEMA
    )
    op.drop_table(TABLE, schema=SCHEMA)
