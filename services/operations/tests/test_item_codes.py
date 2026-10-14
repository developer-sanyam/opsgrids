"""Auto-generated item codes: a blank SKU becomes `ITM-000001`, `ITM-000002`, ... per organization.

What is pinned: the HTTP create path and the bulk import both generate; typed codes still work and
stay unique; a generated code skips anything taken by hand; two sessions racing in one organization
never receive the same code; one organization's counter never moves another's; and a refused
import leaves the counter where it was.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import item_codes
from app.inventory import services as inv_services
from app.models import SCHEMA, InventoryItem, ItemCodeCounter
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, ctx_for, principal_for
from tests.test_bulk_import_api import count, item_row, run
from tests.tracking_fixtures import World, build_world

pytestmark = pytest.mark.integration

WRITE_PERM = "operations.inventory.item.write:ORG"
READ_PERM = "operations.inventory.item.read:ORG"
CREATE = "/api/v1/operations/inventory"


def body(**extra: object) -> dict[str, object]:
    return {
        "name": "Auto item",
        "category": "raw_materials",
        "warehouse": "Pune Plant",
        "uom": "MT",
        **extra,
    }


@pytest.fixture
async def maker():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=20,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def bound(maker, org: uuid.UUID) -> AsyncSession:  # type: ignore[no-untyped-def]
    s = maker()

    @event.listens_for(s.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return s


def test_format_is_prefix_and_six_digits() -> None:
    assert item_codes.format_item_code(1) == "ITM-000001"
    assert item_codes.format_item_code(42) == "ITM-000042"
    assert item_codes.format_item_code(1234567) == "ITM-1234567"


class TestCreateOverHttp:
    async def test_a_missing_sku_is_generated_and_returned(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            first = await client.post(CREATE, json=body())
            second = await client.post(CREATE, json=body(name="Another"))
        assert first.status_code == 201, first.text
        assert first.json()["sku"] == "ITM-000001"
        assert second.json()["sku"] == "ITM-000002"

    async def test_a_blank_or_null_sku_is_generated_too(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            blank = await client.post(CREATE, json=body(sku="   "))
            null = await client.post(CREATE, json=body(sku=None))
        assert (blank.json()["sku"], null.json()["sku"]) == ("ITM-000001", "ITM-000002")

    async def test_a_typed_sku_is_used_as_given_and_does_not_move_the_counter(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            typed = await client.post(CREATE, json=body(sku="MY-CODE-1"))
            generated = await client.post(CREATE, json=body())
        assert typed.json()["sku"] == "MY-CODE-1"
        assert generated.json()["sku"] == "ITM-000001"

    async def test_a_typed_duplicate_is_still_a_conflict(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            await client.post(CREATE, json=body(sku="DUP-1"))
            clash = await client.post(CREATE, json=body(sku="DUP-1"))
        assert clash.status_code == 409

    async def test_a_generated_code_skips_one_typed_by_hand(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            await client.post(CREATE, json=body(sku="ITM-000002"))
            a = await client.post(CREATE, json=body())
            b = await client.post(CREATE, json=body())
        assert (a.json()["sku"], b.json()["sku"]) == ("ITM-000001", "ITM-000003")

    async def test_a_crowded_range_is_jumped_not_walked(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            for n in (1, 2, 3, 4, 5, 6, 7, 8):
                await client.post(CREATE, json=body(sku=f"ITM-{n:06d}"))
            nxt = await client.post(CREATE, json=body())
        assert nxt.status_code == 201 and nxt.json()["sku"] == "ITM-000009"

    async def test_a_generated_code_skips_another_items_barcode(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            await client.post(CREATE, json=body(sku="HAS-BARCODE", barcode="ITM-000001"))
            made = await client.post(CREATE, json=body())
        assert made.json()["sku"] == "ITM-000002"

    async def test_an_unknown_field_is_still_rejected(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            response = await client.post(CREATE, json=body(code="X"))
        assert response.status_code == 422

    async def test_an_invalid_typed_sku_is_still_rejected(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE_PERM])):
            response = await client.post(CREATE, json=body(sku="has space"))
        assert response.status_code == 422


class TestAuthorization:
    async def test_a_reader_cannot_create_without_a_sku(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [READ_PERM])):
            response = await client.post(CREATE, json=body())
        assert response.status_code == 403

    async def test_an_anonymous_caller_is_refused(self, client, org_id, data) -> None:
        response = await client.post(CREATE, json=body())
        assert response.status_code == 401

    async def test_a_refused_create_does_not_consume_a_number(
        self, client, session, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [READ_PERM])):
            await client.post(CREATE, json=body())
        async with session.begin():
            counters = await session.scalar(select(func.count()).select_from(ItemCodeCounter))
        assert counters == 0


class TestRaceAndIsolation:
    async def _warehouse(self, maker, org: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
        s = bound(maker, org)
        try:
            async with s.begin():
                ctx = ctx_for(org, "operations.inventory.warehouse.write", Scope.ORG)
                await inv_services.create_warehouse(s, ctx, code="WH-RACE", name="Race Store")
        finally:
            await s.close()

    async def _create(self, maker, org: uuid.UUID, sku: str | None = None) -> str:  # type: ignore[no-untyped-def]
        s = bound(maker, org)
        try:
            async with s.begin():
                ctx = ctx_for(org, "operations.inventory.item.write", Scope.ORG)
                item = await inv_services.create_inventory_item(
                    s,
                    ctx,
                    sku=sku,
                    name="Racing item",
                    category="raw_materials",
                    warehouse="Race Store",
                    uom="kg",
                )
                return item.sku
        finally:
            await s.close()

    async def test_simultaneous_creates_never_share_a_code(self, maker, org_id) -> None:
        await self._warehouse(maker, org_id)
        await self._create(maker, org_id, "SEED-UOM")  # creates the unit once, outside the race
        codes = await asyncio.gather(*(self._create(maker, org_id) for _ in range(12)))
        assert sorted(codes) == [item_codes.format_item_code(n) for n in range(1, 13)]

    async def test_a_race_against_a_hand_typed_code_still_yields_distinct_codes(
        self, maker, org_id
    ) -> None:
        await self._warehouse(maker, org_id)
        await self._create(maker, org_id, "SEED-UOM")
        work = [self._create(maker, org_id) for _ in range(6)]
        work.append(self._create(maker, org_id, "ITM-000003"))
        codes = await asyncio.gather(*work)
        assert len(set(codes)) == len(codes) == 7

    async def test_one_organizations_counter_never_moves_anothers(self, maker, org_id) -> None:
        org_b = uuid.uuid4()
        await self._warehouse(maker, org_id)
        await self._warehouse(maker, org_b)
        await self._create(maker, org_id, "SEED-A")
        await self._create(maker, org_b, "SEED-B")
        a = [await self._create(maker, org_id) for _ in range(3)]
        b = [await self._create(maker, org_b)]
        assert a == ["ITM-000001", "ITM-000002", "ITM-000003"]
        assert b == ["ITM-000001"]
        assert await self._create(maker, org_id) == "ITM-000004"

    async def test_an_organization_cannot_see_another_organizations_counter(
        self, maker, org_id
    ) -> None:
        org_b = uuid.uuid4()
        await self._warehouse(maker, org_id)
        await self._create(maker, org_id)
        s = bound(maker, org_b)
        try:
            async with s.begin():
                rows = (await s.execute(select(ItemCodeCounter))).scalars().all()
        finally:
            await s.close()
        assert rows == []


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


class TestBulkImport:
    async def test_blank_codes_are_generated_and_typed_ones_kept(
        self, client, session, world
    ) -> None:
        rows = [item_row(""), item_row("TYPED-1"), item_row("")]
        response = await run(client, world.org_id, "items", rows, mode="commit", key="gen-1")
        assert response.status_code == 200, response.text
        out = response.json()
        assert out["committed"] is True and out["created"] == 3
        assert [r["key"] for r in out["rows"]] == ["ITM-000001", "TYPED-1", "ITM-000002"]
        async with session.begin():
            skus = set(
                (
                    await session.execute(
                        select(InventoryItem.sku).where(
                            InventoryItem.organization_id == world.org_id,
                            InventoryItem.sku.like("ITM-%") | (InventoryItem.sku == "TYPED-1"),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert skus == {"ITM-000001", "ITM-000002", "TYPED-1"}

    async def test_a_sku_column_may_be_left_out_entirely(self, client, world) -> None:
        row = item_row("")
        row.pop("sku")
        response = await run(client, world.org_id, "items", [row], mode="commit", key="gen-2")
        assert response.json()["rows"][0]["key"] == "ITM-000001"

    async def test_a_preview_shows_the_codes_a_commit_would_assign_and_keeps_none(
        self, client, session, world
    ) -> None:
        rows = [item_row(""), item_row("")]
        preview = (await run(client, world.org_id, "items", rows, mode="preview")).json()
        assert [r["key"] for r in preview["rows"]] == ["ITM-000001", "ITM-000002"]
        async with session.begin():
            counters = await session.scalar(select(func.count()).select_from(ItemCodeCounter))
        assert counters == 0
        commit = (await run(client, world.org_id, "items", rows, mode="commit", key="g3")).json()
        assert [r["key"] for r in commit["rows"]] == ["ITM-000001", "ITM-000002"]

    async def test_generated_codes_skip_existing_typed_codes(self, client, world) -> None:
        await run(client, world.org_id, "items", [item_row("ITM-000001")], mode="commit", key="a")
        rows = [item_row(""), item_row("")]
        out = (await run(client, world.org_id, "items", rows, mode="commit", key="b")).json()
        assert [r["key"] for r in out["rows"]] == ["ITM-000002", "ITM-000003"]

    async def test_one_bad_row_leaves_no_items_and_no_counter(self, client, session, world) -> None:
        before = await count(session, InventoryItem, world.org_id)
        rows = [item_row(""), item_row(""), item_row("", warehouse="No Such Store")]
        response = await run(client, world.org_id, "items", rows, mode="commit", key="bad")
        assert response.status_code == 422
        assert await count(session, InventoryItem, world.org_id) == before
        assert await count(session, ItemCodeCounter, world.org_id) == 0
        # A corrected file under the same key commits and starts from the first code again.
        good = await run(client, world.org_id, "items", rows[:2], mode="commit", key="bad")
        assert [r["key"] for r in good.json()["rows"]] == ["ITM-000001", "ITM-000002"]

    async def test_a_typed_duplicate_in_the_file_is_still_an_error(self, client, world) -> None:
        rows = [item_row("DUP-1"), item_row("DUP-1"), item_row("")]
        response = await run(client, world.org_id, "items", rows, mode="commit", key="d")
        assert response.status_code == 422
        assert [(e["row"], e["code"]) for e in response.json()["errors"]] == [
            (2, "duplicate_in_file")
        ]

    async def test_import_still_needs_its_permissions(self, client, world) -> None:
        response = await run(
            client,
            world.org_id,
            "items",
            [item_row("")],
            perms=[WRITE_PERM],
            mode="commit",
            key="n",
        )
        assert response.status_code == 403
