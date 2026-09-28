"""location barcode, pick sequence and capacity columns

ADR-0024 §5 / Fulfilment plan F1: a location gains what picking and putaway need once its kind
vocabulary is wide enough to describe where picks stage, orders pack and trucks load
(`670a53a2c597`, the migration directly before this one).

Adds, all nullable and additive — this migration invents no data and cannot lose any:

* `barcode` (`String(64)`) — unique **per organization** where set. A partial unique index
  (`barcode IS NOT NULL AND deleted_at IS NULL`), the same shape as
  `uq_locations_org_warehouse_code`, so two live locations in the same org can never claim the
  same scanned value, but a soft-deleted location's old barcode does not block reuse.
* `pick_sequence` (`Integer`) — orders a picker's walk through a zone.
* `max_weight_grams` (`BigInteger`, `CHECK (... IS NULL OR ... >= 0)`).
* `max_volume_milli` (`BigInteger`, `CHECK (... IS NULL OR ... >= 0)`).
* `storage_type` (`String(30)`) — free text for now; a fixed vocabulary is a later phase's call
  per the plan.

Downgrade drops all five cleanly. Unlike `670a53a2c597`, which narrows an existing column's
meaning, these are new nullable columns nothing yet depends on, so there is no data-loss refusal
to make.

Revision ID: 46345e7e794d
Revises: 670a53a2c597
Create Date: 2026-09-28 09:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "46345e7e794d"  # pragma: allowlist secret
down_revision: str | None = "670a53a2c597"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"


def upgrade() -> None:
    op.add_column(
        "locations", sa.Column("barcode", sa.String(length=64), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "locations", sa.Column("pick_sequence", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "locations", sa.Column("max_weight_grams", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "locations", sa.Column("max_volume_milli", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "locations", sa.Column("storage_type", sa.String(length=30), nullable=True), schema=SCHEMA
    )

    op.create_check_constraint(
        op.f("ck_locations_max_weight_grams_non_negative"),
        "locations",
        "max_weight_grams IS NULL OR max_weight_grams >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_locations_max_volume_milli_non_negative"),
        "locations",
        "max_volume_milli IS NULL OR max_volume_milli >= 0",
        schema=SCHEMA,
    )

    op.create_index(
        "uq_locations_org_barcode",
        "locations",
        ["organization_id", "barcode"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("barcode IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_locations_org_barcode", table_name="locations", schema=SCHEMA)

    op.drop_constraint(
        "ck_locations_max_volume_milli_non_negative", "locations", schema=SCHEMA
    )
    op.drop_constraint(
        "ck_locations_max_weight_grams_non_negative", "locations", schema=SCHEMA
    )

    op.drop_column("locations", "storage_type", schema=SCHEMA)
    op.drop_column("locations", "max_volume_milli", schema=SCHEMA)
    op.drop_column("locations", "max_weight_grams", schema=SCHEMA)
    op.drop_column("locations", "pick_sequence", schema=SCHEMA)
    op.drop_column("locations", "barcode", schema=SCHEMA)
