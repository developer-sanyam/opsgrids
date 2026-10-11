"""Per-item unit conversions over HTTP (inventory Phase 2's deferred `uom_conversions` work): the
CRUD, the refusals that keep one fact from having two sources, the guards that stop a ratio change
from silently re-interpreting an open order, and the exact-math effect on a receipt.

Authorization and cross-tenant behaviour: `test_receiving_authz.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import SCHEMA, AuditLog, InventoryItem, UomConversion
from app.receiving import selectors
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, principal_for
from tests.receiving_fixtures import (
    API,
    ITEM_READ,
    ITEM_WRITE,
    RECEIVE_PERMS,
    RWorld,
    build_rworld,
    make_po,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def w(session, org_id) -> RWorld:
    return await build_rworld(session, org_id)


async def call(client, org_id, perm, method, path, body=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, [perm])):
        return await client.request(method, API + path, json=body)


def url(item: uuid.UUID) -> str:
    return f"/operations/inventory/{item}/uom-conversions"


class TestCrud:
    async def test_list_shows_the_items_conversions_in_its_own_stock_unit(
        self, client, org_id, w
    ) -> None:
        response = await call(client, org_id, ITEM_READ, "GET", url(w.buttons))
        assert response.status_code == 200
        (row,) = response.json()
        assert (row["uom"], row["numerator"], row["denominator"], row["stock_uom"]) == (
            "box",
            12,
            1,
            "pcs",
        )
        assert (await call(client, org_id, ITEM_READ, "GET", url(w.dye))).json() == []

    async def test_create_reduces_the_ratio_to_lowest_terms_and_audits(
        self, client, session, org_id, w
    ) -> None:
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "POST",
            url(w.buttons),
            {"uom": "Cartons", "numerator": 240, "denominator": 10},
        )
        assert response.status_code == 201, response.text
        made = response.json()
        # "Cartons" folds to the registry's `carton`; 240/10 is 24/1
        assert (made["uom"], made["numerator"], made["denominator"]) == ("carton", 24, 1)
        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "uom_conversion.created")
                    )
                )
                .scalars()
                .all()
            )
        audit = next(a for a in audits if a.after["uom"] == "carton")
        assert audit.after["numerator"] == 24 and audit.after["stock_uom"] == "pcs"

    async def test_a_custom_unit_is_created_on_first_use(self, client, org_id, w) -> None:
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "POST",
            url(w.fabric),
            {"uom": "bale", "numerator": 5, "denominator": 2},
        )
        assert response.status_code == 201, response.text
        assert (response.json()["uom"], response.json()["stock_uom"]) == ("bale", "m")

    async def test_the_items_own_unit_and_a_second_row_for_one_unit_are_refused(
        self, client, org_id, w
    ) -> None:
        same = await call(
            client, org_id, ITEM_WRITE, "POST", url(w.buttons), {"uom": "pieces", "numerator": 1}
        )
        assert same.status_code == 422 and same.json()["errors"][0]["code"] == "same_unit"
        dup = await call(
            client, org_id, ITEM_WRITE, "POST", url(w.buttons), {"uom": "box", "numerator": 10}
        )
        assert dup.status_code == 409 and dup.json()["errors"][0]["code"] == "duplicate"

    async def test_a_unit_with_a_fixed_ratio_needs_no_row(self, client, org_id, w) -> None:
        response = await call(
            client, org_id, ITEM_WRITE, "POST", url(w.fabric), {"uom": "cm", "numerator": 1}
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "fixed_conversion_exists"

    @pytest.mark.parametrize(
        "body",
        [
            {"uom": "box", "numerator": 0},
            {"uom": "box", "numerator": 1, "denominator": 0},
            {"uom": "box", "numerator": 1.5},
            {"uom": "box", "numerator": "12"},
            {"numerator": 12},
            {"uom": "box", "numerator": 12, "extra": 1},
        ],
    )
    async def test_a_malformed_ratio_is_a_422(self, client, org_id, w, body) -> None:
        response = await call(client, org_id, ITEM_WRITE, "POST", url(w.dye), body)
        assert response.status_code == 422

    async def test_patch_changes_the_ratio_and_audits_before_and_after(
        self, client, session, org_id, w
    ) -> None:
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {"numerator": 24, "denominator": 2},
        )
        assert response.status_code == 200, response.text
        assert (response.json()["numerator"], response.json()["denominator"]) == (12, 1)
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {"numerator": 10},
        )
        assert (response.json()["numerator"], response.json()["denominator"]) == (10, 1)
        async with session.begin():
            audit = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.action == "uom_conversion.updated")
                        .order_by(AuditLog.at.desc())
                    )
                )
                .scalars()
                .first()
            )
        assert audit.before["numerator"] == 12 and audit.after["numerator"] == 10

    async def test_patch_needs_something_to_change(self, client, org_id, w) -> None:
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {},
        )
        assert response.status_code == 422

    async def test_delete_removes_it(self, client, session, org_id, w) -> None:
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "DELETE",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
        )
        assert response.status_code == 204
        async with session.begin():
            gone = (
                await session.execute(
                    select(UomConversion).where(UomConversion.id == w.box_conversion_id)
                )
            ).scalar_one_or_none()
        assert gone is None


class TestGuards:
    async def test_a_conversion_cannot_be_created_for_a_unit_an_open_po_already_received_in(
        self, client, session, org_id, w
    ) -> None:
        # "carton" has no conversion, so the PO's quantity was read as stock units while receiving
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 10, "uom": "carton", "unit_price_minor": 100}],
        )
        with as_principal(principal_for(org_id, list(RECEIVE_PERMS))):
            posted = await client.post(
                f"{API}/operations/goods-receipts",
                json={
                    "purchase_order_id": str(po_id),
                    "lines": [{"purchase_order_line_id": str(lines["BTN-14MM"]), "quantity": "4"}],
                },
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
        assert posted.status_code == 201, posted.text
        created = await call(
            client,
            org_id,
            ITEM_WRITE,
            "POST",
            url(w.buttons),
            {"uom": "carton", "numerator": 24, "denominator": 1},
        )
        assert created.status_code == 409, created.text

    async def test_changing_a_conversion_takes_the_items_lock_that_receipts_wait_on(
        self, session, org_id, w
    ) -> None:
        engine = create_async_engine(settings().app_dsn)
        bind = ("SELECT set_config(:g, :o, true)", {"g": TENANT_GUC, "o": str(org_id)})
        path = f"SET LOCAL search_path = {SCHEMA}, public"
        try:
            async with (
                engine.connect() as holder,
                engine.connect() as other,
                holder.begin(),
                other.begin(),
            ):
                for conn in (holder, other):
                    await conn.execute(text(bind[0]), bind[1])
                    await conn.execute(text(path))
                found = await selectors.get_conversion(
                    AsyncSession(bind=holder), org_id, w.box_conversion_id, for_update=True
                )
                assert found is not None
                with pytest.raises(DBAPIError):
                    await other.execute(
                        select(InventoryItem.id)
                        .where(InventoryItem.id == w.buttons)
                        .with_for_update(nowait=True)
                    )
        finally:
            await engine.dispose()

    async def test_a_conversion_an_open_po_orders_in_cannot_be_deleted(
        self, client, session, org_id, w
    ) -> None:
        await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 10, "uom": "box", "unit_price_minor": 100}],
        )
        response = await call(
            client,
            org_id,
            ITEM_WRITE,
            "DELETE",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
        )
        assert response.status_code == 409
        assert "box" in response.json()["detail"]

    async def test_a_ratio_cannot_change_once_an_open_po_has_received_in_that_unit(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 10, "uom": "box", "unit_price_minor": 100}],
        )
        with as_principal(principal_for(org_id, list(RECEIVE_PERMS))):
            posted = await client.post(
                f"{API}/operations/goods-receipts",
                json={
                    "purchase_order_id": str(po_id),
                    "lines": [
                        {
                            "purchase_order_line_id": str(lines["BTN-14MM"]),
                            "quantity": "4",
                            "uom": "box",
                        }
                    ],
                },
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
        assert posted.status_code == 201, posted.text
        changed = await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {"numerator": 10},
        )
        assert changed.status_code == 409
        # a same-ratio PATCH is not a change
        same = await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {"numerator": 12},
        )
        assert same.status_code == 200

    async def test_a_receipt_already_posted_keeps_the_quantity_it_was_converted_to(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 120, "uom": "pcs", "unit_price_minor": 100}],
        )
        with as_principal(principal_for(org_id, list(RECEIVE_PERMS))):
            posted = await client.post(
                f"{API}/operations/goods-receipts",
                json={
                    "purchase_order_id": str(po_id),
                    "lines": [
                        {
                            "purchase_order_line_id": str(lines["BTN-14MM"]),
                            "quantity": "2",
                            "uom": "box",
                        }
                    ],
                },
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
        assert posted.json()["lines"][0]["received_qty"] == "24"
        await call(
            client,
            org_id,
            ITEM_WRITE,
            "PATCH",
            f"/operations/inventory/uom-conversions/{w.box_conversion_id}",
            {"numerator": 10},
        )
        with as_principal(principal_for(org_id, ["operations.procurement.receipt.read:ORG"])):
            again = await client.get(f"{API}/operations/goods-receipts/{posted.json()['id']}")
        row = again.json()["lines"][0]
        # entered 2 box, converted to 24 pcs at the time: the new ratio does not rewrite history
        assert (row["entered_qty"], row["entered_uom"], row["received_qty"]) == ("2", "box", "24")
