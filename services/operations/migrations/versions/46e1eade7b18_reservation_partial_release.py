"""reservation partial release

ADR-0014 §11 (Phase 1 prerequisite, resolved): a count, or any outbound move, that would eat
into reserved stock stays refused (`app.inventory.ledger.post_move`'s check is unchanged) — but
the documented way out is now reachable over HTTP: `POST
/operations/inventory/reservations/{id}:release`, which can give back all or part of a
reservation's open remainder.

Adds:

* `inventory_reservations.released_milli` — what has been explicitly given back, tracked
  separately from `consumed_milli` so a full release of a reservation nothing has drawn against
  yet never has to set `quantity_milli` to zero (the `quantity_positive` CHECK forbids that;
  `quantity_milli` is now the original promise, kept for the record, forever).
* `inventory_reservation_releases` — one append-only row per release call that actually released
  something, the idempotency record for `ledger.release`. A retried release replays instead of
  releasing twice: `UNIQUE (organization_id, reservation_id, idempotency_key)` catches a
  concurrent duplicate the same way `inventory_moves`' own idempotency index does, and the lookup
  runs under the reservation's own row lock (`ledger.release`'s lock order), so there is no race
  between "does this key already exist" and "insert it". A single mutable column on the
  reservation row (an earlier draft of this migration) can only remember the *most recent*
  release's key — a second, later, genuinely different release silently overwrites it and a retry
  of the *first* one would then either false-conflict or, worse, replay the wrong result. An
  append-only table has no such ceiling and doubles as the release history the reservation
  otherwise has no record of.

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
APP_ROLE = "opsgrids_operations"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def _unrls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")


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
        sa.Column("released_milli", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.alter_column("inventory_reservations", "released_milli", server_default=None, schema=SCHEMA)
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

    op.create_table(
        "inventory_reservation_releases",
        sa.Column("reservation_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("released_milli", sa.BigInteger(), nullable=False),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "released_milli > 0",
            name=op.f("ck_inventory_reservation_releases_released_milli_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            [f"{SCHEMA}.inventory_reservations.id"],
            name=op.f("fk_inventory_reservation_releases_reservation_id_inventory_reservations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_reservation_releases")),
        schema=SCHEMA,
    )
    # No plain single-column organization_id index (INFO): the composite unique index below
    # already leads with it, and this table's only read is the exact idempotency-key lookup.
    op.create_index(
        "uq_inventory_reservation_releases_org_reservation_key",
        "inventory_reservation_releases",
        ["organization_id", "reservation_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
    )
    _rls("inventory_reservation_releases")
    # Append-only, like `inventory_moves`/`inventory_ledger` — a release record is never edited
    # or removed once it exists.
    op.execute(
        f'REVOKE UPDATE, DELETE ON {SCHEMA}.inventory_reservation_releases FROM "{APP_ROLE}"'
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

    _unrls("inventory_reservation_releases")
    op.drop_table("inventory_reservation_releases", schema=SCHEMA)

    op.drop_constraint(
        "ck_inventory_reservations_consumed_within", "inventory_reservations", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_inventory_reservations_consumed_within"),
        "inventory_reservations",
        "consumed_milli >= 0 AND consumed_milli <= quantity_milli",
        schema=SCHEMA,
    )
    op.drop_column("inventory_reservations", "released_milli", schema=SCHEMA)
