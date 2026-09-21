"""register the engage product

Engage is served by notification-service but is not the notification product. `notification` is
platform plumbing — `is_platform`, entitled to everyone, the transactional message API every
product sends through. `engage` is what a customer buys: their own sending identity, their
audience, their campaigns. One service, two product keys, because a product is an entitlement and
a permission catalogue rather than a process (ADR-0000 §10 keeps the schema question separate).

`status` is `available`, not `soon`, and that is the whole point of this migration rather than a
detail of it. `activate()` refuses any product marked `soon`, so registering Engage that way would
leave a service that exists behind a gate nothing can open: the activation request refused, and
every `/api/v1/engage` route answering 402 at the gateway. That is precisely how the storefront
registration was found missing (d7e8f9a0b1c2), and it is the trap the nine unshipped product lines
are still in — correctly, because nothing stands behind them. Something stands behind this one.

Which products a deployment actually sells stays deployment data, edited on the box (ADR-0013 §5).
This row is what makes that edit possible.

Revision ID: e5f6a7b8c9d0
Revises: f0a1b2c3d4e5
Create Date: 2026-09-21 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"  # pragma: allowlist secret
down_revision: str | None = "f0a1b2c3d4e5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "engage"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO product.products
                (id, key, name, category, status, tagline, connects_to, features,
                 is_platform, sort_order)
            VALUES
                (gen_random_uuid(), :key, 'OpsGrids Engage', 'Revenue', 'available',
                 'Email your customers from your own domain, to the audience the CRM already knows',
                 '[]'::jsonb, '[]'::jsonb, false, 65)
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(key=KEY)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM product.products WHERE key = :key").bindparams(key=KEY))
