"""collections: which wire made a collection's provider Order (ADR-0033 §8)

One additive revision on `charges`, no row rewritten and no backfill:

- **`order_wire`** (nullable `varchar(8)`): `stub` when a merchant collection's provider Order was
  made by the in-process stand-in (`COLLECTIONS_WIRE` / `COLLECTIONS_CASHFREE_WIRE=stub`), `live`
  when the provider made it. Recorded once, with the Order. A stub Order can never be paid and no
  real provider holds it, so a collection made on the stub is cancelled and expired without asking
  a provider that has never heard of it, and it never holds up a disconnect once closed. NULL on
  every existing row and on every platform charge: "not known", which collections treats as
  possibly real — the conservative reading.
- **CHECK `order_wire`**: NULL, or a merchant charge's `stub` / `live`. Holds for every existing
  row (all NULL), so it is created valid.

The stuck-charge sweep's column grant is not widened: its discovery never reads this column.

Downgrade drops the CHECK and the column. Nothing else reads it, so the previous code loses only
the knowledge, and treats every collection as possibly real again.

Revision ID: e2f3a4b5c6d7
Revises: d9e0f1a2b3c4
Create Date: 2026-10-07 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"  # pragma: allowlist secret
down_revision: str | None = "d9e0f1a2b3c4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
TABLE = "charges"
CHECK = "order_wire IS NULL OR (payee = 'merchant' AND order_wire in ('stub', 'live'))"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("order_wire", sa.String(length=8), nullable=True), schema=SCHEMA)
    op.create_check_constraint(op.f("ck_charges_order_wire"), TABLE, CHECK, schema=SCHEMA)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_charges_order_wire"), TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, "order_wire", schema=SCHEMA)
