"""merchant accounts: provider-generic secret checks and a non-secret config (integrations framework)

One additive revision on `merchant_accounts`, no row rewritten and no backfill:

- **`config` JSONB NOT NULL DEFAULT '{}'**: non-secret, provider-specific settings a connector
  needs beyond the key pair. Every existing row gets `{}`.
- **The two provider-named secret CHECKs become capability-named.** `razorpay_active_has_webhook_
  secret` and `cashfree_has_no_webhook_secret` named the two providers; they are replaced by
  `webhook_secret_required` and `webhook_secret_forbidden`, which list the providers whose merchant chooses a webhook secret
  (`app.models.MERCHANT_WEBHOOK_SECRET_PROVIDERS`: `razorpay`). Same meaning today, so every existing
  row satisfies them; a later provider is a name in a tuple and a migration like this one, not a new
  bespoke CHECK.
- The `provider` CHECK is untouched (`razorpay`, `cashfree`). The model now builds it from
  `MERCHANT_PROVIDERS`, a test pins that list to the registry, and a later provider's revision
  widens the CHECK to match -- the same text today, so no DDL here.

Downgrade restores the two previous CHECKs (every row written by this code satisfies them: no
other provider is allowed yet) and drops `config`, **refusing** while any row holds a non-empty
config, because dropping it would silently lose settings a connector needs.

Revision ID: a4b5c6d7e8f9
Revises: e2f3a4b5c6d7
Create Date: 2026-10-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4b5c6d7e8f9"  # pragma: allowlist secret
down_revision: str | None = "e2f3a4b5c6d7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
TABLE = "merchant_accounts"

_OLD: tuple[tuple[str, str], ...] = (
    (
        "ck_merchant_accounts_razorpay_active_has_webhook_secret",
        "provider <> 'razorpay' OR status <> 'active' OR webhook_secret_enc IS NOT NULL",
    ),
    (
        "ck_merchant_accounts_cashfree_has_no_webhook_secret",
        "provider <> 'cashfree' OR webhook_secret_enc IS NULL",
    ),
)
_NEW: tuple[tuple[str, str], ...] = (
    (
        "ck_merchant_accounts_webhook_secret_required",
        "provider not in ('razorpay') OR status <> 'active' OR webhook_secret_enc IS NOT NULL",
    ),
    (
        "ck_merchant_accounts_webhook_secret_forbidden",
        "provider in ('razorpay') OR webhook_secret_enc IS NULL",
    ),
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    for name, _ in _OLD:
        op.drop_constraint(op.f(name), TABLE, schema=SCHEMA, type_="check")
    for name, condition in _NEW:
        op.create_check_constraint(op.f(name), TABLE, condition, schema=SCHEMA)


def downgrade() -> None:
    bind = op.get_bind()
    configured = bind.execute(
        sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE config <> '{{}}'::jsonb")
    ).scalar_one()
    if configured:
        raise RuntimeError(
            f"refusing to downgrade: {configured} merchant account(s) hold a non-empty config, "
            "which dropping the column would lose. Clear or export it deliberately first."
        )
    for name, _ in reversed(_NEW):
        op.drop_constraint(op.f(name), TABLE, schema=SCHEMA, type_="check")
    for name, condition in _OLD:
        op.create_check_constraint(op.f(name), TABLE, condition, schema=SCHEMA)
    op.drop_column(TABLE, "config", schema=SCHEMA)
