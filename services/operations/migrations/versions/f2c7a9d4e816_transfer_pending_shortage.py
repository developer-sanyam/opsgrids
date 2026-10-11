"""reserve a transfer shortage on its line while its adjustment awaits a decision

A shortage at or over the adjustment threshold becomes an adjustment request and stays in
transit until it is decided. Nothing on the line recorded that, so the same units could be
received or reported short again, and the decision never closed the line.

* `stock_transfer_lines.pending_shortage_milli` — the units spoken for by an open adjustment;
* `adjustment_requests.transfer_line_id` — which line a request came from (a plain UUID, no FK);
* `quantities_within` widens to `received + shortage + pending <= dispatched`.

Both columns are additive with a default, so code from before the release keeps inserting.
The line table is small (one row per transfer line) so the CHECK is swapped in place.

Revision ID: f2c7a9d4e816
Revises: e1b6d4a8c273
Create Date: 2026-10-11 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c7a9d4e816"  # pragma: allowlist secret
down_revision: str | None = "e1b6d4a8c273"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
CHECK = "ck_stock_transfer_lines_quantities_within"
BASE = (
    "dispatched_milli >= 0 AND received_milli >= 0 AND shortage_milli >= 0 "
    "AND dispatched_milli <= quantity_milli "
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "stock_transfer_lines",
        sa.Column("pending_shortage_milli", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "adjustment_requests",
        sa.Column("transfer_line_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint(op.f(CHECK), "stock_transfer_lines", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "quantities_within",
        "stock_transfer_lines",
        BASE
        + "AND pending_shortage_milli >= 0 "
        + "AND received_milli + shortage_milli + pending_shortage_milli <= dispatched_milli",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    held = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {SCHEMA}.stock_transfer_lines WHERE pending_shortage_milli > 0"
        )
    ).scalar_one()
    if held:
        raise RuntimeError(
            f"{held} transfer line(s) hold a pending shortage; decide or cancel their "
            "adjustment requests before downgrading"
        )
    op.drop_constraint(op.f(CHECK), "stock_transfer_lines", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "quantities_within",
        "stock_transfer_lines",
        BASE + "AND received_milli + shortage_milli <= dispatched_milli",
        schema=SCHEMA,
    )
    op.drop_column("adjustment_requests", "transfer_line_id", schema=SCHEMA)
    op.drop_column("stock_transfer_lines", "pending_shortage_milli", schema=SCHEMA)
