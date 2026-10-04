# ruff: noqa: S608 — every f-string here interpolates only `SCHEMA` and this module's own
# constants/uuids, never external input.
"""The indexes the lot / roll / ledger reads were built for (inventory Phase 1, I5; CLAUDE.md:
"every index leads with organization_id").

Same two checks as `test_query_plans.py`: every index this slice adds exists and leads with the
tenant, and — seeded at a realistic size and `ANALYZE`d — the *exact* first-page statement the
selectors compile (keyset order and limit included) is planned over the index built for it, as
the app role with the tenant GUC set. A list that quietly falls back to scanning and sorting the
whole ledger is a production incident at a million rows, and nothing else in the suite would
notice.
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
from app.inventory import tracking_selectors as sel
from app.models import SCHEMA, LedgerEntry, Lot, Roll
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

NEW_INDEXES = [
    ("lots", "uq_lots_org_lot_no"),
    ("lots", "ix_lots_org_item"),
    ("lots", "ix_lots_org_created"),
    ("rolls", "uq_rolls_org_roll_no"),
    ("rolls", "ix_rolls_org_item"),
    ("rolls", "ix_rolls_org_lot"),
    ("rolls", "ix_rolls_org_parent"),
    ("rolls", "ix_rolls_org_created"),
    ("roll_events", "ix_roll_events_org_roll"),
    ("roll_events", "ix_roll_events_org_related"),
    ("lot_genealogy", "ix_lot_genealogy_org_parent"),
    ("lot_genealogy", "ix_lot_genealogy_org_child"),
    ("inventory_ledger", "ix_inventory_ledger_org_created"),
    ("inventory_ledger", "ix_inventory_ledger_org_location_created"),
    ("inventory_ledger", "ix_inventory_ledger_org_lot_created"),
    ("inventory_ledger", "ix_inventory_ledger_org_roll_created"),
    ("inventory_balances", "ix_inventory_balances_org_location"),
    ("inventory_balances", "ix_inventory_balances_org_lot"),
    ("inventory_balances", "ix_inventory_balances_org_roll"),
    ("inventory_moves", "ix_inventory_moves_org_occurred"),
]

#: Ledger rows, spread so that a roll or lot is a small slice of the tenant's history.
LEDGER_ROWS = 300_000
TRACKED = 1_000  # lots and rolls
TRACKED_ROWS = 200_000  # ledger rows that name one


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _index_names(plan: Any) -> set[str]:
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
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestNewIndexesLeadWithTheTenant:
    @pytest.mark.parametrize(("table", "index"), NEW_INDEXES)
    async def test_the_index_exists_and_leads_with_organization_id(
        self, session: AsyncSession, table: str, index: str
    ) -> None:
        definition = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'operations' AND tablename = :t AND indexname = :i"
                ),
                {"t": table, "i": index},
            )
        ).scalar_one_or_none()
        assert definition is not None, f"{index} does not exist on operations.{table}"
        assert "(organization_id," in definition or "(organization_id)" in definition, definition


def _lot_uuid(expr: str) -> str:
    return f"md5('lot' || ({expr})::text)::uuid"


def _roll_uuid(expr: str) -> str:
    return f"md5('roll' || ({expr})::text)::uuid"


@pytest.fixture
async def ledger_at_scale(session):  # type: ignore[no-untyped-def]
    """One organization: an item, a bin, `TRACKED` lots and rolls, and `LEDGER_ROWS` ledger rows
    over a year of history — bulk SQL, then `ANALYZE`. Returns `(org_id, a roll id, a lot id)`.
    Depends on `session` only for its TRUNCATE-at-start, so a previous test's rows are gone."""
    org_id = uuid.uuid4()
    ids = {name: uuid.uuid4() for name in ("wh", "loc", "item", "move")}
    admin = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    try:
        async with admin.begin() as conn:
            params = {**ids, "org": org_id}
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location, "
                    "created_at, updated_at) VALUES (:wh, :org, 'W', 'W', '', now(), now())"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, "
                    "code, name, is_default, created_at, updated_at) "
                    "VALUES (:loc, :org, :wh, 'bin', 'D', '', true, now(), now())"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.inventory_items (id, organization_id, sku, name, "
                    "category, warehouse, reorder_point, uom, unit_value_minor, currency, "
                    "hsn_code, track_lot, track_roll, track_expiry, track_quality, is_active, "
                    "created_at, updated_at) VALUES (:item, :org, 'S', 'S', 'raw_materials', '', "
                    "0, 'm', 0, 'INR', '', true, true, false, false, true, now(), now())"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, note, "
                    "occurred_on, created_at) VALUES (:move, :org, 'opening', '', "
                    "'2026-01-01', now())"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.lots (id, organization_id, inventory_item_id, lot_no, "
                    f"quality_status, created_at, updated_at) SELECT {_lot_uuid('gs')}, :org, "
                    "CAST(:item AS uuid), 'L-' || gs, 'APPROVED', now() - gs * interval '1 hour', "
                    f"now() FROM generate_series(1, {TRACKED}) AS gs"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.rolls (id, organization_id, inventory_item_id, lot_id, "
                    "roll_no, state, primary_milli, created_at, updated_at) "
                    f"SELECT {_roll_uuid('gs')}, :org, CAST(:item AS uuid), {_lot_uuid('gs')}, "
                    "'R-' || gs, 'ACTIVE', 1000, now() - gs * interval '1 hour', now() "
                    f"FROM generate_series(1, {TRACKED}) AS gs"
                ),
                params,
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.inventory_ledger (id, organization_id, move_id, "
                    "inventory_item_id, location_id, status, lot_id, roll_id, quantity_milli, "
                    "balance_after_milli, created_at) "
                    "SELECT gen_random_uuid(), :org, :move, :item, :loc, 'AVAILABLE', "
                    f"CASE WHEN gs <= {TRACKED_ROWS} THEN {_lot_uuid(f'gs % {TRACKED} + 1')} END, "
                    f"CASE WHEN gs <= {TRACKED_ROWS} THEN {_roll_uuid(f'gs % {TRACKED} + 1')} END, "
                    "1000, 1000, now() - gs * interval '2 minutes' "
                    f"FROM generate_series(1, {LEDGER_ROWS}) AS gs"
                ),
                params,
            )
            for table in (
                "inventory_ledger",
                "lots",
                "rolls",
                "inventory_moves",
                "inventory_items",
                "locations",
            ):
                await conn.execute(text(f"ANALYZE {SCHEMA}.{table}"))
        async with admin.connect() as conn:
            roll_id = (await conn.execute(text(f"SELECT {_roll_uuid('7')}"))).scalar_one()
            lot_id = (await conn.execute(text(f"SELECT {_lot_uuid('7')}"))).scalar_one()
        yield org_id, roll_id, lot_id
    finally:
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f"TRUNCATE {SCHEMA}.inventory_ledger, {SCHEMA}.rolls, {SCHEMA}.lots, "
                    f"{SCHEMA}.inventory_moves, {SCHEMA}.inventory_items, {SCHEMA}.locations, "
                    f"{SCHEMA}.warehouses CASCADE"
                )
            )
        await admin.dispose()


