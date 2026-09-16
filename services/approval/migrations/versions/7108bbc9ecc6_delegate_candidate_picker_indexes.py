"""delegate candidate picker indexes

`ix_hierarchy_nodes_org_display_name` and `ix_hierarchy_nodes_org_employee_no` — data model
§2.2a pattern 16, the sole index the ADR-0006 §15 table assigns to slice 3 ("with the
delegation surface — not earlier either"): `GET /delegate-candidates` (`app/selectors.py`'s
`search_delegate_candidates`) ships in this slice, and ADR-0006 §15's own rule is that an
index ships with the query that needs it, not with the feature it was designed for.

Both are **partial** (`WHERE status <> 'exited'`, matching the endpoint's own exclusion of
exited employees — data model §2.2a: "using the index at all already implies" the predicate,
so it is not carried as a redundant column) and **functional**, on `lower(display_name)` /
`lower(employee_no)` with `varchar_pattern_ops`: the database collation is `en_US.utf8`
(verified, data model §2.2a), and a default-collation btree cannot serve `LIKE 'pri%'`.
`employee_id` trails each as a third column so a matching row's id is served index-only,
the same reasoning `ix_hierarchy_nodes_org_manager` uses in the same section.

Raw `op.execute()`, the same choice `c8d3e6f4a1b2`'s guard triggers made: SQLAlchemy's index
DSL has no clean way to attach an operator class to a functional expression, and the literal
SQL below is transcribed verbatim from data model §2.2a rather than reconstructed through a
DSL that would only approximate it.

`ix_hierarchy_nodes_org_employee_no` is the removable one if the endpoint ever ships
name-only search (data model §2.2a: "delete this index rather than keeping it as a margin")
— it exists only because `GET /delegate-candidates` matches `q` against the staff number too.

Revision ID: 7108bbc9ecc6
Revises: cde7b9237543
Create Date: 2026-09-16 14:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '7108bbc9ecc6'
down_revision: str | None = 'cde7b9237543'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX ix_hierarchy_nodes_org_display_name
            ON {SCHEMA}.hierarchy_nodes (
                organization_id, lower(display_name) varchar_pattern_ops, employee_id
            )
            WHERE status <> 'exited'
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_hierarchy_nodes_org_employee_no
            ON {SCHEMA}.hierarchy_nodes (
                organization_id, lower(employee_no) varchar_pattern_ops, employee_id
            )
            WHERE status <> 'exited'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_hierarchy_nodes_org_employee_no")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_hierarchy_nodes_org_display_name")
