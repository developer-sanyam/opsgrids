"""snapshot the products a subscription entitles

A `suite` subscription entitled `suite` — a key no gateway route checks — because the lifecycle
events carried only the key it was bought under. The member list a bundle sells is declared on
its catalog version (`bundled_products`, docs/commerce-packaging.md); this column is where
subscription snapshots it, at subscribe and at change-plan, so every `commerce.subscription.*`
event can carry it without anybody downstream reading catalog.

Nullable, with no data step, deliberately. Filling it for existing rows needs catalog's feature
sheet, and reading `catalog.features` from a subscription migration is a cross-schema read baked
into this service's history (ADR-0000 §10). A NULL reads as `[product_key]` — correct for every
subscription that is not a bundle — and `python -m app.backfill` resolves the rest over catalog's
internal API, as a separate, reviewed deploy step (RUNBOOK).

Revision ID: a9c4e2b7d1f0
Revises: f3a7c1d90b42
Create Date: 2026-09-23 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9c4e2b7d1f0"  # pragma: allowlist secret
down_revision: str | None = "f3a7c1d90b42"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("entitled_products", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "entitled_products", schema=SCHEMA)
