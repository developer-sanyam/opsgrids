"""append-only leave_ledger_entries at the database

A balance is the sum of every posting ever made (CLAUDE.md's append-only rule) — a correction is
always a new, compensating row, never an edit. Row-level security decides *which rows* the app
role may touch; it does not stop the role using UPDATE or DELETE on rows it can see. Append-only
is a *verb* question, so the right tool is a table privilege, not a policy — the same tool
`audit_logs` and `metering.usage_events` use (`platform_core.db.apply_schema_grants`).

This migration revokes UPDATE and DELETE on `leave.leave_ledger_entries` from `opsgrids_leave`.
INSERT and SELECT remain: the ledger is written once per posting and read many times.

**One interaction worth stating plainly**, transcribed from `metering`'s identical migration:
`migrations/env.py` runs `apply_schema_grants` after every migration, and that helper re-grants
UPDATE/DELETE on *all* tables (then re-revokes only `audit_logs`). A revoke that lived only in
this migration would be undone on the very next `alembic upgrade`. The durable enforcement
therefore also lives in this service's `env.py`, which re-applies exactly this revoke right after
`apply_schema_grants`. This migration keeps the revoke explicit and reversible in the schema
history; `env.py` is what makes it hold across grant re-application.

Revision ID: e5b7c9d1a2f4
Revises: d2a4f6b8c1e3
Create Date: 2026-09-23 18:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'e5b7c9d1a2f4'  # pragma: allowlist secret
down_revision: str | None = 'd2a4f6b8c1e3'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "leave"
APP_ROLE = "opsgrids_leave"
TABLE = "leave_ledger_entries"


def upgrade() -> None:
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{TABLE} FROM "{APP_ROLE}"')


def downgrade() -> None:
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.{TABLE} TO "{APP_ROLE}"')
