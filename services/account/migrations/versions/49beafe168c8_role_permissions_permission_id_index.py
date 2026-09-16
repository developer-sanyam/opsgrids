"""role permissions permission id index

Revision ID: 49beafe168c8
Revises: 46478181a5ab
Create Date: 2026-09-16 05:52:39.375741

`role_permissions` carries one composite index — the unique constraint on
`(role_id, permission_id)` — which only serves a lookup that leads with `role_id`.
`selectors.count_memberships_missing_grants` (ADR-0007's `memberships_without`) joins the
other way: it filters `permissions.code` down to a handful of rows first, then needs every
`role_permissions` row matching those permission ids. `permission_id` is the *second* column
of that composite index, so the planner cannot descend it directly — it falls back to a
bitmap scan of the whole index (confirmed against the query plan) rather than a lookup.
Adding a single-column index on `permission_id` gives the planner the leading column it is
missing; `(role_id, permission_id)` still serves every query that leads with `role_id`, so
nothing about that index changes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = '49beafe168c8'
down_revision: str | None = '46478181a5ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_role_permissions_permission_id',
        'role_permissions',
        ['permission_id'],
        unique=False,
        schema='account',
    )


def downgrade() -> None:
    op.drop_index(
        'ix_role_permissions_permission_id',
        table_name='role_permissions',
        schema='account',
    )
