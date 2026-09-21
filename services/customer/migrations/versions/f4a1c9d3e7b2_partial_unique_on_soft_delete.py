"""partial unique on soft delete

`uq_companies_org_name` and `uq_contacts_org_email` are **full** unique constraints — they
compare every row, deleted or not. Now that companies and contacts have a delete route
(soft only: `deleted_at` is set, the row stays), that is a bug: a caller deletes "Acme
Inc." and tries to re-add it, the service's own duplicate check passes (it already filters
`deleted_at`), and the insert then dies on the constraint anyway, surfacing as a generic
409 with no useful detail (`libs/platform-core/src/platform_core/errors.py:170-175`).

The fix is the same shape account-service's role table already uses
(`services/account/migrations/versions/5250ce5920bb_initial_account_schema.py:185-186`):
drop the full constraint and replace it with a **partial** unique index, `WHERE deleted_at
IS NULL`. A live row still collides with a live row; a deleted row no longer blocks the
name or email it once held.

`uq_owners_org_email` is untouched — this slice does not add an owner delete route, so
there is nothing yet that could produce a soft-deleted owner whose email needs freeing.

Revision ID: f4a1c9d3e7b2
Revises: b5c81e02a7d9
Create Date: 2026-09-21 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a1c9d3e7b2"  # pragma: allowlist secret
down_revision: str | None = "b5c81e02a7d9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"

_LIVE_ONLY = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    op.drop_constraint("uq_companies_org_name", "companies", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_companies_org_name",
        "companies",
        ["organization_id", "name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=_LIVE_ONLY,
    )

    op.drop_constraint("uq_contacts_org_email", "contacts", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_contacts_org_email",
        "contacts",
        ["organization_id", "email"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=_LIVE_ONLY,
    )


def downgrade() -> None:
    op.drop_index("uq_contacts_org_email", "contacts", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_contacts_org_email", "contacts", ["organization_id", "email"], schema=SCHEMA
    )

    op.drop_index("uq_companies_org_name", "companies", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_companies_org_name", "companies", ["organization_id", "name"], schema=SCHEMA
    )
