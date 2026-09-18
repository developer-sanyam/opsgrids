"""append-only usage_events at the database

`usage_events` is the one thing this platform cannot reconstruct — a meter reading not taken is
gone — so it is never mutated: a correction is a new, compensating event (ADR-0000 §9, ADR-0004
§7). Row-level security decides *which rows* the app role may touch; it does not stop the role
using UPDATE or DELETE on rows it can see. Append-only is a *verb* question, so the right tool is
a table privilege, not a policy — the same tool `audit_logs` uses (platform_core `apply_schema_grants`).

This migration revokes UPDATE and DELETE on `metering.usage_events` from `opsgrids_metering`, so no
code path this service's role can reach may rewrite or erase a recorded reading. INSERT and SELECT
remain: the sink is written once and read many times.

**One interaction worth stating plainly.** `migrations/env.py` runs `apply_schema_grants` after
every migration, and that helper re-grants UPDATE/DELETE on *all* tables (then re-revokes only
`audit_logs`). So a revoke that lived only in this migration would be undone on the very next
`alembic upgrade`. The durable enforcement therefore also lives in this service's `env.py`, which
re-applies exactly this revoke right after `apply_schema_grants` — the same shape the helper uses
to keep `audit_logs` append-only. This migration keeps the revoke explicit and reversible in the
schema history; `env.py` is what makes it hold across grant re-application.

Revision ID: c2f4a6b8d0e1
Revises: b1a7c3d5e9f0
Create Date: 2026-08-24 21:08:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c2f4a6b8d0e1'  # pragma: allowlist secret
down_revision: str | None = 'b1a7c3d5e9f0'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "metering"
APP_ROLE = "opsgrids_metering"
TABLE = "usage_events"


def upgrade() -> None:
    op.execute(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{TABLE} FROM "{APP_ROLE}"')


def downgrade() -> None:
    # The deliberate undo: hand UPDATE/DELETE back, matching the blanket grant every other table
    # holds. Reverses this migration cleanly for `migration-roundtrip`.
    op.execute(f'GRANT UPDATE, DELETE ON {SCHEMA}.{TABLE} TO "{APP_ROLE}"')
