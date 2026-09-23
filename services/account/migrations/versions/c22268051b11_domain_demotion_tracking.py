"""domain demotion tracking

Adds `consecutive_failures` and `demoted_at` to `org_domains` — MAJOR2's automatic-demotion
policy (`app.domain.should_demote`, `services.recheck_domain`) needs both to decide whether a
sustained run of failed periodic re-checks should clear `verified_at`. See
`docs/adr/0017-domain-verification-lifecycle.md` for the decision and
`infrastructure/edge/RUNBOOK.md` for the operator-facing side.

`consecutive_failures` is added `NOT NULL` with a server-side default of `0` so every existing
row backfills to "never failed" rather than `NULL` — the correct starting state for a domain
this policy has not looked at yet, and the same reasoning `last_checked_at`/`last_check_ok`
being nullable-with-no-default already applies to "never checked". The server default is then
dropped: new rows get `0` from the ORM's own `default=0`, and a server-side default lingering
on the column would silently paper over an application bug that forgot to set the column.

Revision ID: c22268051b11
Revises: 11d00ec66542
Create Date: 2026-09-23 23:19:46.552439
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c22268051b11"  # pragma: allowlist secret
down_revision: str | None = "11d00ec66542"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "account"


def upgrade() -> None:
    op.add_column(
        "org_domains",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.alter_column("org_domains", "consecutive_failures", server_default=None, schema=SCHEMA)
    op.add_column(
        "org_domains",
        sa.Column("demoted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("org_domains", "demoted_at", schema=SCHEMA)
    op.drop_column("org_domains", "consecutive_failures", schema=SCHEMA)
