"""Orders over HTTP — the order engine (ADR-0024, ADR-0025; `docs/fulfilment/plan.md` F2).

Mirrors `test_locations_write.py`'s shape (F1's own template): real status codes asserted at
every step, a seed step's own status checked before its id is trusted in a later assertion, and a
resend of the caller's own current, unchanged value is proven *not* to trip a guard it shouldn't.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.inventory import services as inv_services
from app.inventory.reconcile import reconcile_organization
from app.models import (
    AuditLog,
    InventoryBalance,
    InventoryItem,
    InventoryReservation,
    OrderTransition,
    OutboxEvent,
)
from app.orders import selectors as order_selectors
from platform_core.auth import Scope
from tests.helpers import as_principal, ctx_for, principal_for
from tests.order_fixtures import (
    ACCEPT_PERM,
    CANCEL_PERM,
    CREATE_PERM,
    READ_PERM,
    WRITE_PERM,
    Shop,
    build_shop,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(session, org_id) -> Shop:
    return await build_shop(session, org_id)


def _principal(org_id: uuid.UUID, perms: list[str], data: dict):  # type: ignore[no-untyped-def]
    return principal_for(org_id, perms, subject_ref=data["subject_a"])


async def _create_order(  # type: ignore[no-untyped-def]
    client, org_id, data, shop: Shop, *, lines: list[dict] | None = None, channel: str = "manual"
) -> dict:
    lines = (
        lines if lines is not None else [{"inventory_item_id": str(shop.widget), "quantity": "2"}]
    )
    principal = _principal(org_id, [CREATE_PERM], data)
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/orders",
            json={
                "channel": channel,
                "warehouse_id": str(shop.warehouse_id),
                "customer_name": "Asha Verma",
                "customer_phone": "9999900000",
                "lines": lines,
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _place(client, org_id, data, order: dict) -> dict:  # type: ignore[no-untyped-def]
    principal = _principal(org_id, [WRITE_PERM], data)
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order['id']}/place",
            json={"version": order["version"]},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _accept(client, org_id, data, order: dict):  # type: ignore[no-untyped-def]
    principal = _principal(org_id, [ACCEPT_PERM], data)
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/orders/{order['id']}/accept",
            json={"version": order["version"]},
        )


async def _assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


async def _reserved_milli(session, org_id, item_id) -> int:  # type: ignore[no-untyped-def]
    """The item's own `reserved_milli` at its home location — read fresh, so a refused accept can
    be proven to have moved nothing, not just answered `409`."""
    async with session.begin():
        home_location_id = (
            await session.execute(
                select(InventoryItem.home_location_id).where(InventoryItem.id == item_id)
            )
        ).scalar_one()
        row = (
            await session.execute(
                select(InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item_id,
                    InventoryBalance.location_id == home_location_id,
                    InventoryBalance.status == "AVAILABLE",
                    InventoryBalance.lot_id.is_(None),
                    InventoryBalance.roll_id.is_(None),
                )
            )
        ).scalar_one_or_none()
    return row or 0


async def _add_item(  # type: ignore[no-untyped-def]
    session,
    org_id,
    *,
    sku: str,
    selling_price_minor: int | None,
    selling_price_currency: str | None,
) -> InventoryItem:
    """An inventory item at `shop`'s own warehouse, with a hand-picked selling price/currency —
    for the currency-mismatch (MAJOR A) and overflow (MINOR D) tests, which need a price `Shop`
    itself does not give them."""
    ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
    async with session.begin():
        return await inv_services.create_inventory_item(
            session,
            ctx,
            sku=sku,
            name=sku,
            category="components",
            warehouse="Shop Store",
            uom="pcs",
            on_hand=0,
            selling_price_minor=selling_price_minor,
            selling_price_currency=selling_price_currency,
        )


# ------------------------------------------------------------------ quote


class TestQuotePreview:
    async def test_happy_path_prices_and_checks_availability(
        self, client, org_id, data, shop
    ) -> None:
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "3"}],
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fully_priced"] is True
        assert body["fully_available"] is True
        assert body["subtotal_minor"] == 3 * 500_00
        assert body["total_minor"] == 3 * 500_00
        assert body["lines"][0]["sufficient"] is True

    async def test_an_unpriced_item_is_flagged_not_422ed(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.unpriced), "quantity": "1"}],
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fully_priced"] is False
        assert body["lines"][0]["priced"] is False
        assert body["lines"][0]["line_total_minor"] is None

    async def test_a_shortfall_is_flagged_sufficient_false(
        self, client, org_id, data, shop
    ) -> None:
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "9999"}],
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["fully_available"] is False

    async def test_a_currency_mismatched_catalogue_price_is_conflict(
        self, client, org_id, data, session, shop
    ) -> None:
        """Review MAJOR A: a line pricing from the catalogue is refused, not silently mixed into
        the quote's own currency, when the item's own price is in a different one."""
        usd_item = await _add_item(
            session, org_id, sku="USD-001", selling_price_minor=10_00, selling_price_currency="USD"
        )
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "currency": "INR",
                    "lines": [{"inventory_item_id": str(usd_item.id), "quantity": "1"}],
                },
            )
        assert response.status_code == 409, response.text
        assert "USD-001" in response.text

    async def test_an_overridden_quote_line_price_bypasses_the_currency_check(
        self, client, org_id, data, session, shop
    ) -> None:
        usd_item = await _add_item(
            session, org_id, sku="USD-002", selling_price_minor=10_00, selling_price_currency="USD"
        )
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "currency": "INR",
                    "lines": [
                        {
                            "inventory_item_id": str(usd_item.id),
                            "quantity": "1",
                            "selling_price_minor": 500_00,
                        }
                    ],
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["lines"][0]["priced"] is True

    async def test_a_caller_without_read_is_forbidden(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 403

    async def test_cross_tenant_warehouse_is_not_found(self, client, org_id, data, shop) -> None:
        other = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 404

    async def test_cross_tenant_item_with_org_bs_own_warehouse_is_not_found(
        self, client, org_id, data, admin_session, shop
    ) -> None:
        """Org B's *own* warehouse, but a line naming org A's item — the warehouse 404 above only
        proves warehouse-level scoping; this proves the item id is checked against the caller's
        own organization too, not just resolved globally."""
        other_org = uuid.uuid4()
        other_shop = await build_shop(admin_session, other_org)
        other = principal_for(other_org, [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/orders/quote",
                json={
                    "warehouse_id": str(other_shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ create


class TestCreateOrder:
    async def test_happy_path_is_a_draft_with_unpriced_lines(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        assert order["status"] == "DRAFT"
        assert order["version"] == 0
        assert order["total_minor"] == 0
        assert order["lines"][0]["selling_price_minor"] is None

    async def test_a_reader_cannot_create(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(shop.warehouse_id),
                    "customer_name": "Asha",
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 403

    async def test_cross_tenant_warehouse_is_not_found(self, client, org_id, data, shop) -> None:
        other = principal_for(uuid.uuid4(), [CREATE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(shop.warehouse_id),
                    "customer_name": "Asha",
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 404

    async def test_cross_tenant_item_with_org_bs_own_warehouse_is_not_found(
        self, client, org_id, data, admin_session, shop
    ) -> None:
        """Org B's *own* warehouse, but a line naming org A's item — the warehouse 404 above only
        proves warehouse-level scoping; this proves the item id is checked against the caller's
        own organization too, not just resolved globally."""
        other_org = uuid.uuid4()
        other_shop = await build_shop(admin_session, other_org)
        other = principal_for(other_org, [CREATE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(other_shop.warehouse_id),
                    "customer_name": "Someone",
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 404

    async def test_no_customer_at_all_is_422(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [CREATE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "walk_in",
                    "warehouse_id": str(shop.warehouse_id),
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 422

    async def test_an_unknown_item_is_not_found(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [CREATE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(shop.warehouse_id),
                    "customer_name": "Asha",
                    "lines": [{"inventory_item_id": str(uuid.uuid4()), "quantity": "1"}],
                },
            )
        assert response.status_code == 404

    async def test_a_malformed_currency_is_422(self, client, org_id, data, shop) -> None:
        """Review MAJOR A: `OrderIn.currency` now needs the ISO-4217 shape, not any 3-character
        string."""
        principal = _principal(org_id, [CREATE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(shop.warehouse_id),
                    "customer_name": "Asha",
                    "currency": "usd",
                    "lines": [{"inventory_item_id": str(shop.widget), "quantity": "1"}],
                },
            )
        assert response.status_code == 422

    async def test_an_oversized_explicit_line_price_is_422(
        self, client, org_id, data, shop
    ) -> None:
        """Review MINOR D: `10**19` is a clean 422, not an unhandled `DataError` at flush."""
        principal = _principal(org_id, [CREATE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/orders",
                json={
                    "channel": "manual",
                    "warehouse_id": str(shop.warehouse_id),
                    "customer_name": "Asha",
                    "lines": [
                        {
                            "inventory_item_id": str(shop.widget),
                            "quantity": "1",
                            "selling_price_minor": 10**19,
                        }
                    ],
                },
            )
        assert response.status_code == 422

    async def test_a_catalogue_priced_line_is_not_flagged_an_override(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        line = order["lines"][0]
        assert line["price_overridden"] is False
        assert line["catalogue_price_minor"] == 500_00

    async def test_an_explicit_line_price_is_visible_as_an_override(
        self, client, org_id, data, shop
    ) -> None:
        """Review MAJOR B: the override is visible on the wire, not a silent substitution."""
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {
                    "inventory_item_id": str(shop.widget),
                    "quantity": "1",
                    "selling_price_minor": 999_00,
                }
            ],
        )
        line = order["lines"][0]
        assert line["price_overridden"] is True
        assert line["selling_price_minor"] == 999_00
        assert line["catalogue_price_minor"] == 500_00

    async def test_an_override_is_named_on_the_create_audit_row(
        self, client, org_id, data, session, shop
    ) -> None:
        """Review MAJOR B: the create audit row names which line overrode its price, and both the
        override and the catalogue price it overrode — not just `line_count`."""
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {
                    "inventory_item_id": str(shop.widget),
                    "quantity": "1",
                    "selling_price_minor": 999_00,
                },
                {"inventory_item_id": str(shop.gizmo), "quantity": "1"},
            ],
        )
        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.created",
                        AuditLog.entity_id == uuid.UUID(order["id"]),
                    )
                )
            ).scalar_one()
        overrides = row.after["price_overrides"]
        assert row.after["line_count"] == 2
        assert len(overrides) == 1  # only the widget line named its own price
        assert overrides[0]["sku"] == "WGT-001"
        assert overrides[0]["override_price_minor"] == 999_00
        assert overrides[0]["catalogue_price_minor"] == 500_00


# ------------------------------------------------------------------ place


class TestPlaceOrder:
    async def test_happy_path_prices_and_totals(self, client, org_id, data, shop) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[{"inventory_item_id": str(shop.widget), "quantity": "2"}],
        )
        placed = await _place(client, org_id, data, order)
        assert placed["status"] == "PLACED"
        assert placed["version"] == 1
        assert placed["total_minor"] == 2 * 500_00
        assert placed["lines"][0]["selling_price_minor"] == 500_00

    async def test_an_explicit_line_price_overrides_the_item(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {
                    "inventory_item_id": str(shop.widget),
                    "quantity": "1",
                    "selling_price_minor": 999_00,
                }
            ],
        )
        placed = await _place(client, org_id, data, order)
        assert placed["lines"][0]["selling_price_minor"] == 999_00
        assert placed["total_minor"] == 999_00
        assert placed["lines"][0]["price_overridden"] is True
        assert placed["lines"][0]["catalogue_price_minor"] == 500_00

    async def test_an_override_is_named_on_the_place_audit_row(
        self, client, org_id, data, session, shop
    ) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {
                    "inventory_item_id": str(shop.widget),
                    "quantity": "1",
                    "selling_price_minor": 999_00,
                }
            ],
        )
        await _place(client, org_id, data, order)
        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.placed",
                        AuditLog.entity_id == uuid.UUID(order["id"]),
                    )
                )
            ).scalar_one()
        overrides = row.after["price_overrides"]
        assert len(overrides) == 1
        assert overrides[0]["sku"] == "WGT-001"
        assert overrides[0]["override_price_minor"] == 999_00
        assert overrides[0]["catalogue_price_minor"] == 500_00

    async def test_a_currency_mismatched_catalogue_price_is_conflict(
        self, client, org_id, data, session, shop
    ) -> None:
        """Review MAJOR A: a line pricing from the catalogue is refused, naming the SKU, when the
        item's own price is in a different currency than the order's."""
        usd_item = await _add_item(
            session, org_id, sku="USD-003", selling_price_minor=10_00, selling_price_currency="USD"
        )
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[{"inventory_item_id": str(usd_item.id), "quantity": "1"}],
        )
        assert order["currency"] == "INR"
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place", json={"version": order["version"]}
            )
        assert response.status_code == 409, response.text
        assert "USD-003" in response.text

    async def test_an_overridden_line_price_bypasses_the_currency_check(
        self, client, org_id, data, session, shop
    ) -> None:
        usd_item = await _add_item(
            session, org_id, sku="USD-004", selling_price_minor=10_00, selling_price_currency="USD"
        )
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {
                    "inventory_item_id": str(usd_item.id),
                    "quantity": "1",
                    "selling_price_minor": 500_00,
                }
            ],
        )
        placed = await _place(client, org_id, data, order)
        assert placed["status"] == "PLACED"
        assert placed["lines"][0]["selling_price_minor"] == 500_00

    async def test_a_line_total_that_would_overflow_is_conflict_not_a_crash(
        self, client, org_id, data, session, shop
    ) -> None:
        """Review MINOR D: `OrderLineIn.selling_price_minor`'s own `le=` bound does not stop a
        *catalogue* price (unbounded) times a large quantity from overflowing `BigInteger` — this
        is refused cleanly instead of ever reaching `session.flush()`."""
        pricey = await _add_item(
            session,
            org_id,
            sku="HUGE-001",
            selling_price_minor=10**15,
            selling_price_currency="INR",
        )
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[{"inventory_item_id": str(pricey.id), "quantity": "1000000"}],
        )
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place", json={"version": order["version"]}
            )
        assert response.status_code == 409, response.text
        assert "HUGE-001" in response.text

    async def test_an_unpriced_line_is_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[{"inventory_item_id": str(shop.unpriced), "quantity": "1"}],
        )
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place", json={"version": order["version"]}
            )
        assert response.status_code == 409

    async def test_a_stale_version_is_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place",
                json={"version": order["version"] + 1},
            )
        assert response.status_code == 409

    async def test_the_current_version_is_not_spuriously_refused(
        self, client, org_id, data, shop
    ) -> None:
        """The version guard refuses a *stale* value; it must not refuse the caller's own
        current, unchanged value (the F1 review's lesson about a guard tripping on a resend that
        changed nothing)."""
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        assert placed["status"] == "PLACED"

    async def test_placing_an_already_placed_order_is_conflict(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{placed['id']}/place",
                json={"version": placed["version"]},
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_place(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place", json={"version": order["version"]}
            )
        assert response.status_code == 403

    async def test_cross_tenant_order_is_not_found(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        other = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/place", json={"version": order["version"]}
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ accept


class TestAcceptOrder:
    async def test_happy_path_reserves_every_line(
        self, client, org_id, data, session, shop
    ) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {"inventory_item_id": str(shop.widget), "quantity": "2"},
                {"inventory_item_id": str(shop.gizmo), "quantity": "3"},
            ],
        )
        placed = await _place(client, org_id, data, order)
        response = await _accept(client, org_id, data, placed)
        assert response.status_code == 200, response.text
        accepted = response.json()
        assert accepted["status"] == "ACCEPTED"
        assert accepted["version"] == 2

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == org_id,
                            InventoryReservation.reference_type == "order",
                            InventoryReservation.reference_id == uuid.UUID(accepted["id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert {r.status for r in rows} == {"active"}

    async def test_accept_marks_the_credit_check_as_not_implemented(
        self, client, org_id, data, session, shop
    ) -> None:
        """Review item 10: `check_credit_before_accept` is a documented no-op stub — the accept
        audit row and outbox event both say so, instead of implying a real check ran."""
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        response = await _accept(client, org_id, data, placed)
        assert response.status_code == 200, response.text
        accepted = response.json()

        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.accepted",
                        AuditLog.entity_id == uuid.UUID(accepted["id"]),
                    )
                )
            ).scalar_one()
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == "operations.orders.order.accepted",
                        OutboxEvent.aggregate_id == uuid.UUID(accepted["id"]),
                    )
                )
            ).scalar_one()

        assert audit.after["settings_snapshot"]["credit_check"] == "not_implemented"
        assert event.payload["settings_snapshot"]["credit_check"] == "not_implemented"

    async def test_two_line_accept_where_one_is_short_reserves_neither(
        self, client, org_id, data, session, shop
    ) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {"inventory_item_id": str(shop.widget), "quantity": "5"},  # plenty (100 on hand)
                {"inventory_item_id": str(shop.gizmo), "quantity": "500"},  # short: only 100
            ],
        )
        placed = await _place(client, org_id, data, order)
        widget_reserved_before = await _reserved_milli(session, org_id, shop.widget)
        gizmo_reserved_before = await _reserved_milli(session, org_id, shop.gizmo)

        response = await _accept(client, org_id, data, placed)
        assert response.status_code == 409, response.text

        # The problem shape ADR-0014 §11 promises: `InsufficientStock`'s own `type`/`errors[]`,
        # not just the bare status code.
        body = response.json()
        assert body["type"].endswith("/insufficient-stock")
        assert body["errors"], body
        assert {e["sku"] for e in body["errors"]} == {"GZM-002"}  # only the short line is named

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == org_id,
                            InventoryReservation.reference_type == "order",
                            InventoryReservation.reference_id == uuid.UUID(placed["id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []

        # The plan's literal exit criterion: neither bucket actually moved, not just "it refused".
        assert await _reserved_milli(session, org_id, shop.widget) == widget_reserved_before
        assert await _reserved_milli(session, org_id, shop.gizmo) == gizmo_reserved_before

    async def test_a_future_version_is_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        principal = _principal(org_id, [ACCEPT_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{placed['id']}/accept",
                json={"version": placed["version"] + 5},
            )
        assert response.status_code == 409

    async def test_an_older_version_is_also_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        principal = _principal(org_id, [ACCEPT_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{placed['id']}/accept",
                json={"version": placed["version"] - 1},
            )
        assert response.status_code == 409

    async def test_accepting_a_draft_is_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        response = await _accept(client, org_id, data, order)
        assert response.status_code == 409

    async def test_a_writer_without_accept_is_forbidden(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{placed['id']}/accept",
                json={"version": placed["version"]},
            )
        assert response.status_code == 403

    async def test_cross_tenant_order_is_not_found(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        other = principal_for(uuid.uuid4(), [ACCEPT_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/orders/{placed['id']}/accept",
                json={"version": placed["version"]},
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ hold / release


class TestHoldAndRelease:
    async def _accepted(self, client, org_id, data, shop):  # type: ignore[no-untyped-def]
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        response = await _accept(client, org_id, data, placed)
        assert response.status_code == 200, response.text
        return response.json()

    async def test_hold_requires_a_reason(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"]},
            )
        assert response.status_code == 422

    async def test_hold_then_release_returns_to_accepted(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            held = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"], "reason": "Customer asked to delay pickup"},
            )
        assert held.status_code == 200, held.text
        held_body = held.json()
        assert held_body["status"] == "ON_HOLD"

        with as_principal(principal):
            released = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/release",
                json={"version": held_body["version"]},
            )
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "ACCEPTED"

    async def test_release_of_an_order_not_on_hold_is_conflict(
        self, client, org_id, data, shop
    ) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/release",
                json={"version": accepted["version"]},
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_hold(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"], "reason": "delay"},
            )
        assert response.status_code == 403

    async def test_a_reader_cannot_release(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            held = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"], "reason": "Customer asked to delay pickup"},
            )
        assert held.status_code == 200, held.text
        reader = _principal(org_id, [READ_PERM], data)
        with as_principal(reader):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/release",
                json={"version": held.json()["version"]},
            )
        assert response.status_code == 403

    async def test_cross_tenant_hold_is_not_found(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        other = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"], "reason": "not yours"},
            )
        assert response.status_code == 404

    async def test_cross_tenant_release_is_not_found(self, client, org_id, data, shop) -> None:
        accepted = await self._accepted(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            held = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/hold",
                json={"version": accepted["version"], "reason": "Customer asked to delay pickup"},
            )
        assert held.status_code == 200, held.text
        other = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/release",
                json={"version": held.json()["version"]},
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ cancel


class TestCancelOrder:
    async def test_cancel_a_draft_needs_no_reservation_work(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [CANCEL_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/cancel",
                json={"version": order["version"], "reason": "Customer changed their mind"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCELLED"

    async def test_cancel_an_accepted_order_releases_every_reservation(
        self, client, org_id, data, session, shop
    ) -> None:
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {"inventory_item_id": str(shop.widget), "quantity": "2"},
                {"inventory_item_id": str(shop.gizmo), "quantity": "3"},
            ],
        )
        placed = await _place(client, org_id, data, order)
        accept_response = await _accept(client, org_id, data, placed)
        assert accept_response.status_code == 200, accept_response.text
        accepted = accept_response.json()

        principal = _principal(org_id, [CANCEL_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/cancel",
                json={"version": accepted["version"], "reason": "Stock damaged before handover"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCELLED"

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == org_id,
                            InventoryReservation.reference_type == "order",
                            InventoryReservation.reference_id == uuid.UUID(accepted["id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "the reservations must still exist, just no longer active"
        assert {r.status for r in rows} == {"released"}
        assert all(r.quantity_milli == r.released_milli for r in rows)

        await _assert_reconciles_clean(session, org_id)

    async def test_open_reservations_are_read_in_the_kernels_own_bucket_order(
        self, client, org_id, data, session, shop
    ) -> None:
        """N6 (security review): `open_reservations_for_order` must sort by the exact same bucket
        key (item, location, status, lot, roll) `app.inventory.ledger` locks buckets in — with no
        `ORDER BY` at all, two orders cancelling at once could release their (now-shared, F3
        staging) buckets in different orders and deadlock against each other."""
        order = await _create_order(
            client,
            org_id,
            data,
            shop,
            lines=[
                {"inventory_item_id": str(shop.widget), "quantity": "2"},
                {"inventory_item_id": str(shop.gizmo), "quantity": "3"},
            ],
        )
        placed = await _place(client, org_id, data, order)
        accept_response = await _accept(client, org_id, data, placed)
        assert accept_response.status_code == 200, accept_response.text
        accepted = accept_response.json()

        async with session.begin():
            rows = await order_selectors.open_reservations_for_order(
                session, org_id, uuid.UUID(accepted["id"])
            )
        assert len(rows) == 2
        item_ids = [r.inventory_item_id for r in rows]
        assert item_ids == sorted(item_ids, key=str)

    async def test_cancel_without_a_reason_is_422(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [CANCEL_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/cancel",
                json={"version": order["version"]},
            )
        assert response.status_code == 422

    async def test_a_stale_version_is_conflict(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [CANCEL_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/cancel",
                json={"version": order["version"] + 1, "reason": "stale"},
            )
        assert response.status_code == 409

    async def test_a_writer_without_cancel_is_forbidden(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [WRITE_PERM], data)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/cancel",
                json={"version": order["version"], "reason": "not allowed"},
            )
        assert response.status_code == 403

    async def test_cross_tenant_order_is_not_found(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        other = principal_for(uuid.uuid4(), [CANCEL_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/orders/{order['id']}/cancel",
                json={"version": order["version"], "reason": "not yours"},
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ list / get


class TestListAndGetOrders:
    async def test_list_filters_by_status_and_channel(self, client, org_id, data, shop) -> None:
        draft = await _create_order(client, org_id, data, shop, channel="phone")
        placed_order = await _create_order(client, org_id, data, shop, channel="manual")
        await _place(client, org_id, data, placed_order)

        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            by_status = await client.get(
                "/api/v1/operations/orders", params={"status": "DRAFT", "size": 50}
            )
        assert by_status.status_code == 200
        ids = {row["id"] for row in by_status.json()["items"]}
        assert draft["id"] in ids
        assert placed_order["id"] not in ids

        with as_principal(principal):
            by_channel = await client.get(
                "/api/v1/operations/orders", params={"channel": "phone", "size": 50}
            )
        assert by_channel.status_code == 200
        assert all(row["channel"] == "phone" for row in by_channel.json()["items"])

    async def test_get_returns_lines_with_sku(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [READ_PERM], data)
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/orders/{order['id']}")
        assert response.status_code == 200, response.text
        assert response.json()["lines"][0]["sku"] == "WGT-001"

    async def test_a_caller_without_read_is_forbidden(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        principal = _principal(org_id, [], data)
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/orders/{order['id']}")
        assert response.status_code == 403

    async def test_cross_tenant_get_is_not_found(self, client, org_id, data, shop) -> None:
        order = await _create_order(client, org_id, data, shop)
        other = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get(f"/api/v1/operations/orders/{order['id']}")
        assert response.status_code == 404

    async def test_a_caller_without_read_cannot_list(self, client, org_id, data, shop) -> None:
        principal = _principal(org_id, [], data)
        with as_principal(principal):
            response = await client.get("/api/v1/operations/orders", params={"size": 50})
        assert response.status_code == 403

    async def test_cross_tenant_list_excludes_other_org_orders(
        self, client, org_id, data, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        other = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/orders", params={"size": 50})
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert order["id"] not in ids


# ------------------------------------------------------------------ transitions (append-only)


class TestOrderTransitionsAreAppendOnly:
    async def test_every_transition_writes_one_row(
        self, client, org_id, data, session, shop
    ) -> None:
        order = await _create_order(client, org_id, data, shop)
        placed = await _place(client, org_id, data, order)
        accept_response = await _accept(client, org_id, data, placed)
        assert accept_response.status_code == 200, accept_response.text
        accepted = accept_response.json()

        principal = _principal(org_id, [CANCEL_PERM], data)
        with as_principal(principal):
            cancelled = await client.post(
                f"/api/v1/operations/orders/{accepted['id']}/cancel",
                json={"version": accepted["version"], "reason": "done with it"},
            )
        assert cancelled.status_code == 200, cancelled.text

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OrderTransition)
                        .where(
                            OrderTransition.organization_id == org_id,
                            OrderTransition.order_id == uuid.UUID(order["id"]),
                        )
                        .order_by(OrderTransition.at.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.from_status, r.to_status) for r in rows] == [
            ("DRAFT", "PLACED"),
            ("PLACED", "ACCEPTED"),
            ("ACCEPTED", "CANCELLED"),
        ]
