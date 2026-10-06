"""payments phase A: checkout charges, the poll index, provider-scoped unique indexes (ADR-0032)

One additive revision, no table rewritten and no backfill:

- **`charges.flow`** (`debit` | `checkout`, server default `debit`) and
  **`charges.checkout_expires_at`**. A checkout charge is `created` with no provider payment until
  the customer pays — exactly the shape the stuck-charge sweep resumes — so the flow is a column the
  sweep filters on, not a convention. A constant default on `ADD COLUMN` is a catalogue change on
  PostgreSQL 11+: every existing row is a `debit`, which is exactly what it always was.
- **CHECKs** `flow` and `checkout_flow_shape` (a checkout is a platform charge with an expiry).
  Both hold for every existing row, so they are created valid.
- **`ix_charges_poll`**: the poller's discovery (charges with a provider Order and no terminal
  state), partial so it stays small. **`uq_charges_open_checkout`**: at most one open checkout per
  invoice, enforced in the database as well as in the service.
- **Provider-scoped unique indexes.** `uq_charges_org_provider_payment_id`,
  `uq_charges_org_provider_order_id`, `uq_mandates_org_provider_mandate_id`,
  `uq_refunds_org_provider_refund_id` and the `provider_events` inbox key
  `uq_provider_events_org_event_id` each gain `provider`: two providers' id spaces are unrelated, and
  one provider's id must never be a conflict with another's. Every one is a **widening** — the new
  key is a superset of the old columns, so no existing row can violate it and nothing is backfilled
  — and the index and constraint **names are kept**: `services._PROVIDER_ID_INDEXES` recognises a
  provider-id collision by them. `provider_events` and `refunds` are append-only; only their
  indexes change, never a row (the trigger fires on row UPDATE/DELETE, not on DDL).

The column grant the stuck-charge sweep's discovery reads `charges` through is re-applied by
`migrations/env.py` after this runs (it now includes `flow`), like every revision.

Downgrade **refuses** while a checkout-flow charge exists: the previous code has no idea what one
is, and would resume it as a stuck debit — failing it as `attempts_exhausted` and suspending a
customer who has not yet paid. It also restores the narrower unique keys, which only succeeds if no
two providers share an id.

Revision ID: c7d8e9f0a1b2
Revises: a8c1d2e3f4b5
Create Date: 2026-10-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"  # pragma: allowlist secret
down_revision: str | None = "a8c1d2e3f4b5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"

#: (table, index name, new columns, old columns, partial predicate)
_SCOPED_INDEXES = (
    (
        "charges",
        "uq_charges_org_provider_payment_id",
        ["organization_id", "provider", "provider_payment_id"],
        ["organization_id", "provider_payment_id"],
        "provider_payment_id IS NOT NULL",
    ),
    (
        "charges",
        "uq_charges_org_provider_order_id",
        ["organization_id", "provider", "provider_order_id"],
        ["organization_id", "provider_order_id"],
        "provider_order_id IS NOT NULL",
    ),
    (
        "mandates",
        "uq_mandates_org_provider_mandate_id",
        ["organization_id", "provider", "provider_mandate_id"],
        ["organization_id", "provider_mandate_id"],
        "provider_mandate_id IS NOT NULL",
    ),
    (
        "refunds",
        "uq_refunds_org_provider_refund_id",
        ["organization_id", "provider", "provider_refund_id"],
        ["organization_id", "provider_refund_id"],
        "provider_refund_id IS NOT NULL",
    ),
)


def _recreate_index(
    table: str, name: str, columns: list[str], where: str
) -> None:
    op.drop_index(name, table_name=table, schema=SCHEMA)
    op.create_index(
        name,
        table,
        columns,
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(where),
    )


def upgrade() -> None:
    op.add_column(
        "charges",
        sa.Column("flow", sa.String(length=16), server_default=sa.text("'debit'"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "charges",
        sa.Column("checkout_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_charges_flow"), "charges", "flow in ('debit', 'checkout')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_charges_checkout_flow_shape"),
        "charges",
        "flow = 'debit' OR (payee = 'platform' AND checkout_expires_at IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_charges_poll",
        "charges",
        ["state", "provider", "flow"],
        schema=SCHEMA,
        postgresql_where=sa.text(
            "provider_order_id IS NOT NULL AND state IN ('created', 'authorized')"
        ),
    )
    op.create_index(
        "uq_charges_open_checkout",
        "charges",
        ["organization_id", "invoice_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "flow = 'checkout' AND state IN ('created', 'authorized') AND invoice_id IS NOT NULL"
        ),
    )

    for table, name, new_columns, _old, where in _SCOPED_INDEXES:
        _recreate_index(table, name, new_columns, where)

    # The inbox key is a constraint, not a bare index. Dropped and re-added in one transaction;
    # the new key is a superset, so no existing row can violate it.
    op.drop_constraint(
        "uq_provider_events_org_event_id", "provider_events", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_provider_events_org_event_id",
        "provider_events",
        ["organization_id", "provider", "provider_event_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    checkouts = bind.execute(
        sa.text(f"SELECT count(*) FROM {SCHEMA}.charges WHERE flow = 'checkout'")
    ).scalar_one()
    if checkouts:
        raise RuntimeError(
            f"refusing to downgrade: {checkouts} checkout-flow charge(s) exist, and the previous "
            "code would resume them as stuck debits and fail them (suspending a customer who has "
            "not yet paid). Close or delete them deliberately first."
        )

    op.drop_constraint(
        "uq_provider_events_org_event_id", "provider_events", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_provider_events_org_event_id",
        "provider_events",
        ["organization_id", "provider_event_id"],
        schema=SCHEMA,
    )
    for table, name, _new, old_columns, where in reversed(_SCOPED_INDEXES):
        _recreate_index(table, name, old_columns, where)

    op.drop_index("uq_charges_open_checkout", table_name="charges", schema=SCHEMA)
    op.drop_index("ix_charges_poll", table_name="charges", schema=SCHEMA)
    op.drop_constraint(op.f("ck_charges_checkout_flow_shape"), "charges", schema=SCHEMA, type_="check")
    op.drop_constraint(op.f("ck_charges_flow"), "charges", schema=SCHEMA, type_="check")
    op.drop_column("charges", "checkout_expires_at", schema=SCHEMA)
    op.drop_column("charges", "flow", schema=SCHEMA)
