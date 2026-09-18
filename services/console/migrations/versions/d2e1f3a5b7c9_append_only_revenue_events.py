"""append-only revenue_events at the database

`revenue_events` is the time series MRR, churn and trial-conversion are computed from (ADR-0004 §4),
so it is never mutated: a correction is a new, compensating row (ADR-0000 §9). Row-level security
decides *which rows* the app role may touch; it does not stop the role using UPDATE or DELETE on
rows it can see. Append-only is a *verb* question, so the right tool is a table privilege, not a
policy — the same tool `audit_logs` and metering's `usage_events` use.

This migration revokes UPDATE and DELETE on `console.revenue_events` from `opsgrids_console`, so no
code path this service's role can reach may rewrite or erase a folded transition. INSERT and SELECT
remain: the log is written once and read many times.

**One interaction worth stating plainly.** `migrations/env.py` runs `apply_schema_grants` after every
migration, and that helper re-grants UPDATE/DELETE on *all* tables (then re-revokes only
`audit_logs`). So a revoke that lived only in this migration would be undone on the very next
`alembic upgrade`. The durable enforcement therefore also lives in this service's `env.py`
(`reapply_append_only`), which re-applies exactly this revoke right after `apply_schema_grants`. This
migration keeps the revoke explicit and reversible in the schema history; `env.py` is what makes it
hold across grant re-application.

Revision ID: d2e1f3a5b7c9
Revises: c1d0e2f4a6b8
Create Date: 2026-08-24 22:24:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'd2e1f3a5b7c9'  # pragma: allowlist secret
down_revision: str | None = 'c1d0e2f4a6b8'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "console"
APP_ROLE = "opsgrids_console"
TABLE = "revenue_events"


def upgrade() -> None:
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{TABLE} FROM "{APP_ROLE}"')


def downgrade() -> None:
    # The deliberate undo: hand UPDATE/DELETE back, matching the blanket grant every other table
    # holds. Reverses this migration cleanly for `migration-roundtrip`.
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.{TABLE} TO "{APP_ROLE}"')
