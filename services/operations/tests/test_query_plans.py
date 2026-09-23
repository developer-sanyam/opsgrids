"""Tenant-leading indexes for the item master's new lookups (ADR-0014 Phase 1, CLAUDE.md: "every
index leads with organization_id").

`services/account/tests/test_query_plans.py` proves this two ways: forcing `enable_seqscan = off`
and checking which index `EXPLAIN` reaches for, and — for `audit_logs`, where the single-column
`organization_id` index was retired outright — a direct structural check against `pg_indexes`.

Only the second technique gives a reliable answer here. Every table in this service still carries
the plain `organization_id` index `OrgScopedMixin` adds to every tenant table, alongside the new
composite ones, and forcing `enable_seqscan = off` does not reliably choose the composite index
over that plain one even at real scale: verified empirically while writing this suite — a 600-row
table (six categories, 100 rows each) and a 120,000-row table (six categories, 20,000 rows each)
both still planned a category-filtered list over the plain `organization_id` index with a `Filter`
step, not the composite one. The cause is structural, not a tuning problem this suite can fix by
seeding harder: row-level security's own `organization_id = current_org()` policy qual and this
service's own explicit `organization_id = :o` filter (CLAUDE.md: RLS is the net, the filter is not
optional) are the *same* condition applied twice, and the planner prices them as independent,
collapsing its row estimate for the whole query to about 1 regardless of the table's actual size
or the second predicate's real selectivity — so every index looks equally, trivially cheap and the
smaller one wins. That is a planner-estimation property of every tenant-scoped query this whole
platform runs, not something specific to the tables this slice adds, and not one migration should
paper over with `enable_seqscan` gymnastics against a synthetic dataset.

What must actually be true — "an index exists, and its leading column is `organization_id`" — is
checked directly instead, for every composite index this slice adds.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: (table, index) for every composite tenant-leading index this slice adds.
NEW_INDEXES = [
    ("inventory_items", "ix_inventory_items_org_item_category"),
    ("inventory_items", "ix_inventory_items_org_uom"),
    ("item_categories", "uq_item_categories_org_code"),
    ("item_categories", "ix_item_categories_org_parent"),
    ("item_attributes", "uq_item_attributes_org_item_key"),
    ("item_variants", "uq_item_variants_org_sku"),
    ("item_variants", "ix_item_variants_org_item"),
    ("uoms", "uq_uoms_org_code"),
    ("uoms", "ix_uoms_org_dimension"),
    ("uom_conversions", "uq_uom_conversions_org_item_uom"),
]


async def _index_definition(session: AsyncSession, table: str, index: str) -> str | None:
    return (
        await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'operations' AND tablename = :t AND indexname = :i"
            ),
            {"t": table, "i": index},
        )
    ).scalar_one_or_none()


class TestItemMasterIndexesLeadWithTheTenant:
    @pytest.mark.parametrize(("table", "index"), NEW_INDEXES)
    async def test_the_index_exists_and_leads_with_organization_id(
        self, session: AsyncSession, table: str, index: str
    ) -> None:
        definition = await _index_definition(session, table, index)
        assert definition is not None, f"{index} does not exist on operations.{table}"
        assert "(organization_id," in definition or "(organization_id)" in definition, (
            f"{index} does not lead with organization_id: {definition}"
        )


class TestTheAutomaticSingleColumnIndexIsStillThereToo:
    """Not retired, unlike `audit_logs`'s — these tables are new and low-write-volume, and a
    caller can still list `all_inventory`-style with no other predicate. Documented here so the
    next person to read this file does not wonder whether it was supposed to go."""

    @pytest.mark.parametrize(
        "table",
        ["inventory_items", "item_categories", "item_attributes", "item_variants", "uoms"],
    )
    async def test_the_plain_organization_id_index_exists(
        self, session: AsyncSession, table: str
    ) -> None:
        definition = await _index_definition(
            session, table, f"ix_operations_{table}_organization_id"
        )
        assert definition is not None
