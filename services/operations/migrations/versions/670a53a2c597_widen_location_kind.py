"""widen location kind

ADR-0024 §5 (amendment to ADR-0014 §1): fulfilment needs places that are neither racks nor
bins — where picks are staged, where orders are packed, where trucks load, where returns and
receipts land. `Location.kind` is extended rather than duplicated into a parallel table.

Ships as its own migration, on purpose, separate from the barcode/capacity columns this same
plan phase (F1) also adds (`46345e7e794d`) and separate from every later fulfilment migration —
a revert of the fulfilment tables must never depend on reverting the vocabulary, or the other way
round.

Upgrade:

* widens `locations.kind` from `String(10)` to `String(20)` (`pack_station` is 12 characters and
  will not fit in 10);
* drops `ck_locations_kind_known` and recreates it with the full vocabulary: `zone, aisle, rack,
  shelf, bin, staging, pack_station, dock, receiving, returns, quarantine`.

No production location uses anything but `bin` today (nothing on the write path sets `kind`), so
this is the cheapest the change will ever be.

Downgrade **refuses** if any row already carries a kind outside the original `zone | rack | bin`
set — the same refusal shape `46e1eade7b18`/`7899dcdf7534` use rather than guessing what a
`pack_station` row should become — then restores the old constraint and the old column width.

Revision ID: 670a53a2c597
Revises: 7899dcdf7534
Create Date: 2026-09-28 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "670a53a2c597"  # pragma: allowlist secret
down_revision: str | None = "7899dcdf7534"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"

_OLD_KINDS = ("zone", "rack", "bin")
_NEW_KINDS = (
    "zone",
    "aisle",
    "rack",
    "shelf",
    "bin",
    "staging",
    "pack_station",
    "dock",
    "receiving",
    "returns",
    "quarantine",
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


def upgrade() -> None:
    op.alter_column(
        "locations",
        "kind",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
        schema=SCHEMA,
    )
    op.drop_constraint("ck_locations_kind_known", "locations", schema=SCHEMA)
    kinds_sql = ", ".join(f"'{k}'" for k in _NEW_KINDS)
    op.create_check_constraint(
        op.f("ck_locations_kind_known"),
        "locations",
        f"kind IN ({kinds_sql})",
        schema=SCHEMA,
    )


def downgrade() -> None:
    old_kinds_sql = ", ".join(f"'{k}'" for k in _OLD_KINDS)
    _refuse_if(
        f"SELECT id FROM {SCHEMA}.locations WHERE kind NOT IN ({old_kinds_sql})",
        "a location uses a kind outside the pre-fulfilment zone|rack|bin vocabulary; the "
        "pre-F1 shape has no meaning for it",
    )

    op.drop_constraint("ck_locations_kind_known", "locations", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_locations_kind_known"),
        "locations",
        f"kind IN ({old_kinds_sql})",
        schema=SCHEMA,
    )
    op.alter_column(
        "locations",
        "kind",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
        existing_nullable=False,
        schema=SCHEMA,
    )
