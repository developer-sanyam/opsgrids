"""purge sweep discovery index

ADR-0009 §12's index-timing register named this one for "slice 2, with the cron" and it never
landed alongside the table (`e266c48a08c6`) — this is that slice.

`(deleted_at) WHERE deleted_at IS NOT NULL AND purged_at IS NULL`, deliberately with **no**
`organization_id` leading column: `app/selectors.py`'s `due_purge_assets` is the purge sweep's
own cross-tenant discovery query, run on the BYPASSRLS outbox role precisely because it has no
tenant to bind while asking which organizations have assets due for purging. An index led by
`organization_id` would not serve a query that never filters on it; this one exists to make
`deleted_at <= :cutoff` over every soft-deleted, not-yet-purged row across every tenant a
lookup instead of the scan it would otherwise be once the table holds more than a handful of
rows past their retention window.

Revision ID: ba3a482fd724
Revises: b4a1f7c92d5e
Create Date: 2026-09-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'ba3a482fd724'  # pragma: allowlist secret
down_revision: str | None = 'b4a1f7c92d5e'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_assets_purge_sweep_discovery',
        'assets',
        ['deleted_at'],
        unique=False,
        schema='document',
        postgresql_where='deleted_at IS NOT NULL AND purged_at IS NULL',
    )


def downgrade() -> None:
    op.drop_index(
        'ix_assets_purge_sweep_discovery',
        table_name='assets',
        schema='document',
        postgresql_where='deleted_at IS NOT NULL AND purged_at IS NULL',
    )
