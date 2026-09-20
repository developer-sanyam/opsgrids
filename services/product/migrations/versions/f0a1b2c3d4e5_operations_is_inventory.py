"""call the operations product what it is: Inventory

The `operations` service is suppliers, warehouses, purchase orders, receiving and stock on
hand — inventory management, and the first product sold self-serve (ADR-0013). The key stays
`operations`: it is the permission grammar's first segment, the compose profile is keyed to it,
and renaming a key is a migration of every grant in every organisation for the sake of a word.
The name and tagline are what a customer reads on the marketplace, and those change here.

`status` is deliberately not touched: whether this deployment sells it is deployment data, set
on the box (ADR-0013 §5).

Revision ID: f0a1b2c3d4e5
Revises: d7e8f9a0b1c2
Create Date: 2026-09-20 16:42:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"  # pragma: allowlist secret
down_revision: str | None = "d7e8f9a0b1c2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "operations"
OLD = ("OpsGrids Operations", None)
NEW = ("OpsGrids Inventory", "Suppliers, purchase orders and stock on hand — the ledger a warehouse runs on")


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE product.products SET name = :name, tagline = :tagline WHERE key = :key"
        ).bindparams(name=NEW[0], tagline=NEW[1], key=KEY)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE product.products SET name = :name, tagline = :tagline WHERE key = :key"
        ).bindparams(name=OLD[0], tagline=OLD[1], key=KEY)
    )
