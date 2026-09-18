"""register the notification product

Notification is **platform**, not a product anybody buys. It is registered in the catalogue for
one reason: `list_entitlements` always includes platform products, so marking it `is_platform`
entitles every organization automatically — which is the correct answer for a capability that
ships with the account rather than with a subscription.

The alternative was to gate the routes on `core` and never register anything. That works today
and breaks the moment somebody asks what the platform can do: the permission grammar's first
segment is the product key, so `notification.delivery.template.write` implies a `notification`
product, and a catalogue that does not contain it makes the grammar lie.

`status` is `available`, unlike the nine product lines that are still `soon`, because unlike them
this one has a service behind it.

Revision ID: c3d4e5f6a7b8
Revises: a6b1c8d3e9f2
Create Date: 2026-09-18 10:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"  # pragma: allowlist secret
down_revision: str | None = "a6b1c8d3e9f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "notification"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO product.products
                (id, key, name, category, status, tagline, connects_to, features,
                 is_platform, sort_order)
            VALUES
                (gen_random_uuid(), :key, 'Atmora Notifications', 'Platform', 'available',
                 'Email and WhatsApp that react to what the platform already knows',
                 '[]'::jsonb, '[]'::jsonb, true, 5)
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(key=KEY)
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM product.products WHERE key = :key").bindparams(key=KEY)
    )
