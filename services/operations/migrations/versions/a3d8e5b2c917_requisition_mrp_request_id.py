"""store the MRP request id on the requisition instead of in its free-text reason

`requisition_for_request` decided "already made" by searching `purchase_requisitions.reason` for
`MRP request <uuid>`. The reason is user-editable, and the request id is returned to the caller
before the consumer runs, so a hand-typed reason could make the consumer drop the planner's
request. The id now lives in `mrp_request_id`, set only by the consumer and unique per
organization.

The unique index is built `CONCURRENTLY` in an autocommit block (a plain build would block
requisition writes) and is idempotent, so a failure part-way is resumed by re-running
`alembic upgrade head`. No backfill: MRP requisitions only exist from this release on.

Revision ID: a3d8e5b2c917
Revises: f2c7a9d4e816
Create Date: 2026-10-11 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3d8e5b2c917"  # pragma: allowlist secret
down_revision: str | None = "f2c7a9d4e816"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
INDEX = "uq_purchase_requisitions_org_mrp_request"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "purchase_requisitions",
        sa.Column("mrp_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    with op.get_context().autocommit_block():
        invalid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = :name AND NOT i.indisvalid"
                ),
                {"schema": SCHEMA, "name": INDEX},
            )
            .scalar_one()
        )
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.{INDEX}")
        op.create_index(
            INDEX,
            "purchase_requisitions",
            ["organization_id", "mrp_request_id"],
            unique=True,
            schema=SCHEMA,
            postgresql_where=sa.text("mrp_request_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{INDEX}")
    op.drop_column("purchase_requisitions", "mrp_request_id", schema=SCHEMA)
