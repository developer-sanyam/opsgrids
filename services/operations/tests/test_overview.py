"""`/operations/overview` — the real, non-fake figures.

`on_time_pct` used to be a spend-weighted read of `Supplier.on_time_pct`, a column set once at
supplier creation and never revisited by a receipt (dashboard-data audit §3.5 row 13). These
prove it is now computed fresh from `PurchaseOrder.expected_date`/`received_date`, that
`out_of_stock_count` is real, and that both hold up empty and across tenants.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.inventory import services as inv_services
from platform_core.auth import Scope
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration

OVERVIEW = "/api/v1/operations/overview"
READ_PERM = "operations.procurement.purchase_order.read:ORG"


class TestOnTimeAndStockCounts:
    async def test_on_time_pct_is_computed_from_receipts_not_the_stale_supplier_column(
        self, client, org_id, data
    ) -> None:
        """The fixture's only received PO (PO-2026-0140) landed two days late. The suppliers'
        own `on_time_pct` columns (96.4 and 88.7) must play no part — a fresh, real 0% is what
        the receipts actually show."""
        principal = principal_for(org_id, [READ_PERM])
        with as_principal(principal):
            res = await client.get(OVERVIEW)
        assert res.status_code == 200
        body = res.json()
        assert body["on_time_pct"] == 0.0
        # Neither seeded item is out of stock (one is low, one is healthy).
        assert body["out_of_stock_count"] == 0

    async def test_on_time_pct_is_none_before_anything_has_been_received(
        self, session: AsyncSession, client, org_id
    ) -> None:
        """An org with only an unreceived PO reports `None`, not a manufactured `0.0` — there is
        nothing yet to judge as on time or late."""
        write = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            supplier = await services.create_supplier(
                session,
                write,
                code="SUP-NEW",
                name="New Supplier",
                category="raw_materials",
                status="active",
                lead_time_days=10,
                on_time_pct=100,
                spend_minor=0,
                currency="INR",
                open_pos=1,
                contact_name="",
                contact_email="",
                location="",
                since=None,
            )
            await services.create_purchase_order(
                session,
                write,
                number="PO-2026-0001",
                supplier_id=supplier.id,
                status="approved",
                warehouse="",
                raised_by="",
                raised_by_id=None,
                currency="INR",
                created_date=date(2026, 8, 1),
                expected_date=date(2026, 8, 15),
                received_date=None,
                notes=None,
                lines=[
                    {
                        "sku": "RM-0001",
                        "description": "Widget",
                        "quantity": 1,
                        "uom": "pcs",
                        "unit_price_minor": 100_00,
                        "received_qty": 0,
                    }
                ],
            )

        principal = principal_for(org_id, [READ_PERM])
        with as_principal(principal):
            res = await client.get(OVERVIEW)
        assert res.status_code == 200
        body = res.json()
        assert body["on_time_pct"] is None
        assert body["out_of_stock_count"] == 0

    async def test_an_out_of_stock_item_is_counted_and_separated_from_low_stock(
        self, session: AsyncSession, client, org_id, data
    ) -> None:
        write = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
        async with session.begin():
            await inv_services.create_inventory_item(
                session,
                write,
                sku="RM-EMPTY-001",
                name="Depleted spool",
                category="raw_materials",
                warehouse="Pune Plant",
                on_hand=0,
                reorder_point=5,
                uom="pcs",
                unit_value_minor=100_00,
                currency="INR",
                stocked_at=date(2026, 8, 22),
            )

        principal = principal_for(org_id, [READ_PERM])
        with as_principal(principal):
            res = await client.get(OVERVIEW)
        assert res.status_code == 200
        body = res.json()
        assert body["out_of_stock_count"] == 1
        # The seeded low-stock item (on hand but below reorder) still counts toward
        # `low_stock_count`, not `out_of_stock_count` — the two are not the same bucket.
        assert body["low_stock_count"] == 2  # low-stock item + the new zero-stock one
        assert body["out_of_stock_count"] <= body["low_stock_count"]

    async def test_empty_organization_has_no_fake_figures(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, [READ_PERM])
        with as_principal(principal):
            res = await client.get(OVERVIEW)
        assert res.status_code == 200
        body = res.json()
        assert body["on_time_pct"] is None
        assert body["out_of_stock_count"] == 0
        assert body["open_pos"] == 0

    async def test_another_organizations_receipts_never_move_this_ones_figure(
        self, session: AsyncSession, client, org_id, data, admin_session: AsyncSession
    ) -> None:
        """A second organization with a well-behaved, always-on-time supplier must not leak
        into this tenant's `on_time_pct` — the fixture org's own figure stays 0%."""
        other_org = uuid.uuid4()
        other_write = ctx_for(other_org, "operations.procurement.purchase_order.write", Scope.ORG)
        async with admin_session.begin():
            supplier = await services.create_supplier(
                admin_session,
                other_write,
                code="SUP-OTHER",
                name="Other Org Supplier",
                category="raw_materials",
                status="active",
                lead_time_days=5,
                on_time_pct=100,
                spend_minor=0,
                currency="INR",
                open_pos=1,
                contact_name="",
                contact_email="",
                location="",
                since=None,
            )
            await services.create_purchase_order(
                admin_session,
                other_write,
                number="PO-OTHER-0001",
                supplier_id=supplier.id,
                status="received",
                warehouse="",
                raised_by="",
                raised_by_id=None,
                currency="INR",
                created_date=date(2026, 8, 1),
                expected_date=date(2026, 8, 15),
                received_date=date(2026, 8, 10),
                notes=None,
                lines=[
                    {
                        "sku": "RM-OTHER-0001",
                        "description": "Widget",
                        "quantity": 1,
                        "uom": "pcs",
                        "unit_price_minor": 500_00,
                        "received_qty": 1,
                    }
                ],
            )

        principal = principal_for(org_id, [READ_PERM])
        with as_principal(principal):
            res = await client.get(OVERVIEW)
        assert res.status_code == 200
        assert res.json()["on_time_pct"] == 0.0
