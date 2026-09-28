"""add order_id to invoices

Fulfilment F4 (finance slice), ADR-0024 §1/§4: `operations` originates the order, `finance`
raises the invoice for it, and no foreign key may cross the schema boundary (ADR-0000 §10). So
`order_id` is a plain indexed `UUID`, nullable — every invoice raised before this slice, and every
one a person raises by hand through the principal route, has none.

The partial unique index is the actual guarantee: **one `invoice` document per order.** It is
scoped to `document_type = 'invoice'` on purpose — a `credit_note` correcting that invoice
legitimately names the same `order_id`, and must not collide with the index that is protecting the
original document. It excludes soft-deleted rows for the same reason every other partial index
here does: a row this table's own soft-delete has retired must not go on blocking a real one.

`services.evaluate_order_invoice_request` checks this by query before it ever tries to write — the
common case, a redelivered `operations.orders.order.invoice_requested` for an order that already
has its invoice, never has to reach the database's own guard. The guard exists for the genuine
race: two deliveries for the same order landing in two consumer replicas close enough together
that both pass the pre-check. That one is caught as an `IntegrityError` on this very index,
inside a `SAVEPOINT` (`session.begin_nested()`), and treated as the same benign no-op the pre-check
would have been — never raised to the caller, never dead-lettered.

Revision ID: 57e43c60b6db
Revises: e7d648663241
Create Date: 2026-09-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = "57e43c60b6db"  # pragma: allowlist secret
down_revision: str | None = "e7d648663241"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    # Nullable, no default: an invoice raised before this slice, or by a person through the
    # principal route, genuinely has no order behind it — null is what "not from an order" is.
    op.add_column(
        "invoices", sa.Column("order_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_index(
        "uq_invoices_org_order_id",
        "invoices",
        ["organization_id", "order_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND document_type = 'invoice' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_invoices_org_order_id",
        table_name="invoices",
        schema=SCHEMA,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND document_type = 'invoice' AND deleted_at IS NULL"
        ),
    )
    op.drop_column("invoices", "order_id", schema=SCHEMA)
