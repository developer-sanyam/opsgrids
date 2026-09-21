"""The HTTP write surface: raising purchase orders, and the reference data behind them.

The write *logic* is covered elsewhere (`test_authz.py` for the service-level refusals,
`test_po_totals.py` for the arithmetic). These tests are about the **routes**: that each one is
guarded by its own permission rather than by a UI affordance, that the payload cannot assert
what the server derives, and that a malformed body is refused before it reaches the service.

Every endpoint gets the same three questions: does the happy path persist, is a caller without
the write permission refused, and is a bad payload rejected. The refusal is the important one —
a write route that only proves it can write has proved the uninteresting half.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.main import app
from app.models import InventoryItem, PurchaseOrder, PurchaseOrderLine, Supplier, Warehouse
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    """Run the request as this principal — the identity the gateway would have forwarded.

    Deliberately a local copy of the helper in `test_authz.py`: conftest is loaded rather than
    imported, and a test module that reaches into a sibling module for a fixture helper breaks
    the moment either is run alone.
    """
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


PO_WRITE = "operations.procurement.purchase_order.write"
PO_READ = "operations.procurement.purchase_order.read"
SUPPLIER_WRITE = "operations.procurement.supplier.write"
SUPPLIER_READ = "operations.procurement.supplier.read"
ITEM_WRITE = "operations.inventory.item.write"
ITEM_READ = "operations.inventory.item.read"
WAREHOUSE_WRITE = "operations.inventory.warehouse.write"
WAREHOUSE_READ = "operations.inventory.warehouse.read"


def po_body(number: str, **overrides: object) -> dict:
    """A well-formed purchase order against the seeded Meridian supplier."""
    body: dict = {
        "number": number,
        "supplier_code": "SUP-MER",
        "warehouse": "Pune Plant",
        "raised_by": "Devansh Rao",
        "created_date": "2026-09-01",
        "expected_date": "2026-09-30",
        "notes": "Top-up for the casting line.",
        "lines": [
            {
                "sku": "RM-STL-4501",
                "description": "Cold-rolled steel coil, 1.2mm",
                "quantity": 10,
                "uom": "MT",
                "unit_price_minor": 62_500_00,
            },
            {
                "sku": "RM-STL-4520",
                "description": "Galvanised steel sheet, 0.8mm",
                "quantity": 4,
                "uom": "MT",
                "unit_price_minor": 71_200_00,
            },
        ],
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------- purchase orders


class TestRaisePurchaseOrder:
    async def test_raising_a_po_persists_it_with_its_supplier_and_lines(
        self, client, session, org_id, data
    ) -> None:
        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/purchase-orders", json=po_body("PO-2026-0201")
            )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["po_number"] == "PO-2026-0201"
        # Denormalised from the supplier the code named, not from the payload.
        assert body["supplier_name"] == "Meridian Metals"
        assert body["category"] == "raw_materials"
        assert body["status"] == "draft"
        assert [line["sku"] for line in body["line_items"]] == ["RM-STL-4501", "RM-STL-4520"]
        assert all(line["received_qty"] == 0 for line in body["line_items"])

        # In the database, in this tenant — not merely echoed back.
        po = (
            await session.execute(
                select(PurchaseOrder).where(PurchaseOrder.number == "PO-2026-0201")
            )
        ).scalar_one()
        assert po.organization_id == org_id
        assert po.received_date is None

    async def test_line_amounts_are_computed_and_a_supplied_one_is_ignored(
        self, client, session, org_id, data
    ) -> None:
        """`amount_minor` is `quantity * unit_price_minor`, computed server-side. A client that
        sends its own must not be able to make the lines stop footing to the total."""
        body = po_body("PO-2026-0202")
        body["lines"][0]["amount_minor"] = 1  # a total of its own choosing

        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post("/api/v1/operations/purchase-orders", json=body)
        assert response.status_code == 201, response.text

        po_id = response.json()["id"]
        lines = (
            (
                await session.execute(
                    select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.purchase_order_id == po_id)
                    .order_by(PurchaseOrderLine.sku)
                )
            )
            .scalars()
            .all()
        )
        assert [line.amount_minor for line in lines] == [10 * 62_500_00, 4 * 71_200_00]

    async def test_the_raiser_is_the_caller_not_the_payload(self, client, org_id, data) -> None:
        """`raised_by_id` is the column OWN scope narrows on, so it comes from the caller. A
        buyer who raises a PO can then see it; another buyer cannot."""
        writer = principal_for(org_id, [f"{PO_WRITE}:OWN"], subject_ref=data["subject_a"])
        with as_principal(writer):
            created = await client.post(
                "/api/v1/operations/purchase-orders", json=po_body("PO-2026-0203")
            )
        assert created.status_code == 201, created.text

        mine = principal_for(org_id, [f"{PO_READ}:OWN"], subject_ref=data["subject_a"])
        with as_principal(mine):
            own = await client.get("/api/v1/operations/purchase-orders")
        theirs = principal_for(org_id, [f"{PO_READ}:OWN"], subject_ref=data["subject_b"])
        with as_principal(theirs):
            other = await client.get("/api/v1/operations/purchase-orders")

        assert "PO-2026-0203" in {item["po_number"] for item in own.json()["items"]}
        assert "PO-2026-0203" not in {item["po_number"] for item in other.json()["items"]}

    async def test_a_reader_cannot_raise_a_purchase_order(self, client, session, org_id) -> None:
        """Holding purchase_order.read but not .write is refused by name."""
        reader = principal_for(org_id, [f"{PO_READ}:ORG"])
        with as_principal(reader):
            response = await client.post(
                "/api/v1/operations/purchase-orders", json=po_body("PO-2026-0204")
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        # The refusal wrote nothing.
        assert (
            await session.execute(
                select(PurchaseOrder).where(PurchaseOrder.number == "PO-2026-0204")
            )
        ).scalar_one_or_none() is None

    async def test_unauthenticated_cannot_raise_a_purchase_order(self, client) -> None:
        response = await client.post(
            "/api/v1/operations/purchase-orders", json=po_body("PO-2026-0205")
        )
        assert response.status_code == 401

    async def test_a_line_without_a_positive_quantity_is_refused(
        self, client, org_id, data
    ) -> None:
        body = po_body("PO-2026-0206")
        body["lines"][0]["quantity"] = 0
        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post("/api/v1/operations/purchase-orders", json=body)
        assert response.status_code == 422

    async def test_a_po_naming_no_supplier_is_refused(self, client, org_id, data) -> None:
        """The service takes either a supplier id or a supplier code; a body carrying neither is
        refused at the contract, not with a 500 from the write path."""
        body = po_body("PO-2026-0207")
        del body["supplier_code"]
        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post("/api/v1/operations/purchase-orders", json=body)
        assert response.status_code == 422

    async def test_a_po_cannot_be_raised_already_received(self, client, org_id, data) -> None:
        """Receiving is its own endpoint — it fills the line quantities and stamps the date —
        so `received` is not a status a client may assert at creation."""
        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json=po_body("PO-2026-0208", status="received"),
            )
        assert response.status_code == 422

    async def test_a_duplicate_po_number_is_a_conflict(self, client, org_id, data) -> None:
        writer = principal_for(org_id, [f"{PO_WRITE}:ORG"], subject_ref=data["subject_a"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/purchase-orders", json=po_body("PO-2026-0148")
            )
        assert response.status_code == 409


# --------------------------------------------------------------------- suppliers


SUPPLIER_BODY = {
    "code": "SUP-KAV",
    "name": "Kaveri Polymers",
    "category": "packaging",
    "status": "active",
    "lead_time_days": 9,
    "currency": "INR",
    "contact_name": "Anita Desai",
    "contact_email": "anita@kaveripolymers.example",
    "location": "Coimbatore",
    "since": "2024-02-11",
}


class TestCreateSupplier:
    async def test_adding_a_supplier_persists_it_and_lists_it(
        self, client, session, org_id, data
    ) -> None:
        writer = principal_for(org_id, [f"{SUPPLIER_WRITE}:ORG", f"{SUPPLIER_READ}:ORG"])
        with as_principal(writer):
            created = await client.post("/api/v1/operations/suppliers", json=SUPPLIER_BODY)
            listed = await client.get("/api/v1/operations/suppliers", params={"q": "kaveri"})

        assert created.status_code == 201, created.text
        assert created.json()["code"] == "SUP-KAV"
        assert [row["name"] for row in listed.json()["items"]] == ["Kaveri Polymers"]

        supplier = (
            await session.execute(select(Supplier).where(Supplier.code == "SUP-KAV"))
        ).scalar_one()
        assert supplier.organization_id == org_id
        # The performance rollups are observed, never asserted by the caller: a new supplier
        # starts with no spend, no open POs and no delivery record.
        assert (supplier.spend_minor, supplier.open_pos, float(supplier.on_time_pct)) == (0, 0, 0.0)

    async def test_a_reader_cannot_add_a_supplier(self, client, session, org_id) -> None:
        reader = principal_for(org_id, [f"{SUPPLIER_READ}:ORG"])
        with as_principal(reader):
            response = await client.post("/api/v1/operations/suppliers", json=SUPPLIER_BODY)
        assert response.status_code == 403
        assert (
            await session.execute(select(Supplier).where(Supplier.code == "SUP-KAV"))
        ).scalar_one_or_none() is None

    async def test_a_category_outside_the_vocabulary_is_refused(self, client, org_id) -> None:
        writer = principal_for(org_id, [f"{SUPPLIER_WRITE}:ORG"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/suppliers", json=SUPPLIER_BODY | {"category": "widgets"}
            )
        assert response.status_code == 422

    async def test_a_duplicate_supplier_code_is_a_conflict(self, client, org_id, data) -> None:
        writer = principal_for(org_id, [f"{SUPPLIER_WRITE}:ORG"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/suppliers", json=SUPPLIER_BODY | {"code": "SUP-MER"}
            )
        assert response.status_code == 409


# --------------------------------------------------------------------- inventory


INVENTORY_BODY = {
    "sku": "PKG-CRT-220",
    "name": "Corrugated carton, 5-ply",
    "category": "packaging",
    "warehouse": "Pune Plant",
    "on_hand": 150,
    "reorder_point": 400,
    "uom": "pcs",
    "unit_value_minor": 62_00,
    "currency": "INR",
    "stocked_at": "2026-09-14",
}


class TestCreateInventoryItem:
    async def test_stocking_an_item_persists_it_and_reads_back_as_low_stock(
        self, client, session, org_id, data
    ) -> None:
        """Low-stock is derived on read (`on_hand <= reorder_point`), so an item written through
        this route must surface in the low-stock filter without anyone storing a flag."""
        writer = principal_for(org_id, [f"{ITEM_WRITE}:ORG", f"{ITEM_READ}:ORG"])
        with as_principal(writer):
            created = await client.post("/api/v1/operations/inventory", json=INVENTORY_BODY)
            low = await client.get("/api/v1/operations/inventory", params={"low_stock": "true"})

        assert created.status_code == 201, created.text
        assert created.json()["unit_value_minor"] == 62_00
        assert created.json()["updated_at"] == "2026-09-14"
        assert "PKG-CRT-220" in {row["sku"] for row in low.json()["items"]}

        item = (
            await session.execute(select(InventoryItem).where(InventoryItem.sku == "PKG-CRT-220"))
        ).scalar_one()
        assert (item.organization_id, item.on_hand, item.currency) == (org_id, 150, "INR")

    async def test_a_reader_cannot_stock_an_item(self, client, session, org_id) -> None:
        reader = principal_for(org_id, [f"{ITEM_READ}:ORG"])
        with as_principal(reader):
            response = await client.post("/api/v1/operations/inventory", json=INVENTORY_BODY)
        assert response.status_code == 403
        assert (
            await session.execute(select(InventoryItem).where(InventoryItem.sku == "PKG-CRT-220"))
        ).scalar_one_or_none() is None

    async def test_a_negative_quantity_on_hand_is_refused(self, client, org_id) -> None:
        writer = principal_for(org_id, [f"{ITEM_WRITE}:ORG"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/inventory", json=INVENTORY_BODY | {"on_hand": -5}
            )
        assert response.status_code == 422


# --------------------------------------------------------------------- warehouses


WAREHOUSE_BODY = {"code": "WH-CHN", "name": "Chennai Depot", "location": "Chennai"}


class TestCreateWarehouse:
    async def test_opening_a_warehouse_persists_it_and_lists_it(
        self, client, session, org_id, data
    ) -> None:
        writer = principal_for(org_id, [f"{WAREHOUSE_WRITE}:ORG", f"{WAREHOUSE_READ}:ORG"])
        with as_principal(writer):
            created = await client.post("/api/v1/operations/warehouses", json=WAREHOUSE_BODY)
            listed = await client.get("/api/v1/operations/warehouses")

        assert created.status_code == 201, created.text
        assert created.json()["name"] == "Chennai Depot"
        assert "WH-CHN" in {row["code"] for row in listed.json()["items"]}

        warehouse = (
            await session.execute(select(Warehouse).where(Warehouse.code == "WH-CHN"))
        ).scalar_one()
        assert warehouse.organization_id == org_id

    async def test_a_reader_cannot_open_a_warehouse(self, client, session, org_id) -> None:
        reader = principal_for(org_id, [f"{WAREHOUSE_READ}:ORG"])
        with as_principal(reader):
            response = await client.post("/api/v1/operations/warehouses", json=WAREHOUSE_BODY)
        assert response.status_code == 403
        assert (
            await session.execute(select(Warehouse).where(Warehouse.code == "WH-CHN"))
        ).scalar_one_or_none() is None

    async def test_a_blank_code_is_refused(self, client, org_id) -> None:
        writer = principal_for(org_id, [f"{WAREHOUSE_WRITE}:ORG"])
        with as_principal(writer):
            response = await client.post(
                "/api/v1/operations/warehouses", json=WAREHOUSE_BODY | {"code": ""}
            )
        assert response.status_code == 422
