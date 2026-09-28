"""reservation transfer

ADR-0024 §6 (amending ADR-0014 §3), `docs/fulfilment/plan.md` F3's kernel prerequisite:
`app.inventory.ledger.post_move` gains an optional reservation transfer on a relocation line —
a pick or pack moves reserved stock from a bin to a staging or pack location *and* carries its
reservation with it, in the one call, so the stock is never free for another order to grab
between "moved" and "reserved again".

Adds to `inventory_reservations`:

* `transferred_milli` — what a *source* reservation gave up to `post_move`'s transfer, its own
  bucket alongside `consumed_milli` (a real draw-down, dispatch's move) and `released_milli`
  (given back, free again). The demand behind a transferred quantity was not met and not freed,
  it moved with the stock — conflating it with either of the other two would make a reservation
  closed by a pick indistinguishable, later, from one closed by a real dispatch (ADR-0024 §6's
  own "why now": that distinction can never be reconstructed once lost). The `consumed_within`
  CHECK widens to `consumed_milli + released_milli + transferred_milli <= quantity_milli`, still
  under that name (nothing outside this migration names the old one).
* `transferred_from_reservation_id` — set only on a *destination* reservation `post_move` opened
  or topped up as the other side of a transfer, tracing it back to the source. A plain in-schema
  self-FK (`inventory_reservations.id`), not the cross-schema kind ADR-0000 §10 forbids. A column
  rather than a transfer-history row (`inventory_reservation_releases`'s own shape): unlike a
  release, a transfer's idempotency is already covered by `post_move`'s own move-level key — a
  retried post short-circuits before ever reaching the transfer logic again — so the extra table
  a release needed for its *own* per-call idempotency record has nothing to protect here. What is
  still needed, and all this column gives, is "which reservation did this one come from".
* `status`'s vocabulary widens from `active | released | consumed` to add `transferred`: a source
  reservation whose remainder closes entirely by transfer (no consumption, no release) lands
  there, kept apart from `consumed` for the same reason `transferred_milli` is its own column.
  The column itself widens `String(10)` → `String(20)` to hold it — "transferred" is 11
  characters — the same column-then-check order F1's `670a53a2c597` (widening `locations.kind`)
  already uses.

Backward compatible: every existing row gets `transferred_milli = 0` and
`transferred_from_reservation_id = NULL`, which satisfy both the old and the new
`consumed_within` identically, and no existing row's `status` is `transferred`, so the rewrite
changes no stored fact.

Revision ID: d2185b2fec98
Revises: b6a2f4c9d1e7
Create Date: 2026-09-28 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2185b2fec98"  # pragma: allowlist secret
down_revision: str | None = "b6a2f4c9d1e7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


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


def upgrade() -> None:
    op.add_column(
        "inventory_reservations",
        sa.Column("transferred_milli", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.alter_column(
        "inventory_reservations", "transferred_milli", server_default=None, schema=SCHEMA
    )
    op.add_column(
        "inventory_reservations",
        sa.Column("transferred_from_reservation_id", sa.UUID(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_inventory_reservations_transferred_from_reservation_id_inventory_reservations"),
        "inventory_reservations",
        "inventory_reservations",
        ["transferred_from_reservation_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "ck_inventory_reservations_consumed_within", "inventory_reservations", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_inventory_reservations_consumed_within"),
        "inventory_reservations",
        "consumed_milli >= 0 AND released_milli >= 0 AND transferred_milli >= 0 "
        "AND consumed_milli + released_milli + transferred_milli <= quantity_milli",
        schema=SCHEMA,
    )

    op.drop_constraint(
        "ck_inventory_reservations_status_known", "inventory_reservations", schema=SCHEMA
    )
    # `String(10)` → `String(20)`: "transferred" is 11 characters, one more than the old column
    # ever needed to hold. Widened before the vocabulary check is recreated, same order F1's
    # `670a53a2c597` (widen `locations.kind`) uses for its own check-and-column pair.
    op.alter_column(
        "inventory_reservations",
        "status",
        type_=sa.String(length=20),
        existing_type=sa.String(length=10),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inventory_reservations_status_known"),
        "inventory_reservations",
        "status IN ('active', 'released', 'consumed', 'transferred')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # A transfer is a fact the old shape has no column for — refuse rather than silently forget
    # it happened, the same refusal shape `46e1eade7b18`'s downgrade uses for a partial release.
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_reservations WHERE transferred_milli <> 0",
        "a reservation has been transferred (ADR-0024 §6) through the column this migration "
        "adds; the pre-F3 shape has no field for it",
    )
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.inventory_reservations WHERE status = 'transferred'",
        "a reservation is closed as 'transferred', a status the pre-F3 vocabulary does not have",
    )

    op.drop_constraint(
        "ck_inventory_reservations_status_known", "inventory_reservations", schema=SCHEMA
    )
    op.alter_column(
        "inventory_reservations",
        "status",
        type_=sa.String(length=10),
        existing_type=sa.String(length=20),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inventory_reservations_status_known"),
        "inventory_reservations",
        "status IN ('active', 'released', 'consumed')",
        schema=SCHEMA,
    )

    op.drop_constraint(
        "ck_inventory_reservations_consumed_within", "inventory_reservations", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_inventory_reservations_consumed_within"),
        "inventory_reservations",
        "consumed_milli >= 0 AND released_milli >= 0 "
        "AND consumed_milli + released_milli <= quantity_milli",
        schema=SCHEMA,
    )

    op.drop_constraint(
        op.f("fk_inventory_reservations_transferred_from_reservation_id_inventory_reservations"),
        "inventory_reservations",
        schema=SCHEMA,
    )
    op.drop_column("inventory_reservations", "transferred_from_reservation_id", schema=SCHEMA)
    op.drop_column("inventory_reservations", "transferred_milli", schema=SCHEMA)
