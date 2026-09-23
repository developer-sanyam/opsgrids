# ruff: noqa: S608 — every f-string here interpolates only `SCHEMA` and this module's own
# constants/uuids, never external input.
"""Tenant-leading indexes for the item master's new lookups (ADR-0014 Phase 1, CLAUDE.md: "every
index leads with organization_id").

Two checks, same shape as `services/account/tests/test_query_plans.py`:

1. Structural — every composite index this slice adds exists and leads with `organization_id`,
   checked directly against `pg_indexes`.
2. Behavioural — seeded at a realistic size, `ANALYZE`d, and then the *exact* statement one of
   this service's own selectors compiles is `EXPLAIN`ed as the app role with the tenant GUC set
   (the real shape production runs: RLS's own qual and the selector's explicit
   `organization_id = :o` filter both in the query the planner sees), and the composite index
   the query was built for is the one the planner reaches for.

An earlier version of this file claimed row-level security's policy qual and the selector's own
explicit `organization_id` filter "double count" and collapse the planner's row estimate to
around 1 regardless of the table's real size, making the composite index look no cheaper than the
plain single-column one even at 120,000 rows. That claim does not hold up: the planner merges the
RLS qual and the explicit filter into one `organization_id = $1` condition (visible as a
"One-Time Filter" once parameterised, or folded directly into the index condition once the value
is a literal), and the row estimate for the rest of the query stays realistic. The actual cause of
that earlier result was seeding without a following `ANALYZE` (stale-to-absent statistics make
every plan look equally cheap) compounded by an `ORDER BY … LIMIT` in the comparison query, which
let a differently-ordered index "early-exit" its way to a cheap-looking plan regardless of how
selective the real filter was. Seed as an org would organically fill the table, `ANALYZE`, and
`EXPLAIN` the selector's own unpaginated statement (no `LIMIT` beyond what the selector itself
adds) and the composite index wins on its merits.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.inventory import selectors as inv_selectors
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for

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


# ------------------------------------------------------------------ behavioural: EXPLAIN at scale

#: The earlier (incorrect) version of this file seeded 120,000 rows across six categories — a
#: 1-in-6 filter, selective enough on paper but not selective enough in practice to beat an
#: already-sku-ordered index scan of the whole per-tenant range (no early exit is possible without
#: a `LIMIT`, and an unordered index-scan-plus-sort only wins once the filter throws away most of
#: what it would otherwise have to touch). Same total row count, spread over a hundred categories
#: instead of six, so one category is a realistic 1-in-100 narrowing — closer to an org whose
#: items are actually organised into a real catalogue tree than "everything is basically one of
#: six buckets" was.
CATEGORY_COUNT = 100
ROWS_PER_CATEGORY = 1_200


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _index_names(plan: Any) -> set[str]:
    """Every index the plan tree touches, at any depth."""
    found: set[str] = set()
    if isinstance(plan, dict):
        if "Index Name" in plan:
            found.add(plan["Index Name"])
        for value in plan.values():
            found |= _index_names(value)
    elif isinstance(plan, list):
        for value in plan:
            found |= _index_names(value)
    return found


def _compiled(stmt: Any) -> str:
    """The exact SQL a selector produces, literals inlined — what `EXPLAIN` sees is what
    production runs, not a parameterised stand-in for it."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.fixture
async def item_list_at_scale():  # type: ignore[no-untyped-def]
    """One organization, `CATEGORY_COUNT` categories, `ROWS_PER_CATEGORY` items apiece — seeded
    with raw bulk SQL (an ORM insert loop of 120,000 rows would dominate the test's runtime for
    no reason) and `ANALYZE`d before anything reads it. Returns `(org_id, one_category_id)`."""
    org_id = uuid.uuid4()
    category_ids = [uuid.uuid4() for _ in range(CATEGORY_COUNT)]
    admin = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    try:
        async with admin.begin() as conn:
            # `CATEGORY_COUNT` rows — a Python-side loop of individually bound inserts is simplest
            # and its cost is negligible next to the bulk item insert below.
            for i, category_id in enumerate(category_ids, start=1):
                await conn.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.item_categories "
                        "(id, organization_id, code, name, created_at, updated_at) "
                        "VALUES (:id, :org, :code, :name, now(), now())"
                    ),
                    {"id": category_id, "org": org_id, "code": f"CAT-{i}", "name": f"Category {i}"},
                )
            category_array = "ARRAY[" + ",".join(f"'{c}'::uuid" for c in category_ids) + "]"
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.inventory_items "
                    "(id, organization_id, sku, name, category, warehouse, reorder_point, uom, "
                    " unit_value_minor, currency, hsn_code, track_lot, track_roll, "
                    " track_expiry, track_quality, is_active, item_category_id, "
                    " created_at, updated_at) "
                    "SELECT gen_random_uuid(), :org, 'ITM-' || gs, 'Item ' || gs, "
                    "'raw_materials', '', 0, 'pcs', 0, 'INR', '', "
                    "false, false, false, false, true, "
                    f"({category_array})[1 + (gs % {CATEGORY_COUNT})], now(), now() "
                    f"FROM generate_series(1, {CATEGORY_COUNT * ROWS_PER_CATEGORY}) AS gs"
                ),
                {"org": org_id},
            )
            await conn.execute(text(f"ANALYZE {SCHEMA}.inventory_items"))
            await conn.execute(text(f"ANALYZE {SCHEMA}.item_categories"))
        yield org_id, category_ids[0]
    finally:
        async with admin.begin() as conn:
            await conn.execute(
                text(f"DELETE FROM {SCHEMA}.inventory_items WHERE organization_id = :o"),
                {"o": org_id},
            )
            await conn.execute(
                text(f"DELETE FROM {SCHEMA}.item_categories WHERE organization_id = :o"),
                {"o": org_id},
            )
        await admin.dispose()


class TestTheItemCategoryIndexServesTheItemListAtScale:
    async def test_a_category_filtered_item_list_is_served_by_the_composite_index(
        self, item_list_at_scale: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The list screen behind `GET /operations/inventory?item_category_id=…` (ADR-0014 Phase
        1, plan §4 "item list") — one category out of a hundred, `ROWS_PER_CATEGORY` of
        `CATEGORY_COUNT * ROWS_PER_CATEGORY` rows. The composite `(organization_id,
        item_category_id)` index should be cheaper than reading every one of this org's rows off
        the plain `organization_id` index (or the `(organization_id, sku)` one the default sort
        could otherwise reach for) and discarding all but the one matching category in a hundred.
        """
        org_id, category_id = item_list_at_scale
        ctx = ctx_for(org_id, "operations.inventory.item.read", Scope.ORG)
        sql = _compiled(inv_selectors.inventory_stmt(ctx, item_category_id=category_id))

        app_engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        try:
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)"))
                raw = (await conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))).scalar_one()
        finally:
            await app_engine.dispose()

        plan = raw if isinstance(raw, list) else json.loads(raw)
        used = _index_names(plan)
        assert "ix_inventory_items_org_item_category" in used, (
            f"the category-filtered item list was planned over {sorted(used) or 'no index'} at "
            f"{CATEGORY_COUNT * ROWS_PER_CATEGORY} rows; ix_inventory_items_org_item_category "
            "should serve it instead of a full per-tenant scan or sort."
        )
