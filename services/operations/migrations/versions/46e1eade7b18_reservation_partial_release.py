"""reservation partial release

ADR-0014 §11 (Phase 1 prerequisite, resolved): a count, or any outbound move, that would eat
into reserved stock stays refused (`app.inventory.ledger.post_move`'s check is unchanged) — but
the documented way out is now reachable over HTTP: `POST
/operations/inventory/reservations/{id}:release`, which can give back all or part of a
reservation's open remainder.

Adds to `inventory_reservations`:

* `released_milli` — what has been explicitly given back, tracked separately from
  `consumed_milli` so a full release of a reservation nothing has drawn against yet never has to
  set `quantity_milli` to zero (the `quantity_positive` CHECK forbids that; `quantity_milli` is
  now the original promise, kept for the record, forever).
* `release_idempotency_key` / `release_request_hash` — a retried release replays instead of
  releasing twice. Not a unique index: the row's own lock (`ledger.release`'s lock order) already
  serialises every writer of one reservation, so a plain compare under that lock is enough.

The `consumed_within` CHECK becomes `consumed_milli + released_milli <= quantity_milli`, still
under that name (nothing outside this migration names the old check).

Backward compatible: every existing row gets `released_milli = 0`, which satisfies both the old
and the new check identically, so the rewrite changes no stored fact.

Revision ID: 46e1eade7b18
Revises: d4e8b1c93f20
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "46e1eade7b18"  # pragma: allowlist secret
down_revision: str | None = "d4e8b1c93f20"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


def upgrade() -> None:
    op.add_column(
        "inventory_reservations",
        sa.Column("released_milli", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.alter_column(
        "inventory_reservations", "released_milli", server_default=None, schema=SCHEMA
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("release_idempotency_key", sa.String(length=128), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("release_request_hash", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint("ck_inventory_reservations_consumed_within", "inventory_reservations", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_inventory_reservations_consumed_within"),
        "inventory_reservations",
        "consumed_milli >= 0 AND released_milli >= 0 "
        "AND consumed_milli + released_milli <= quantity_milli",
        schema=SCHEMA,
    )


def _refuse_if(sql: str, message: str) -> None:
    message = message.replace("'", "''")
    op.execute(
        f"""
        DO $$
        DECLARE offender text;
        BEGIN
            SELECT x::text INTO offender FROM ({sql}) AS x LIMIT 1;
            IF offender IS NOT NULL THEN
                RAISE EXCEPTION '{message} (first offender: %)', offender;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # A partial release is a fact the old shape has no column for — refuse rather than silently
    # forget it happened (this migration's own upgrade never invents data; its downgrade does
    # not delete it unrecorded either).
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_reservations WHERE released_milli <> 0",
        "a reservation has been released (in full or in part) through the column this "
        "migration adds; the pre-Phase-1 shape has no field for it",
    )
    op.drop_constraint("ck_inventory_reservations_consumed_within", "inventory_reservations", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_inventory_reservations_consumed_within"),
        "inventory_reservations",
        "consumed_milli >= 0 AND consumed_milli <= quantity_milli",
        schema=SCHEMA,
    )
    op.drop_column("inventory_reservations", "release_request_hash", schema=SCHEMA)
    op.drop_column("inventory_reservations", "release_idempotency_key", schema=SCHEMA)
    op.drop_column("inventory_reservations", "released_milli", schema=SCHEMA)
