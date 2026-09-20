"""register the storefront product

Storefront shipped three ADRs (0010, 0011, 0012), a service, a permission manifest, a gateway
route carrying `"product": "storefront"` — and no row in the catalogue. Found on the first
end-to-end run against the edge: `POST /products/activations {"product_key": "storefront"}`
had nothing to activate, and every merchant route answered 402 from the gateway's entitlement
gate. Nothing could ever have created a store.

`status` is `available`, as the notification product's is and for the same reason: a service
stands behind it. Which products a deployment actually sells remains deployment data and is
edited on the box, not here — this row is what makes that edit possible.

Revision ID: d7e8f9a0b1c2
Revises: c3d4e5f6a7b8
Create Date: 2026-09-20 13:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"  # pragma: allowlist secret
down_revision: str | None = "c3d4e5f6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "storefront"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO product.products
                (id, key, name, category, status, tagline, connects_to, features,
                 is_platform, sort_order)
            VALUES
                (gen_random_uuid(), :key, 'OpsGrids Storefront', 'Revenue', 'available',
                 'A store on your own address, with delivery you can be honest about',
                 '[]'::jsonb, '[]'::jsonb, false, 25)
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(key=KEY)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM product.products WHERE key = :key").bindparams(key=KEY))
