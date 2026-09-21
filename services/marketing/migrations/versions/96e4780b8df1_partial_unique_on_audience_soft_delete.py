"""partial unique on audience soft delete

`uq_audiences_org_name` is a **full** unique constraint — it compares every row, deleted or
not. Now that audiences get a delete route (soft only: `deleted_at` is set, the row stays),
that is a bug: a caller deletes "Growth-stage SaaS" and tries to re-add it, the service's own
duplicate check passes (`selectors.get_audience_by_name` already filters `deleted_at`), and the
insert then dies on the constraint anyway, surfacing as a generic 409 with no useful detail.

Same fix as account-service's `uq_users_email`
(`services/account/app/models.py:250`) and customer-service's companies/contacts
(`services/customer/migrations/versions/f4a1c9d3e7b2_partial_unique_on_soft_delete.py`): drop the
full constraint and replace it with a **partial** unique index, `WHERE deleted_at IS NULL`. A
live audience still collides with a live audience; a deleted one no longer blocks the name it
once held.

Revision ID: 96e4780b8df1
Revises: f6b63708a2e2
Create Date: 2026-09-21 20:41:38.995444
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "96e4780b8df1"  # pragma: allowlist secret
down_revision: str | None = "f6b63708a2e2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "marketing"

_LIVE_ONLY = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    op.drop_constraint("uq_audiences_org_name", "audiences", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_audiences_org_name",
        "audiences",
        ["organization_id", "name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=_LIVE_ONLY,
    )


def downgrade() -> None:
    op.drop_index("uq_audiences_org_name", "audiences", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_audiences_org_name", "audiences", ["organization_id", "name"], schema=SCHEMA
    )
