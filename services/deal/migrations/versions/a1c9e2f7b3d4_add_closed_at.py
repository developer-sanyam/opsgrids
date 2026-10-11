"""add closed_at

Additive: one nullable column plus this schema's own audit backfill (ADR-0020 §3). No source
event was renamed and nothing here narrows an existing grant, so this migration is safe to run
on production alongside a live consumer.

`closed_at` did not exist before this deal ever closed, so it is recovered from `audit_logs`,
never guessed: the latest `deal.stage_changed` row whose `after->>'stage'` still matches the
deal's current stage, falling back to the `deal.created` row when a deal was created directly in
a closed stage (`lead_conversion`'s deals never are, but a seed or an import might be). A deal
with neither — closed before this service kept an audit trail for it, or seeded with
`created_at` overridden and no audit row at all — keeps `closed_at IS NULL`, and insights' own
`deals_won`/`deals_lost`/`win_rate` coverage for that organization starts at go-live rather than
inventing a date (`docs/insights/metrics.md` §3).

Revision ID: a1c9e2f7b3d4
Revises: e94d7a2c5f18
Create Date: 2026-09-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9e2f7b3d4"  # pragma: allowlist secret
down_revision: str | None = "e94d7a2c5f18"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "deal"


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # 1) The latest stage_changed row whose recorded stage still matches the deal's current one.
    op.execute(
        f"""
        UPDATE {SCHEMA}.deals d
           SET closed_at = latest.at
          FROM (
                SELECT al.entity_id, al.at,
                       row_number() OVER (PARTITION BY al.entity_id ORDER BY al.at DESC) AS rn
                  FROM {SCHEMA}.audit_logs al
                  JOIN {SCHEMA}.deals dd ON dd.id = al.entity_id
                 WHERE al.action = 'deal.stage_changed'
                   AND al.after ->> 'stage' = dd.stage
               ) latest
         WHERE d.id = latest.entity_id
           AND latest.rn = 1
           AND d.stage IN ('closed_won', 'closed_lost')
           AND d.closed_at IS NULL
        """
    )
    # 2) Else the created row, for a deal opened directly in a closed stage.
    op.execute(
        f"""
        UPDATE {SCHEMA}.deals d
           SET closed_at = al.at
          FROM {SCHEMA}.audit_logs al
         WHERE al.entity_id = d.id
           AND al.action = 'deal.created'
           AND al.after ->> 'stage' = d.stage
           AND d.stage IN ('closed_won', 'closed_lost')
           AND d.closed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("deals", "closed_at", schema=SCHEMA)
