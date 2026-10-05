# ruff: noqa: S608 — every f-string here interpolates only `SCHEMA` and this module's own constants.
"""The plans behind find-stock and the put-away capture lookup (CLAUDE.md: every index leads with
`organization_id`; `test_tracking_query_plans.py` for the method).

Seeded at a realistic size and `ANALYZE`d, the **exact** statement the selector compiles is
`EXPLAIN`ed as the app role with the tenant GUC set — RLS's own qual and the selector's explicit
`organization_id` filter both in the query the planner sees — and the index built for it must be the
one it reaches for, with no sequential scan of the big table. `locate` runs on every "where is this
item" a storekeeper types; a quiet fall back to scanning every balance of the tenant would be a
production incident at a few hundred thousand rows, and nothing else in the suite would notice.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.inventory import putaway_selectors
from app.models import SCHEMA, PutawayCapture
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

ITEMS = 400
LOCATIONS = 150  # every item has a balance in every location: 60,000 balance rows
CAPTURES = 30_000


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _compiled(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _uuid(kind: str, expr: str) -> str:
    return f"md5('{kind}' || ({expr})::text)::uuid"


def _walk(plan: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(plan, dict):
        if "Node Type" in plan:
            nodes.append(plan)
        for value in plan.values():
            nodes += _walk(value)
    elif isinstance(plan, list):
        for value in plan:
            nodes += _walk(value)
    return nodes


@pytest.fixture
async def at_scale(session):  # type: ignore[no-untyped-def]
    """One tenant with `ITEMS x LOCATIONS` balances and `CAPTURES` captures — bulk SQL, then
    `ANALYZE`. Depends on `session` only for its TRUNCATE-at-start. Yields `(org, item, move)`."""
    org = uuid.uuid4()
    wh = uuid.uuid4()
    admin = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    try:
        async with admin.begin() as conn:
            params = {"org": org, "wh": wh}
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                                     created_at, updated_at)
                    VALUES (:wh, :org, 'W', 'W', '', now(), now())
                    """
                ),
                params,
            )
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code,
                                                    name, is_default, created_at, updated_at)
                    SELECT {_uuid("loc", "gs")}, :org, :wh, 'bin', 'L-' || gs, '', false, now(),
                           now()
                      FROM generate_series(1, {LOCATIONS}) AS gs
                    """
                ),
                params,
            )
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.inventory_items
                        (id, organization_id, sku, name, category, warehouse, reorder_point, uom,
                         unit_value_minor, currency, hsn_code, track_lot, track_roll, track_expiry,
                         track_quality, is_active, created_at, updated_at)
                    SELECT {_uuid("item", "gs")}, :org, 'S-' || gs, 'Item ' || gs, 'raw_materials',
                           '', 0, 'kg', 0, 'INR', '', false, false, false, false, true, now(), now()
                      FROM generate_series(1, {ITEMS}) AS gs
                    """
                ),
                params,
            )
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.inventory_balances
                        (id, organization_id, inventory_item_id, location_id, status,
                         quantity_milli, reserved_milli, updated_at)
                    SELECT gen_random_uuid(), :org, {_uuid("item", "i")}, {_uuid("loc", "l")},
                           'AVAILABLE', 1000, 0, now()
                      FROM generate_series(1, {ITEMS}) AS i, generate_series(1, {LOCATIONS}) AS l
                    """
                ),
                params,
            )
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.inventory_moves
                        (id, organization_id, kind, reference_type, note, occurred_on, created_at)
                    SELECT {_uuid("move", "gs")}, :org, 'relocation', 'putaway', '', '2026-10-01',
                           now()
                      FROM generate_series(1, {CAPTURES}) AS gs
                    """
                ),
                params,
            )
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.putaway_captures
                        (id, organization_id, move_id, location_id, created_by, captured_at)
                    SELECT gen_random_uuid(), :org, {_uuid("move", "gs")},
                           {_uuid("loc", f"gs % {LOCATIONS} + 1")}, gen_random_uuid(), now()
                      FROM generate_series(1, {CAPTURES}) AS gs
                    """
                ),
                params,
            )
            for table in (
                "inventory_balances",
                "inventory_items",
                "inventory_moves",
                "putaway_captures",
                "locations",
            ):
                await conn.execute(text(f"ANALYZE {SCHEMA}.{table}"))
        async with admin.connect() as conn:
            item = (await conn.execute(text(f"SELECT {_uuid('item', '7')}"))).scalar_one()
            move = (await conn.execute(text(f"SELECT {_uuid('move', '7')}"))).scalar_one()
        yield org, item, move
    finally:
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f"TRUNCATE {SCHEMA}.putaway_captures, {SCHEMA}.inventory_balances, "
                    f"{SCHEMA}.inventory_moves, {SCHEMA}.inventory_items, {SCHEMA}.locations, "
                    f"{SCHEMA}.warehouses CASCADE"
                )
            )
        await admin.dispose()


async def explain(org: uuid.UUID, sql: str) -> list[dict[str, Any]]:
    """The plan's nodes, as the **app** role with the tenant bound — what production runs."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org}', true)"))
            raw = (await conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))).scalar_one()
    finally:
        await engine.dispose()
    return _walk(raw if isinstance(raw, list) else json.loads(raw))


