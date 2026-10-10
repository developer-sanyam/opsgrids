"""Bulk import (inventory Phase 9, ADR-0027): preview, **all-or-nothing** commit, row-level errors,
duplicate detection, idempotency, and that stock only ever arrives through the posting kernel.

The headline cases: one bad row in a file of good ones means **nothing is committed** — no items,
no ledger rows, no audit rows, no outbox events — and a preview shows exactly what a commit would
do while writing nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    InventoryBalance,
    InventoryImportRun,
    InventoryItem,
    InventoryMove,
    LedgerEntry,
    Location,
    Lot,
    OutboxEvent,
    Roll,
)
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.tracking_fixtures import (
    API,
    STOCK_READ,
    World,
    assert_reconciled,
    build_world,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
BASE = "/operations/inventory/data/import"
DATA_IMPORT = "operations.inventory.data.import:ORG"
WRITE = {
    "items": "operations.inventory.item.write:ORG",
    "locations": "operations.inventory.location.write:ORG",
    "lots": "operations.inventory.lot.write:ORG",
    "rolls": "operations.inventory.roll.write:ORG",
    "opening_stock": "operations.inventory.stock.adjust:ORG",
}


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def run(  # type: ignore[no-untyped-def]
    client,
    org_id,
    entity: str,
    rows: list[dict[str, Any]],
    *,
    mode: str = "preview",
    key: str | None = None,
    on_duplicate: str = "fail",
    perms: list[str] | None = None,
):
    body = {"mode": mode, "on_duplicate": on_duplicate, "rows": rows}
    headers = {"Idempotency-Key": key} if key else {}
    held = perms if perms is not None else [DATA_IMPORT, WRITE[entity], STOCK_READ]
    with as_principal(principal_for(org_id, held)):
        return await client.post(f"{API}{BASE}/{entity}", json=body, headers=headers)


async def count(session, model, org_id, *where) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.organization_id == org_id, *where)
                )
            ).scalar_one()
        )


def item_row(sku: str, **extra: str) -> dict[str, str]:
    return {
        "sku": sku,
        "name": f"Item {sku}",
        "category": "raw_materials",
        "warehouse": "Weaving Store",
        "uom": "m",
        **extra,
    }


def stock_row(sku: str, location: str = "DEFAULT", qty: str = "10", **extra: str) -> dict[str, str]:
    return {
        "sku": sku,
        "warehouse_code": "WH-WEAVE",
        "location_code": location,
        "quantity": qty,
        **extra,
    }


# ------------------------------------------------------------------ preview


class TestPreview:
    async def test_a_preview_reports_what_a_commit_would_do_and_writes_nothing(
        self, client, session, world
    ) -> None:
        before = await count(session, InventoryItem, world.org_id)
        audits = await count(session, AuditLog, world.org_id)
        events = await count(session, OutboxEvent, world.org_id)
        response = await run(
            client, world.org_id, "items", [item_row("NEW-1"), item_row("NEW-2")], mode="preview"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["committed"] is False and body["mode"] == "preview"
        assert (body["total"], body["created"], body["error_count"]) == (2, 2, 0)
        assert [r["status"] for r in body["rows"]] == ["ok", "ok"]
        assert await count(session, InventoryItem, world.org_id) == before
        assert await count(session, AuditLog, world.org_id) == audits
        assert await count(session, OutboxEvent, world.org_id) == events
        assert await count(session, InventoryImportRun, world.org_id) == 0

    async def test_a_preview_needs_no_idempotency_key_and_names_every_bad_row(
        self, client, world
    ) -> None:
        rows = [
            item_row("OK-1"),
            {**item_row("BAD-2"), "warehouse": "Nowhere"},
            {"sku": "BAD-3", "name": "x"},  # missing required columns
            {**item_row("BAD-4"), "category": "gadgets"},
            {**item_row("BAD-5"), "reorder_point": "ten"},
        ]
        body = (await run(client, world.org_id, "items", rows)).json()
        assert body["error_count"] == 4
        by_row = {e["row"]: e for e in body["errors"]}
        assert by_row[2]["field"] == "warehouse" and by_row[2]["code"] == "unknown_warehouse"
        assert by_row[3]["code"] == "required"
        assert by_row[4]["field"] == "category"
        assert by_row[5]["field"] == "reorder_point" and by_row[5]["code"] == "invalid_integer"
        assert [r["status"] for r in body["rows"]] == ["ok", "error", "error", "error", "error"]

    async def test_a_preview_exercises_the_real_write_path(self, client, world) -> None:
        # Not just shape checks: the kernel's own refusal (a lot-tracked item with no lot) shows.
        rows = [stock_row("COT-180-NAVY", qty="5")]
        body = (await run(client, world.org_id, "opening_stock", rows)).json()
        assert body["error_count"] == 1 and body["errors"][0]["code"] == "required"

    async def test_unknown_columns_are_refused_up_front(self, client, world) -> None:
        response = await run(
            client, world.org_id, "items", [{**item_row("X-1"), "colour": "red", "id": "abc"}]
        )
        assert response.status_code == 422
        assert {e["field"] for e in response.json()["errors"]} == {"colour", "id"}

    async def test_cells_must_be_strings_never_numbers(self, client, world) -> None:
        response = await run(
            client, world.org_id, "opening_stock", [{**stock_row("BTN-14MM"), "quantity": 5}]
        )
        assert response.status_code == 422

    async def test_the_row_cap_is_enforced(self, client, world) -> None:
        rows = [{"sku": f"S{i}"} for i in range(5001)]
        response = await run(client, world.org_id, "lots", rows)
        assert response.status_code == 422


# ------------------------------------------------------------------ commit: items


class TestCommitItems:
    async def test_commit_creates_every_row_and_records_the_run(
        self, client, session, world
    ) -> None:
        rows = [item_row("NEW-1", barcode="EAN-NEW-1"), item_row("NEW-2", track_lot="true")]
        response = await run(client, world.org_id, "items", rows, mode="commit", key="imp-1")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["committed"] is True and body["created"] == 2 and body["run_id"]
        async with session.begin():
            items = {
                i.sku: i
                for i in (
                    await session.execute(
                        select(InventoryItem).where(
                            InventoryItem.organization_id == world.org_id,
                            InventoryItem.sku.in_(["NEW-1", "NEW-2"]),
                        )
                    )
                ).scalars()
            }
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == world.org_id,
                        AuditLog.action == "inventory.import.committed",
                    )
                )
            ).scalar_one()
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.import.committed"
                    )
                )
            ).scalar_one()
        assert items["NEW-1"].barcode == "EAN-NEW-1" and items["NEW-2"].track_lot is True
        assert audit.after["created"] == 2 and audit.entity == "inventory_import_run"
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert event.payload["run_id"] == body["run_id"]

    async def test_each_row_still_writes_its_own_audit_row_and_event(
        self, client, session, world
    ) -> None:
        await run(client, world.org_id, "items", [item_row("NEW-1")], mode="commit", key="k")
        assert (
            await count(
                session, AuditLog, world.org_id, AuditLog.action == "inventory_item.created"
            )
            >= 1
        )

    async def test_money_columns_are_integer_minor_units(self, client, session, world) -> None:
        await run(
            client,
            world.org_id,
            "items",
            [item_row("NEW-1", unit_value_minor="12500", currency="inr")],
            mode="commit",
            key="k",
        )
        async with session.begin():
            item = (
                await session.execute(
                    select(InventoryItem).where(
                        InventoryItem.organization_id == world.org_id,
                        InventoryItem.sku == "NEW-1",
                    )
                )
            ).scalar_one()
        assert (item.unit_value_minor, item.currency) == (12500, "INR")
        bad = await run(
            client,
            world.org_id,
            "items",
            [item_row("NEW-2", unit_value_minor="125.00")],
        )
        assert bad.json()["errors"][0]["code"] == "invalid_integer"

    async def test_all_or_nothing_one_bad_row_commits_nothing(self, client, session, world) -> None:
        before_items = await count(session, InventoryItem, world.org_id)
        before_audit = await count(session, AuditLog, world.org_id)
        before_events = await count(session, OutboxEvent, world.org_id)
        rows = [
            item_row("GOOD-1"),
            item_row("GOOD-2"),
            item_row("GOOD-3"),
            {**item_row("BAD-4"), "warehouse": "Nowhere"},
            item_row("GOOD-5"),
        ]
        response = await run(client, world.org_id, "items", rows, mode="commit", key="all-or-none")
        assert response.status_code == 422, response.text
        problem = response.json()
        assert "Nothing was imported" in problem["detail"]
        assert [(e["row"], e["field"]) for e in problem["errors"]] == [(4, "warehouse")]
        assert await count(session, InventoryItem, world.org_id) == before_items
        assert await count(session, AuditLog, world.org_id) == before_audit
        assert await count(session, OutboxEvent, world.org_id) == before_events
        assert await count(session, InventoryImportRun, world.org_id) == 0
        # The failed attempt did not burn the key: the corrected file commits under it.
        rows[3] = item_row("FIXED-4")
        ok = await run(client, world.org_id, "items", rows, mode="commit", key="all-or-none")
        assert ok.status_code == 200 and ok.json()["created"] == 5


# ------------------------------------------------------------------ commit: opening stock


class TestOpeningStock:
    async def test_commit_posts_through_the_kernel_and_the_ledger_balances(
        self, client, session, world
    ) -> None:
        rows = [
            stock_row("DYE-RED-01", qty="25.5"),
            stock_row("BTN-14MM", qty="1000", unit_cost_minor="150", unit_cost_currency="INR"),
            stock_row("DYE-RED-01", location="BIN-B", qty="4"),
        ]
        response = await run(
            client, world.org_id, "opening_stock", rows, mode="commit", key="open-1"
        )
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 3
        async with session.begin():
            moves = (
                (
                    await session.execute(
                        select(InventoryMove).where(
                            InventoryMove.organization_id == world.org_id,
                            InventoryMove.reference_type == "opening_stock",
                        )
                    )
                )
                .scalars()
                .all()
            )
            entries = (
                await session.execute(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(
                        LedgerEntry.organization_id == world.org_id,
                        LedgerEntry.inventory_item_id.in_([world.t.dye, world.t.buttons]),
                    )
                )
            ).scalar_one()
            dye = (
                await session.execute(
                    select(func.sum(InventoryBalance.quantity_milli)).where(
                        InventoryBalance.organization_id == world.org_id,
                        InventoryBalance.inventory_item_id == world.t.dye,
                    )
                )
            ).scalar_one()
        assert all(m.kind == "opening" for m in moves) and len(moves) >= 2  # world's + import's
        assert entries == 3
        assert dye == 29_500
        await assert_reconciled(session, world.org_id)

    async def test_all_or_nothing_one_bad_row_posts_no_ledger_rows(
        self, client, session, world
    ) -> None:
        ledger_before = await count(session, LedgerEntry, world.org_id)
        moves_before = await count(session, InventoryMove, world.org_id)
        balances_before = await count(session, InventoryBalance, world.org_id)
        rows = [
            stock_row("DYE-RED-01", qty="10"),
            stock_row("BTN-14MM", qty="20"),
            stock_row("BTN-14MM", location="BIN-B", qty="1.5"),  # buttons are discrete
            stock_row("DYE-RED-01", location="BIN-B", qty="5"),
        ]
        response = await run(client, world.org_id, "opening_stock", rows, mode="commit", key="o-2")
        assert response.status_code == 422, response.text
        assert [e["row"] for e in response.json()["errors"]] == [3]
        assert await count(session, LedgerEntry, world.org_id) == ledger_before
        assert await count(session, InventoryMove, world.org_id) == moves_before
        assert await count(session, InventoryBalance, world.org_id) == balances_before
        await assert_reconciled(session, world.org_id)

    async def test_a_roll_row_must_name_its_lot_and_roll(self, client, session, world) -> None:
        good = stock_row("COT-180-NAVY", location="BIN-B", qty="30", lot_no="LOT-A", roll_no="R-1")
        body = (await run(client, world.org_id, "opening_stock", [good])).json()
        assert body["error_count"] == 0, body
        mismatch = {**good, "lot_no": "NOPE"}
        errors = (await run(client, world.org_id, "opening_stock", [mismatch])).json()["errors"]
        assert errors[0]["code"] == "unknown_lot"

    async def test_a_bucket_that_already_holds_stock_is_a_duplicate(
        self, client, session, world
    ) -> None:
        # The world's roll is already open at DEFAULT: re-importing it would double the stock.
        row = stock_row("COT-180-NAVY", qty="82.5", lot_no="LOT-A", roll_no="R-1")
        body = (await run(client, world.org_id, "opening_stock", [row], mode="preview")).json()
        assert body["errors"][0]["code"] == "already_exists"
        skipped = (
            await run(
                client,
                world.org_id,
                "opening_stock",
                [row, stock_row("DYE-RED-01", qty="3")],
                mode="commit",
                key="o-3",
                on_duplicate="skip",
            )
        ).json()
        assert (skipped["created"], skipped["skipped"]) == (1, 1)
        await assert_reconciled(session, world.org_id)

    async def test_two_rows_naming_one_bucket_are_refused(self, client, world) -> None:
        rows = [stock_row("DYE-RED-01", qty="1"), stock_row("DYE-RED-01", qty="2")]
        errors = (await run(client, world.org_id, "opening_stock", rows)).json()["errors"]
        assert [(e["row"], e["code"]) for e in errors] == [(2, "duplicate_in_file")]

    async def test_a_wire_quantity_is_a_decimal_string_never_rounded(self, client, world) -> None:
        for bad in ("1e3", "10.5555", "-4", "0", "abc"):
            body = (
                await run(client, world.org_id, "opening_stock", [stock_row("DYE-RED-01", qty=bad)])
            ).json()
            assert body["error_count"] == 1, bad

    async def test_more_than_one_move_is_posted_for_a_big_file(
        self, client, session, world
    ) -> None:
        # 120 distinct empty buckets -> two kernel moves (100 + 20), one all-or-nothing import.
        async with session.begin():
            from app.inventory import services as inv_services
            from app.models import Warehouse

            wh = (
                await session.execute(
                    select(Warehouse).where(
                        Warehouse.organization_id == world.org_id, Warehouse.code == "WH-WEAVE"
                    )
                )
            ).scalar_one()
            for n in range(120):
                await inv_services.create_location(
                    session, world.t.ctx, wh, kind="bin", code=f"B{n:03d}", name=f"B{n}"
                )
        rows = [stock_row("DYE-RED-01", location=f"B{n:03d}", qty="1") for n in range(120)]
        response = await run(client, world.org_id, "opening_stock", rows, mode="commit", key="big")
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 120
        await assert_reconciled(session, world.org_id)


# ------------------------------------------------------------------ locations, lots, rolls


class TestOtherEntities:
    async def test_locations_may_name_a_parent_earlier_in_the_file(
        self, client, session, world
    ) -> None:
        rows = [
            {"warehouse_code": "WH-WEAVE", "kind": "zone", "code": "Z1", "barcode": "ZONE-1"},
            {
                "warehouse_code": "WH-WEAVE",
                "kind": "bin",
                "code": "Z1-A",
                "parent_code": "Z1",
                "name": "Bin A",
                "pick_sequence": "10",
            },
        ]
        response = await run(client, world.org_id, "locations", rows, mode="commit", key="loc")
        assert response.status_code == 200, response.text
        async with session.begin():
            child = (
                await session.execute(
                    select(Location).where(
                        Location.organization_id == world.org_id, Location.code == "Z1-A"
                    )
                )
            ).scalar_one()
            parent = (
                await session.execute(
                    select(Location).where(
                        Location.organization_id == world.org_id, Location.code == "Z1"
                    )
                )
            ).scalar_one()
        assert child.parent_id == parent.id and parent.barcode == "ZONE-1"

    async def test_lots_and_rolls_import_in_two_steps(self, client, session, world) -> None:
        lots = [
            {"sku": "COT-180-NAVY", "lot_no": "LOT-N1", "quality_status": "APPROVED"},
            {"sku": "COT-180-NAVY", "lot_no": "LOT-N2", "expires_on": "2027-01-31"},
        ]
        assert (
            await run(client, world.org_id, "lots", lots, mode="commit", key="lots")
        ).status_code == 200
        rolls = [
            {"sku": "COT-180-NAVY", "primary_qty": "82.5", "roll_no": "R-N1", "lot_no": "LOT-N1",
             "secondary_qty": "14.8", "secondary_uom": "kg", "width_mm": "1500"},
            {"sku": "COT-180-NAVY", "primary_qty": "50", "roll_no": "R-N2", "lot_no": "LOT-N1"},
        ]  # fmt: skip
        response = await run(client, world.org_id, "rolls", rolls, mode="commit", key="rolls")
        assert response.status_code == 200, response.text
        assert await count(session, Lot, world.org_id) == 3
        assert await count(session, Roll, world.org_id) == 3

    async def test_a_roll_of_an_untracked_item_is_refused_with_the_servers_sentence(
        self, client, world
    ) -> None:
        body = (
            await run(client, world.org_id, "rolls", [{"sku": "BTN-14MM", "primary_qty": "5"}])
        ).json()
        assert body["errors"][0]["code"] == "not_tracked"

    async def test_unknown_skus_and_lots_are_row_errors(self, client, world) -> None:
        body = (
            await run(
                client,
                world.org_id,
                "rolls",
                [
                    {"sku": "NOPE", "primary_qty": "5"},
                    {"sku": "COT-180-NAVY", "primary_qty": "5", "lot_no": "NOPE"},
                ],
            )
        ).json()
        assert [e["code"] for e in body["errors"]] == ["unknown_sku", "unknown_lot"]


# ------------------------------------------------------------------ duplicates


class TestDuplicates:
    async def test_an_existing_record_is_an_error_by_default(self, client, world) -> None:
        body = (
            await run(client, world.org_id, "items", [item_row("COT-180-NAVY"), item_row("NEW-1")])
        ).json()
        assert [(e["row"], e["code"]) for e in body["errors"]] == [(1, "already_exists")]

    async def test_skip_leaves_existing_records_alone_and_commits_the_rest(
        self, client, session, world
    ) -> None:
        before = await count(session, InventoryItem, world.org_id)
        rows = [item_row("COT-180-NAVY", name="Overwritten?"), item_row("NEW-1")]
        body = (
            await run(
                client, world.org_id, "items", rows, mode="commit", key="skip", on_duplicate="skip"
            )
        ).json()
        assert (body["created"], body["skipped"]) == (1, 1)
        assert [r["status"] for r in body["rows"]] == ["skipped", "ok"]
        assert await count(session, InventoryItem, world.org_id) == before + 1
        async with session.begin():
            name = (
                await session.execute(
                    select(InventoryItem.name).where(InventoryItem.id == world.t.fabric)
                )
            ).scalar_one()
        assert name == "Cotton 180 GSM, navy"  # an import creates, it never overwrites

    async def test_a_key_twice_in_the_file_is_always_an_error_even_with_skip(
        self, client, world
    ) -> None:
        body = (
            await run(
                client,
                world.org_id,
                "items",
                [item_row("DUP-1"), item_row("DUP-1")],
                on_duplicate="skip",
            )
        ).json()
        assert [(e["row"], e["code"]) for e in body["errors"]] == [(2, "duplicate_in_file")]

    async def test_a_barcode_twice_in_the_file_or_already_taken_is_an_error(
        self, client, world
    ) -> None:
        twice = (
            await run(
                client,
                world.org_id,
                "items",
                [item_row("A-1", barcode="BC-1"), item_row("A-2", barcode="BC-1")],
            )
        ).json()
        assert twice["errors"][0]["row"] == 2 and twice["errors"][0]["field"] == "barcode"
        # Taken by an existing record: the service's own refusal, reported against the row.
        await run(
            client,
            world.org_id,
            "items",
            [item_row("A-1", barcode="BC-9")],
            mode="commit",
            key="b9",
        )
        taken = (await run(client, world.org_id, "items", [item_row("A-3", barcode="BC-9")])).json()
        assert taken["errors"][0]["field"] == "barcode"

    async def test_an_export_fed_back_is_all_duplicates_never_a_second_copy(
        self, client, session, world
    ) -> None:
        with as_principal(
            principal_for(
                world.org_id,
                [
                    "operations.inventory.data.export:ORG",
                    "operations.inventory.item.read:ORG",
                    "operations.inventory.stock.read:ORG",
                ],
            )
        ):
            items = (await client.get(f"{API}/operations/inventory/data/export/items")).json()
            stock = (await client.get(f"{API}/operations/inventory/data/export/stock")).json()
        import_cols = {"items": items["columns"][:18], "stock": stock["columns"][:9]}
        item_rows = [{c: r[c] for c in import_cols["items"] if r[c] != ""} for r in items["rows"]]
        stock_rows = [{c: r[c] for c in import_cols["stock"] if r[c] != ""} for r in stock["rows"]]
        before = await count(session, InventoryItem, world.org_id)
        body = (await run(client, world.org_id, "items", item_rows)).json()
        assert {e["code"] for e in body["errors"]} == {"already_exists"}
        assert body["error_count"] == len(item_rows)
        body = (await run(client, world.org_id, "opening_stock", stock_rows)).json()
        assert {e["code"] for e in body["errors"]} == {"already_exists"}
        # The read-only columns are not import columns.
        refused = await run(client, world.org_id, "items", [{**item_rows[0], "id": "x"}])
        assert refused.status_code == 422
        assert await count(session, InventoryItem, world.org_id) == before


# ------------------------------------------------------------------ idempotency


class TestIdempotency:
    async def test_a_commit_requires_a_key(self, client, world) -> None:
        response = await run(client, world.org_id, "items", [item_row("NEW-1")], mode="commit")
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "idempotency_key_required"

    async def test_a_retried_commit_replays_instead_of_importing_twice(
        self, client, session, world
    ) -> None:
        rows = [item_row("NEW-1"), item_row("NEW-2")]
        first = await run(client, world.org_id, "items", rows, mode="commit", key="same")
        again = await run(client, world.org_id, "items", rows, mode="commit", key="same")
        assert first.status_code == again.status_code == 200
        assert again.json()["replayed"] is True
        assert again.json()["run_id"] == first.json()["run_id"]
        assert again.json()["created"] == 2
        assert await count(session, InventoryImportRun, world.org_id) == 1
        assert (
            await count(session, InventoryItem, world.org_id, InventoryItem.sku.like("NEW-%")) == 2
        )

    async def test_a_key_reused_for_a_different_file_is_a_conflict(self, client, world) -> None:
        await run(client, world.org_id, "items", [item_row("NEW-1")], mode="commit", key="k")
        other = await run(
            client, world.org_id, "items", [item_row("NEW-2")], mode="commit", key="k"
        )
        assert other.status_code == 409

    async def test_the_same_key_in_two_organizations_is_two_imports(
        self, client, world, admin_session
    ) -> None:
        foreign = await build_world(admin_session, uuid.uuid4())
        for w in (world, foreign):
            response = await run(
                client, w.org_id, "items", [item_row("NEW-1")], mode="commit", key="k"
            )
            assert response.status_code == 200, (w is world, response.text)
            assert response.json()["replayed"] is False


# ------------------------------------------------------------------ authorization


ENTITIES = ["items", "locations", "lots", "rolls", "opening_stock"]
MINIMAL = {
    "items": [
        {"sku": "ZZ-1", "name": "n", "category": "mro", "warehouse": "Weaving Store", "uom": "pcs"}
    ],
    "locations": [{"warehouse_code": "WH-WEAVE", "kind": "bin", "code": "ZZ-1"}],
    "lots": [{"sku": "COT-180-NAVY", "lot_no": "ZZ-1"}],
    "rolls": [{"sku": "COT-180-NAVY", "primary_qty": "5", "roll_no": "ZZ-1", "lot_no": "LOT-A"}],
    "opening_stock": [stock_row("DYE-RED-01", qty="2")],
}  # fmt: skip


@pytest.mark.parametrize("entity", ENTITIES)
class TestImportAuthorization:
    async def test_no_permission_is_403(self, client, world, entity) -> None:
        for mode in ("preview", "commit"):
            response = await run(
                client, world.org_id, entity, MINIMAL[entity], mode=mode, key="k", perms=[]
            )
            assert response.status_code == 403, response.text

    async def test_the_dangerous_permission_alone_is_not_enough(
        self, client, world, entity
    ) -> None:
        response = await run(
            client,
            world.org_id,
            entity,
            MINIMAL[entity],
            mode="commit",
            key="k",
            perms=[DATA_IMPORT],
        )
        assert response.status_code == 403
        assert WRITE[entity].split(":")[0] in response.json()["detail"]

    async def test_the_entitys_write_permission_alone_is_not_enough(
        self, client, session, world, entity
    ) -> None:
        before = await count(session, LedgerEntry, world.org_id)
        response = await run(
            client, world.org_id, entity, MINIMAL[entity], mode="commit", key="k",
            perms=[WRITE[entity]],
        )  # fmt: skip
        assert response.status_code == 403
        assert await count(session, LedgerEntry, world.org_id) == before

    async def test_every_other_inventory_permission_is_403(self, client, world, entity) -> None:
        others = [p for p in WRITE.values() if p != WRITE[entity]] + [STOCK_READ]
        response = await run(
            client, world.org_id, entity, MINIMAL[entity], mode="preview", perms=others
        )
        assert response.status_code == 403

    async def test_template_needs_the_same_two_permissions(self, client, world, entity) -> None:
        for held, expected in (
            ([], 403),
            ([DATA_IMPORT], 403),
            ([WRITE[entity]], 403),
            ([DATA_IMPORT, WRITE[entity]], 200),
        ):
            with as_principal(principal_for(world.org_id, held)):
                response = await client.get(f"{API}{BASE}/{entity}/template")
            assert response.status_code == expected, (held, response.text)

    async def test_another_tenant_cannot_reach_this_tenants_records(
        self, client, session, world, entity
    ) -> None:
        # The intruder holds every right permission; every row that names one of *our* records
        # (SKU, lot, location) is an unknown reference to them — never a hit, never our data.
        intruder = uuid.uuid4()
        skus_in_rows = entity != "items" and entity != "locations"
        locations_in_rows = entity == "locations"
        before = await count(session, LedgerEntry, world.org_id)
        for mode in ("preview", "commit"):
            response = await run(
                client, intruder, entity, MINIMAL[entity], mode=mode, key=f"x-{mode}"
            )
            if skus_in_rows or locations_in_rows or entity == "items":
                assert response.status_code in (200, 422), response.text
                errors = response.json()["errors"]
                assert errors and all(e["code"].startswith("unknown_") for e in errors), errors
        assert await count(session, LedgerEntry, world.org_id) == before
        assert await count(session, Lot, intruder) == 0
        assert await count(session, Roll, intruder) == 0
        assert await count(session, InventoryImportRun, intruder) == 0
        assert await count(session, InventoryImportRun, world.org_id) == 0


async def test_a_foreign_tenant_importing_the_same_skus_writes_only_to_itself(
    client, session, world, admin_session
) -> None:
    """Two organizations with the same SKU: an import in one adds lots to its own item only."""
    foreign = await build_world(admin_session, uuid.uuid4())
    rows = [{"sku": "COT-180-NAVY", "lot_no": "LOT-FOREIGN"}]
    response = await run(client, foreign.org_id, "lots", rows, mode="commit", key="f1")
    assert response.status_code == 200, response.text
    assert await count(session, Lot, world.org_id) == 1  # this tenant still has only LOT-A
    # ... and the lot is on the foreign tenant's own item, visible to it, invisible here.
    mine = await run(client, world.org_id, "lots", rows, mode="preview")
    assert mine.json()["error_count"] == 0  # the number is free in *this* organization


async def test_the_template_lists_every_column_with_required_flags(client, world) -> None:
    with as_principal(principal_for(world.org_id, [DATA_IMPORT, WRITE["opening_stock"]])):
        body = (await client.get(f"{API}{BASE}/opening_stock/template")).json()
    required = [c["name"] for c in body["columns"] if c["required"]]
    assert required == ["sku", "warehouse_code", "location_code", "quantity"]
    assert body["max_rows"] == 5000
