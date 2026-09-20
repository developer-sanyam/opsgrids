"""regularization request index tuning

Review findings against `cc394baeaaac` (already applied, so fixed forward here rather than
edited in place):

* **The read path had no index.** `selectors.get_latest_regularization` /
  `get_latest_regularizations` filter on `(organization_id, attendance_id[, created_at desc])`,
  but the only two indexes on this table are keyed by `employee_id` and by the pending-only
  partial predicate — neither serves that query. Measured 183 buffers / 4.3ms for a 25-row page
  at 10,001 rows: correct at that size, but this table only grows (every correction ever
  requested, never purged), so the same query degrades roughly 30x by ~300k rows. Adds
  `ix_regularization_requests_org_attendance_created`.

* **A redundant single-column index.** `organization_id` carried the `OrgScopedMixin` default
  `index=True`, which is right for a table with no other tenant-leading index — this one has
  three. `approval`'s own schema deliberately omits the same default for this reason (data model
  §1.0); attendance follows suit here. Drops `ix_attendance_regularization_requests_organization_id`.

* **The partial unique index and the selector disagreed on soft deletes.**
  `selectors.get_pending_regularization` filters `deleted_at is null`, so a soft-deleted pending
  row is invisible to the friendly "already pending" check — but
  `uq_regularization_requests_pending_per_attendance` did not carry the same predicate, so that
  same soft-deleted row would still collide on insert, turning the intended 409 into a raw
  `23505` the caller never expects. Every other query in `selectors.py` treats soft-deleted rows
  as gone; the index is brought into line with that convention rather than the other way around,
  so a soft-deleted pending request no longer blocks a legitimate new one.

Revision ID: 41d1cad3e5dc
Revises: cc394baeaaac
Create Date: 2026-09-16 12:15:57.528554
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = '41d1cad3e5dc'
down_revision: str | None = 'cc394baeaaac'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"
TABLE = "regularization_requests"


def upgrade() -> None:
    op.drop_index(
        op.f('ix_attendance_regularization_requests_organization_id'),
        table_name=TABLE,
        schema=SCHEMA,
    )
    op.create_index(
        'ix_regularization_requests_org_attendance_created',
        TABLE,
        ['organization_id', 'attendance_id', sa.literal_column('created_at DESC')],
        unique=False,
        schema=SCHEMA,
    )
    # The partial unique index is dropped and recreated rather than altered in place — Postgres
    # has no `ALTER INDEX ... WHERE`, and this is a small, low-traffic table, so a brief absence
    # of the constraint is an acceptable trade against a `CREATE INDEX CONCURRENTLY` two-step.
    op.drop_index(
        'uq_regularization_requests_pending_per_attendance',
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        'uq_regularization_requests_pending_per_attendance',
        TABLE,
        ['organization_id', 'attendance_id'],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending' and deleted_at is null"),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_regularization_requests_pending_per_attendance',
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending' and deleted_at is null"),
    )
    op.create_index(
        'uq_regularization_requests_pending_per_attendance',
        TABLE,
        ['organization_id', 'attendance_id'],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        'ix_regularization_requests_org_attendance_created',
        table_name=TABLE,
        schema=SCHEMA,
    )
    op.create_index(
        op.f('ix_attendance_regularization_requests_organization_id'),
        TABLE,
        ['organization_id'],
        unique=False,
        schema=SCHEMA,
    )
