"""case insensitive company name lookup

Revision ID: e230c025337e
Revises: c74f180ecf16
Create Date: 2026-09-23 22:53:16.078290

A unique index on `lower(name)` was the first attempt here, and it was wrong: it fails outright
on `alembic upgrade` for any organization that already holds two companies whose names differ
only by case ("Acme Ltd" and "ACME LTD") -- a real possibility the *case-sensitive* constraint
this migration originally replaced has silently allowed for as long as it existed, and there is
no data to rewrite here without a decision about which of the two rows is the "real" one, made
by someone who can actually see them.

So this migration does less than it first tried to: the original `uq_companies_org_name` stays
exactly as it was (case-sensitive, unique, live rows only) -- untouched, not dropped and
recreated -- and this only *adds* a plain, non-unique index on
`(organization_id, lower(btrim(name)))` for the lookup `get_company_by_name` needs to find
"Acme Ltd" when asked about "acme ltd". That is the exact expression the lookup compares, on
both sides: stored names are never trimmed on the way in, so the lookup has to trim them.
Refusing a *new* case-insensitive duplicate going forward is `services.create_company`'s job now
(a `pg_advisory_xact_lock` keyed on the organization and the lowercased name), not this index's.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e230c025337e'  # pragma: allowlist secret
down_revision: str | None = 'c74f180ecf16'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"


def upgrade() -> None:
    op.create_index(
        "ix_companies_org_name_ci",
        "companies",
        ["organization_id", sa.literal_column("lower(btrim(name))")],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_companies_org_name_ci",
        table_name="companies",
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