def indexes_used(nodes: list[dict[str, Any]]) -> set[str]:
    return {n["Index Name"] for n in nodes if "Index Name" in n}


def seq_scans(nodes: list[dict[str, Any]]) -> set[str]:
    return {n["Relation Name"] for n in nodes if n["Node Type"] == "Seq Scan"}


async def test_locate_reads_one_items_balances_through_the_tenant_leading_bucket_index(
    at_scale,
) -> None:
    org, item, _ = at_scale
    ctx = ctx_for(org, "operations.inventory.stock.read", Scope.ORG)
    sql = _compiled(putaway_selectors.locate_stmt(ctx, item_id=item))
    nodes = await explain(org, sql)
    assert "uq_inventory_balances_bucket" in indexes_used(nodes), indexes_used(nodes)
    assert "inventory_balances" not in seq_scans(nodes), seq_scans(nodes)


async def test_locate_with_a_roll_filter_and_a_warehouse_filter_still_avoids_the_big_scan(
    at_scale,
) -> None:
    org, item, _ = at_scale
    ctx = ctx_for(org, "operations.inventory.stock.read", Scope.ORG)
    stmt = putaway_selectors.locate_stmt(
        ctx, item_id=item, roll_id=uuid.uuid4(), warehouse_id=uuid.uuid4()
    )
    nodes = await explain(org, _compiled(stmt))
    assert "inventory_balances" not in seq_scans(nodes), seq_scans(nodes)


async def test_the_capture_lookup_by_move_uses_the_unique_tenant_and_move_index(at_scale) -> None:
    org, _, move = at_scale
    stmt = select(PutawayCapture).where(
        PutawayCapture.organization_id == org, PutawayCapture.move_id == move
    )
    nodes = await explain(org, _compiled(stmt))
    assert "uq_putaway_captures_org_move" in indexes_used(nodes), indexes_used(nodes)
    assert "putaway_captures" not in seq_scans(nodes)


async def test_the_captures_of_one_bin_are_read_through_the_tenant_and_location_index(
    at_scale,
) -> None:
    """The shape of a "who put stock into this bin" question."""
    org, _, _ = at_scale
    sql = (
        f"SELECT * FROM {SCHEMA}.putaway_captures "
        f"WHERE organization_id = '{org}' AND location_id = {_uuid('loc', '9')}"
    )
    nodes = await explain(org, sql)
    assert "ix_putaway_captures_org_location" in indexes_used(nodes), indexes_used(nodes)


async def test_the_control_the_guards_can_fire(at_scale) -> None:
    """The guards above are only worth something if they can fire. A query on columns the bucket
    index does not lead with is not served by it, and a sequential scan is reported as one."""
    org, _, _ = at_scale
    unindexed = await explain(
        org,
        f"SELECT * FROM {SCHEMA}.inventory_balances "
        "WHERE quantity_milli = 1000 AND reserved_milli = 0",
    )
    assert "uq_inventory_balances_bucket" not in indexes_used(unindexed)
    plan = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "inventory_balances", "Plans": []}}]
    assert seq_scans(_walk(plan)) == {"inventory_balances"}
