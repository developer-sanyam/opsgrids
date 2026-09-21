"""service request decision note

`POST /itsm/service-requests/{number}/decision` lets an approver reject a request with a reason.
A rejection with no visible reason is not a usable feature, so the reason needs somewhere to live
beyond the audit row (which the SPA never renders) — `decision_note` on `itsm.requests`, nullable
and unset until a decision is made.

Revision ID: 1b29e2bcb7ad
Revises: b7e4f2a9c1d3
Create Date: 2026-09-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b29e2bcb7ad"  # pragma: allowlist secret
down_revision: str | None = "b7e4f2a9c1d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "itsm"


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("decision_note", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("requests", "decision_note", schema=SCHEMA)
