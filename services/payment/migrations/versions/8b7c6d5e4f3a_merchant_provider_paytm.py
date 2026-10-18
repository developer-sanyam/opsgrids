"""merchant accounts: allow Paytm (wave 1 payment gateways)

Widens the `provider` CHECK on `merchant_accounts` to take `paytm`, no row rewritten. Paytm's
merchant key signs requests and webhooks, so a Paytm account holds no webhook secret: the
webhook-secret CHECKs are unchanged.

Downgrade restores the previous CHECK and **refuses** while a Paytm account exists (live or
removed): the old CHECK would reject the row, and the old code would build the wrong adapter.

Revision ID: 8b7c6d5e4f3a
Revises: 9a8b7c6d5e4f
Create Date: 2026-10-16 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b7c6d5e4f3a"  # pragma: allowlist secret
down_revision: str | None = "9a8b7c6d5e4f"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
TABLE = "merchant_accounts"
NAME = "ck_merchant_accounts_provider"
_OLD = "provider in ('razorpay', 'cashfree', 'phonepe', 'payu')"
_NEW = "provider in ('razorpay', 'cashfree', 'phonepe', 'payu', 'paytm')"


def _replace(condition: str) -> None:
    op.drop_constraint(op.f(NAME), TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(op.f(NAME), TABLE, condition, schema=SCHEMA)


def upgrade() -> None:
    _replace(_NEW)


def downgrade() -> None:
    held = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE provider = 'paytm'"))
        .scalar_one()
    )
    if held:
        raise RuntimeError(
            f"refusing to downgrade: {held} Paytm merchant account(s) exist, which the previous "
            "CHECK and code cannot hold. Remove them deliberately first."
        )
    _replace(_OLD)