async def _plan_indexes(org_id: uuid.UUID, sql: str) -> set[str]:
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)"))
            raw = (await conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))).scalar_one()
    finally:
        await engine.dispose()
    plan = raw if isinstance(raw, list) else json.loads(raw)
    return _index_names(plan)


def _first_page(stmt: Any, sort_col: Any, id_col: Any) -> str:
    return _compiled(stmt.order_by(None).order_by(sort_col.desc(), id_col.desc()).limit(51))


async def test_every_keyset_first_page_is_served_by_the_index_built_for_it(
    ledger_at_scale,
) -> None:
    """One seeding, five statements (seeding is the cost, not the EXPLAINs): the org-wide ledger,
    one roll's ledger, one lot's ledger, the lot list and the roll list — each as the first page
    the route asks for, order and limit included."""
    org_id, roll_id, lot_id = ledger_at_scale
    ctx = ctx_for(org_id, "operations.inventory.stock.read", Scope.ORG)
    cases = {
        "ix_inventory_ledger_org_created": _first_page(
            sel.ledger_stmt(ctx), LedgerEntry.created_at, LedgerEntry.id
        ),
        "ix_inventory_ledger_org_roll_created": _first_page(
            sel.ledger_stmt(ctx, roll_id=roll_id), LedgerEntry.created_at, LedgerEntry.id
        ),
        "ix_inventory_ledger_org_lot_created": _first_page(
            sel.ledger_stmt(ctx, lot_id=lot_id), LedgerEntry.created_at, LedgerEntry.id
        ),
        "ix_lots_org_created": _first_page(sel.lots_stmt(ctx), Lot.created_at, Lot.id),
        "ix_rolls_org_created": _first_page(sel.rolls_stmt(ctx), Roll.created_at, Roll.id),
    }
    for index, sql in cases.items():
        used = await _plan_indexes(org_id, sql)
        assert index in used, f"{index} not used; the plan touched {sorted(used)}"
