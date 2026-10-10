"""Bulk export (inventory Phase 9, ADR-0027): every inventory list as JSON or CSV, exact decimal
strings, never a formula, cross-tenant empty, authorization, and an audit row per export."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy import func, select

from app.inventory import bulk_export
from app.models import AuditLog, InventoryItem
from tests.helpers import as_principal, principal_for
from tests.tracking_fixtures import API, World, build_world

pytestmark = pytest.mark.integration

BASE = "/operations/inventory/data/export"
DATA_EXPORT = "operations.inventory.data.export:ORG"
READ = {
    "items": "operations.inventory.item.read:ORG",
    "locations": "operations.inventory.location.read:ORG",
    "lots": "operations.inventory.stock.read:ORG",
    "rolls": "operations.inventory.stock.read:ORG",
    "stock": "operations.inventory.stock.read:ORG",
    "ledger": "operations.inventory.stock.read:ORG",
}
ENTITIES = sorted(READ)


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def export(client, org_id, entity, *, perms=None, fmt="json"):  # type: ignore[no-untyped-def]
    held = perms if perms is not None else [DATA_EXPORT, READ[entity]]
    with as_principal(principal_for(org_id, held)):
        return await client.get(f"{API}{BASE}/{entity}", params={"format": fmt})


class TestContent:
    async def test_items(self, client, world) -> None:
        body = (await export(client, world.org_id, "items")).json()
        assert body["entity"] == "items" and body["truncated"] is False
        by_sku = {r["sku"]: r for r in body["rows"]}
        assert set(by_sku) == {"COT-180-NAVY", "DYE-RED-01", "BTN-14MM"}
        fabric = by_sku["COT-180-NAVY"]
        assert fabric["unit_value_minor"] == "12345" and fabric["track_roll"] == "true"
        assert fabric["on_hand_qty"] == "82.5" and fabric["id"] == str(world.t.fabric)
        assert set(body["columns"]) >= {"sku", "name", "barcode", "on_hand_qty"}

    async def test_lots_and_rolls_carry_exact_decimals(self, client, world) -> None:
        lots = (await export(client, world.org_id, "lots")).json()["rows"]
        assert lots[0]["lot_no"] == "LOT-A" and lots[0]["on_hand_qty"] == "82.5"
        rolls = (await export(client, world.org_id, "rolls")).json()["rows"]
        assert rolls[0]["roll_no"] == "R-1" and rolls[0]["primary_qty"] == "82.5"
        assert (rolls[0]["secondary_qty"], rolls[0]["secondary_uom"]) == ("14.8", "kg")
        assert rolls[0]["lot_no"] == "LOT-A"

    async def test_locations_name_their_warehouse_and_parent(self, client, world) -> None:
        rows = (await export(client, world.org_id, "locations")).json()["rows"]
        codes = {(r["warehouse_code"], r["code"]) for r in rows}
        assert ("WH-WEAVE", "BIN-B") in codes and ("WH-FIN", "DEFAULT") in codes

    async def test_stock_and_ledger(self, client, world) -> None:
        stock = (await export(client, world.org_id, "stock")).json()["rows"]
        assert len(stock) == 1
        assert (stock[0]["sku"], stock[0]["quantity"], stock[0]["roll_no"]) == (
            "COT-180-NAVY",
            "82.5",
            "R-1",
        )
        ledger = (await export(client, world.org_id, "ledger")).json()["rows"]
        assert len(ledger) == 1 and ledger[0]["kind"] == "opening"
        assert ledger[0]["quantity"] == "82.5" and ledger[0]["balance_after"] == "82.5"

    async def test_an_export_is_capped_and_says_so(self, client, world, monkeypatch) -> None:
        monkeypatch.setattr(bulk_export, "MAX_EXPORT_ROWS", 2)
        body = (await export(client, world.org_id, "items")).json()
        assert len(body["rows"]) == 2 and body["truncated"] is True

    async def test_an_export_writes_an_audit_row(self, client, session, world) -> None:
        await export(client, world.org_id, "lots")
        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == world.org_id,
                        AuditLog.action == "inventory.data.exported",
                    )
                )
            ).scalar_one()
        assert audit.after["entity"] == "lots" and audit.after["rows"] == 1


class TestCsv:
    async def test_csv_has_a_header_and_the_same_rows(self, client, world) -> None:
        response = await export(client, world.org_id, "rolls", fmt="csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "inventory-rolls.csv" in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert rows[0]["roll_no"] == "R-1" and rows[0]["primary_qty"] == "82.5"

    async def test_a_cell_is_never_a_formula(self, client, session, world) -> None:
        async with session.begin():
            await session.execute(
                InventoryItem.__table__.update()
                .where(InventoryItem.id == world.t.dye)
                .values(name='=HYPERLINK("http://evil","click")')
            )
        text = (await export(client, world.org_id, "items", fmt="csv")).text
        rows = {r["sku"]: r for r in csv.DictReader(io.StringIO(text))}
        assert rows["DYE-RED-01"]["name"].startswith("'=")

    def test_formula_guard_leaves_numbers_alone(self) -> None:
        assert bulk_export.csv_cell("-5") == "-5"
        assert bulk_export.csv_cell("-5.250") == "-5.250"
        assert bulk_export.csv_cell("=1+1") == "'=1+1"
        assert bulk_export.csv_cell("+91 98") == "'+91 98"
        assert bulk_export.csv_cell("@x") == "'@x"
        assert bulk_export.csv_cell("-cmd") == "'-cmd"
        assert bulk_export.csv_cell("plain") == "plain"
        assert bulk_export.csv_cell("") == ""

    async def test_a_negative_ledger_quantity_stays_a_number(self, client, session, world) -> None:
        from app.inventory import stock_ops
        from app.inventory.tracking_schemas import StockTransferIn

        async with session.begin():
            await stock_ops.transfer_stock(
                session,
                world.t.ctx,
                StockTransferIn(
                    inventory_item_id=world.t.fabric,
                    from_location_id=world.t.weaving,
                    to_location_id=world.bin_b,
                    quantity="10",
                    lot_id=world.lot,
                    roll_id=world.roll,
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        text = (await export(client, world.org_id, "ledger", fmt="csv")).text
        quantities = {r["quantity"] for r in csv.DictReader(io.StringIO(text))}
        assert "-10" in quantities or "-10.000" in quantities or "-10.0" in quantities


@pytest.mark.parametrize("entity", ENTITIES)
class TestAuthorization:
    async def test_no_permission_is_403(self, client, world, entity) -> None:
        for fmt in ("json", "csv"):
            assert (
                await export(client, world.org_id, entity, perms=[], fmt=fmt)
            ).status_code == 403

    async def test_export_without_the_lists_read_permission_is_403(
        self, client, session, world, entity
    ) -> None:
        response = await export(client, world.org_id, entity, perms=[DATA_EXPORT])
        assert response.status_code == 403
        assert READ[entity].split(":")[0] in response.json()["detail"]
        async with session.begin():
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "inventory.data.exported")
                )
            ).scalar_one()
        assert audits == 0

    async def test_the_read_permission_alone_is_403(self, client, world, entity) -> None:
        response = await export(client, world.org_id, entity, perms=[READ[entity]])
        assert response.status_code == 403

    async def test_every_other_permission_is_403(self, client, world, entity) -> None:
        others = [
            "operations.inventory.data.import:ORG",
            "operations.inventory.stock.adjust:ORG",
            "operations.inventory.stock.transfer:ORG",
        ] + [p for e, p in READ.items() if e != entity and p != READ[entity]]
        assert (await export(client, world.org_id, entity, perms=others)).status_code == 403

    async def test_another_tenant_gets_an_empty_export_never_this_tenants_rows(
        self, client, world, entity
    ) -> None:
        response = await export(client, uuid.uuid4(), entity)
        assert response.status_code == 200
        assert response.json()["rows"] == []

    async def test_each_tenant_exports_only_its_own_rows(
        self, client, world, admin_session, entity
    ) -> None:
        other = await build_world(admin_session, uuid.uuid4())
        mine = (await export(client, world.org_id, entity)).json()["rows"]
        theirs = (await export(client, other.org_id, entity)).json()["rows"]
        assert mine and theirs
        ids_mine = {r.get("id") for r in mine if r.get("id")}
        ids_theirs = {r.get("id") for r in theirs if r.get("id")}
        assert not ids_mine & ids_theirs


async def test_an_unknown_entity_is_422(client, world) -> None:
    with as_principal(principal_for(world.org_id, [DATA_EXPORT])):
        response = await client.get(f"{API}{BASE}/suppliers")
    assert response.status_code == 422
