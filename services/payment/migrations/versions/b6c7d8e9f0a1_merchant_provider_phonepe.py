"""merchant accounts: allow PhonePe (wave 1 payment gateways)

Widens three CHECKs on `merchant_accounts`, no row rewritten:

- `provider` takes `phonepe` (`app.models.MERCHANT_PROVIDERS`).
- `webhook_secret_required` / `webhook_secret_forbidden` list `phonepe` with Razorpay: a PhonePe
  merchant sets a webhook username and password in PhonePe's dashboard, and the password is kept
  sealed as the account's webhook secret (`MERCHANT_WEBHOOK_SECRET_PROVIDERS`).

Downgrade restores the previous CHECKs and **refuses** while a PhonePe account exists (live or
removed): the old CHECK would reject the row, and the old code would build the wrong adapter.

Revision ID: b6c7d8e9f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-10-16 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"  # pragma: allowlist secret
down_revision: str | None = "a4b5c6d7e8f9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
TABLE = "merchant_accounts"


def _checks(providers: str, hook_providers: str) -> tuple[tuple[str, str], ...]:
    return (
        ("ck_merchant_accounts_provider", f"provider in ({providers})"),
        (
            "ck_merchant_accounts_webhook_secret_required",
            f"provider not in ({hook_providers}) OR status <> 'active' "
            "OR webhook_secret_enc IS NOT NULL",
        ),
        (
            "ck_merchant_accounts_webhook_secret_forbidden",
            f"provider in ({hook_providers}) OR webhook_secret_enc IS NULL",
        ),
    )


_OLD = _checks("'razorpay', 'cashfree'", "'razorpay'")
_NEW = _checks("'razorpay', 'cashfree', 'phonepe'", "'razorpay', 'phonepe'")


def _replace(checks: tuple[tuple[str, str], ...]) -> None:
    for name, condition in checks:
        op.drop_constraint(op.f(name), TABLE, schema=SCHEMA, type_="check")
        op.create_check_constraint(op.f(name), TABLE, condition, schema=SCHEMA)


def upgrade() -> None:
    _replace(_NEW)


def downgrade() -> None:
    held = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE provider = 'phonepe'"))
        .scalar_one()
    )
    if held:
        raise RuntimeError(
            f"refusing to downgrade: {held} PhonePe merchant account(s) exist, which the previous "
            "CHECK and code cannot hold. Remove them deliberately first."
        )
    _replace(_OLD)
