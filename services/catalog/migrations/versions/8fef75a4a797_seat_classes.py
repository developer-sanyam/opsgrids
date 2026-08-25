"""seat classes on a plan version, frozen with it

The one packaging shape the existing machinery could not express
(docs/commerce-packaging.md). `prices` says what a seat costs; nothing said that a plan sells
**two kinds** of seat — a paid member seat and a free view-only or approver seat — still less
that one of them may not write. A zero-amount `per_seat` price would have priced the free seat
correctly and entitled it wrongly, and the entitlement is the expensive half.

Two things this migration does beyond creating the table:

- **`price_id` is an intra-schema foreign key with `ON DELETE RESTRICT`.** Money stays in
  `prices` and exists in exactly one place, so a seat class and its rate can never disagree;
  RESTRICT rather than CASCADE because a price that a seat class is charged at must not vanish
  from under it. Cross-schema keys are what ADR-0000 §10 forbids, not keys inside one schema.
- **The table joins the frozen-children trigger.** `forbid_frozen_version_child_mutation` was
  written for `prices` and `features`; a seat class is part of what the customer signed under in
  exactly the same way — what a seat may *do* is a term of the deal — so once the parent version
  is `published` (or `grandfathered`, still pinned) the row is immutable too. Adding the trigger
  here rather than leaving it for later is the difference between a rule and an intention: the
  function already exists and is table-agnostic (`TG_TABLE_NAME`), so this is one CREATE TRIGGER.

Revision ID: 8fef75a4a797
Revises: e3f8a91c7b2d
Create Date: 2026-08-25 11:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8fef75a4a797"  # pragma: allowlist secret
down_revision: str | None = "e3f8a91c7b2d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"


def upgrade() -> None:
    op.create_table(
        "seat_classes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_version_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("price_id", sa.UUID(), nullable=True),
        sa.Column("included_count", sa.Integer(), nullable=True),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "included_count is null or included_count >= 0",
            name=op.f("ck_seat_classes_included_count_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id"],
            [f"{SCHEMA}.plan_versions.id"],
            name="fk_seat_classes_plan_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["price_id"],
            [f"{SCHEMA}.prices.id"],
            name="fk_seat_classes_price_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seat_classes")),
        sa.UniqueConstraint("plan_version_id", "code", name="uq_seat_classes_version_code"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_seat_classes_plan_version_id",
        "seat_classes",
        ["plan_version_id"],
        unique=False,
        schema=SCHEMA,
    )

    # The same guard `prices` and `features` carry. The function is table-agnostic, so this is
    # the whole change: a published version's seat classes are immutable, a draft's are working
    # material the clone flow rewrites freely.
    op.execute(
        f"""
        CREATE TRIGGER trg_seat_classes_frozen_immutable
            BEFORE UPDATE OR DELETE ON {SCHEMA}.seat_classes
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_frozen_version_child_mutation()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_seat_classes_frozen_immutable ON {SCHEMA}.seat_classes")
    op.drop_index("ix_seat_classes_plan_version_id", table_name="seat_classes", schema=SCHEMA)
    op.drop_table("seat_classes", schema=SCHEMA)
