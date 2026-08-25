"""a consumption may name the pack it draws down, and inherit that pack's window

A prepaid credit pack is a time-boxed grant (docs/commerce-packaging.md), and until now every
consumption was windowed `[now, NULL)`. Those two do not survive each other: the instant the
pack's `valid_until` passes the grant drops out of the balance `SUM` while the open-ended
consumption keeps counting, and the organization's balance reads *minus what was spent* forever
after — silently eating unrelated credit.

This is the same defect `reversal_of` was given its window-inheritance for in
`c9d2e4f7a1b3`, arriving from the other direction. The fix is the same shape: name the entry
being drawn on and inherit its `[valid_from, valid_until)`, so the pack and everything spent
from it net to zero at every instant the balance can be asked about.

`drawn_from` is a plain nullable UUID into this same table — no self-FK, because the service
resolves it under row-level security and a foreign key would add nothing the tenant policy does
not already give. Its index is partial (only rows that name a pack) and, unlike `reversal_of`'s,
**not unique**: many consumptions legitimately draw on one pack, which is the whole point of a
pack. Nothing is backfilled: every existing consumption spent the general balance, which is what
a NULL means, so the old rows are already correct.

Revision ID: adc4d9aefbdb
Revises: c9d2e4f7a1b3
Create Date: 2026-08-25 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "adc4d9aefbdb"  # pragma: allowlist secret
down_revision: str | None = "c9d2e4f7a1b3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"


def upgrade() -> None:
    op.add_column(
        "credit_ledger",
        sa.Column("drawn_from", sa.UUID(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_credit_ledger_org_drawn_from",
        "credit_ledger",
        ["organization_id", "drawn_from"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("drawn_from IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credit_ledger_org_drawn_from",
        table_name="credit_ledger",
        schema=SCHEMA,
        postgresql_where=sa.text("drawn_from IS NOT NULL"),
    )
    op.drop_column("credit_ledger", "drawn_from", schema=SCHEMA)
