# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""campaign expanded_at

Closes the race between `request_send` staging `engage.campaign.campaign.send_requested` and
the send driver's own poll (`app/cron.py`): the driver used to treat "nothing claimable and
nothing queued/sending" as "done", which is also exactly what an unexpanded campaign looks like
in the instant before `consumers.expand_campaign_send` (or, now, the driver itself) has written
its first `queued` row. A campaign whose driver tick won that race was marked `sent` with
`recipient_count = 0` and no `campaign_recipients` rows at all — sent to nobody, permanently.

`expanded_at` is the fix's own marker: set once, by `services.expand_campaign`, in the same
transaction as the recipient inserts — including when nobody was sendable. The driver now
refuses to mark a campaign `sent` while it is null, and expands the campaign itself, in its own
transaction, when it finds one that is `sending` but not yet expanded, rather than trust an event
that may simply not have arrived yet (or, on a dead-lettered delivery, may never arrive at all).

**Data.** A campaign already holding `campaign_recipients` rows was, self-evidently, expanded
already — `expanded_at` backfills to `coalesce(started_at, created_at)` for those, the best
record this schema kept of when that happened. A campaign already `sent` with zero recipients and
`recipient_count = 0` is exactly the bug this migration fixes the cause of: production carries
one such row. Backdating it to `sent` would misreport a send that never happened, so it is reset
to `draft` instead — `started_at`, `completed_at` cleared, every count zeroed, `expanded_at`
left null — so its owner can press Send again and this time actually reach their audience.

Revision ID: f4a8c2d6e913
Revises: d8f3a52c9e14
Create Date: 2026-09-24 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a8c2d6e913"  # pragma: allowlist secret
down_revision: str | None = "d8f3a52c9e14"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"

#: Any campaign that already has recipient rows was, by definition, expanded — stamp the best
#: record this schema kept of when: the moment it started sending, or (for the one path that
#: cannot happen today but costs nothing to cover) its creation, if `started_at` is somehow null.
BACKFILL_EXPANDED_SQL = f"""
    UPDATE {SCHEMA}.campaigns c
    SET expanded_at = COALESCE(c.started_at, c.created_at)
    WHERE c.expanded_at IS NULL
      AND EXISTS (
          SELECT 1 FROM {SCHEMA}.campaign_recipients r WHERE r.campaign_id = c.id
      )
"""

#: The exact shape of the bug this migration exists to fix: marked `sent`, nothing was ever
#: expanded, nothing was ever sent. Reset to `draft` so the campaign can be sent for real rather
#: than stand as a false record of a send that never reached anybody.
RESET_NEVER_EXPANDED_SENT_SQL = f"""
    UPDATE {SCHEMA}.campaigns c
    SET status = 'draft',
        started_at = NULL,
        completed_at = NULL,
        recipient_count = 0,
        sent_count = 0,
        failed_count = 0,
        suppressed_count = 0,
        expanded_at = NULL
    WHERE c.status = 'sent'
      AND c.recipient_count = 0
      AND NOT EXISTS (
          SELECT 1 FROM {SCHEMA}.campaign_recipients r WHERE r.campaign_id = c.id
      )
"""


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("expanded_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.execute(BACKFILL_EXPANDED_SQL)
    op.execute(RESET_NEVER_EXPANDED_SENT_SQL)


def downgrade() -> None:
    op.drop_column("campaigns", "expanded_at", schema=SCHEMA)
