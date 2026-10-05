"""merchant connect: a Cashfree account holds one secret; verification state (ADR-0030)

One additive revision on `merchant_accounts`, no row rewritten and no backfill:

- **The two-secret CHECK is replaced.** `secrets_present_iff_active` demanded both sealed secrets
  on every live row; a Cashfree account has one (its Secret Key also signs every webhook). Now:
  `key_secret_present_iff_active` (`(status = 'active') = (key_secret_enc IS NOT NULL)`), plus the
  two provider rules — `razorpay_active_has_webhook_secret` (a live Razorpay row still holds its
  webhook secret) and `cashfree_has_no_webhook_secret` — and `provider in ('razorpay',
  'cashfree')`. Every existing row is a Razorpay row that satisfied the old CHECK, so it satisfies
  all four: they are created valid.
- **`verified_at`** and **`last_error_code`** (nullable): when the provider last accepted the
  account's credentials (`POST /payment/collections/merchant-account/verify`) and the stable code of
  the last failed check. NULL on every existing row: "unverified", which is the truth.

Nothing else changes: the `provider` column was always a free `String(40)`; the charges, refunds
and inbox unique keys were made provider-scoped by `c7d8e9f0a1b2`; the append-only tables are not
touched; the outbox role's column grant on `merchant_accounts` (`id`, `organization_id`,
`deleted_at`) is unchanged. The Cashfree `x-api-version` is a code constant, not a column.

Downgrade **refuses** while any Cashfree account exists, live or removed: the old CHECK cannot hold
for a live one, and the previous code would build a Razorpay adapter from a Cashfree row's keys.

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-10-06 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"  # pragma: allowlist secret
down_revision: str | None = "c7d8e9f0a1b2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
TABLE = "merchant_accounts"

_OLD_SECRETS = (
    "(status = 'active') = (key_secret_enc IS NOT NULL AND webhook_secret_enc IS NOT NULL)"
)
_NEW_CHECKS: tuple[tuple[str, str], ...] = (
    ("ck_merchant_accounts_provider", "provider in ('razorpay', 'cashfree')"),
    (
        "ck_merchant_accounts_key_secret_present_iff_active",
        "(status = 'active') = (key_secret_enc IS NOT NULL)",
    ),
    (
        "ck_merchant_accounts_razorpay_active_has_webhook_secret",
        "provider <> 'razorpay' OR status <> 'active' OR webhook_secret_enc IS NOT NULL",
    ),
    (
        "ck_merchant_accounts_cashfree_has_no_webhook_secret",
        "provider <> 'cashfree' OR webhook_secret_enc IS NULL",
    ),
)


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABLE, sa.Column("last_error_code", sa.String(length=64), nullable=True), schema=SCHEMA
    )
    for name, condition in _NEW_CHECKS:
        op.create_check_constraint(op.f(name), TABLE, condition, schema=SCHEMA)
    op.drop_constraint(
        op.f("ck_merchant_accounts_secrets_present_iff_active"), TABLE, schema=SCHEMA, type_="check"
    )


def downgrade() -> None:
    bind = op.get_bind()
    cashfree = bind.execute(
        sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE provider = 'cashfree'")
    ).scalar_one()
    if cashfree:
        raise RuntimeError(
            f"refusing to downgrade: {cashfree} Cashfree merchant account(s) exist. The previous "
            "code would build a Razorpay adapter from their keys, and its CHECK cannot hold for a "
            "live one. Remove them deliberately first."
        )
    op.create_check_constraint(
        op.f("ck_merchant_accounts_secrets_present_iff_active"), TABLE, _OLD_SECRETS, schema=SCHEMA
    )
    for name, _condition in reversed(_NEW_CHECKS):
        op.drop_constraint(op.f(name), TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, "last_error_code", schema=SCHEMA)
    op.drop_column(TABLE, "verified_at", schema=SCHEMA)
