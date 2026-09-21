"""Purchase order create / edit / delete draft / lifecycle (approve, receive, close, cancel)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import services
from app.config import settings
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration

PO_READ = "operations.procurement.purchase_order.read:ORG"
PO_WRITE = "operations.procurement.purchase_order.write:ORG"
PO_APPROVE = "operations.procurement.purchase_order.approve:ORG"

TOMORROW = (date.today() + timedelta(days=7)).isoformat()


def _valid_lines() -> list[dict]:
    return [
        {
            "sku": "RM-STL-4501",
            "description": "Cold-rolled steel coil",
            "quantity": 10,
            "uom": "MT",
            "unit_price_minor": 100_00,
        }
    ]


class TestCreatePurchaseOrder:
    async def test_happy_path_gets_a_server_number_and_starts_draft(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "draft"
        assert body["po_number"].startswith(f"PO-{date.today().year}-")
        assert body["currency"] == data["meridian"].currency

    async def test_an_own_scope_user_sees_the_po_they_just_raised(
        self, client, org_id, data
    ) -> None:
        subject = uuid.uuid4()
        principal = principal_for(
            org_id, ["operations.procurement.purchase_order.write:OWN"], subject_ref=subject
        )
        with as_principal(principal):
            created = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert created.status_code == 201

        with as_principal(
            principal_for(
                org_id,
                ["operations.procurement.purchase_order.read:OWN"],
                subject_ref=subject,
            )
        ):
            listed = await client.get("/api/v1/operations/purchase-orders")
            fetched = await client.get(f"/api/v1/operations/purchase-orders/{created.json()['id']}")
        assert created.json()["id"] in {item["id"] for item in listed.json()["items"]}
        assert fetched.status_code == 200

    async def test_an_on_hold_supplier_is_refused(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id,
            [PO_WRITE, "operations.procurement.supplier.write:ORG"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            await client.patch(
                f"/api/v1/operations/suppliers/{data['meridian'].id}", json={"status": "on_hold"}
            )
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 409

    async def test_an_unknown_warehouse_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Nowhere Depot",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 422

    async def test_no_lines_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": [],
                },
            )
        assert response.status_code == 422

    async def test_server_owned_fields_are_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                    "status": "approved",
                },
            )
        assert response.status_code == 422

    async def test_unknown_supplier_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(uuid.uuid4()),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 404

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_READ], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 403


class TestConcurrentPoNumbers:
    async def test_two_concurrent_creates_never_collide(self, org_id: uuid.UUID, data) -> None:
        def _connect_args() -> dict:
            return {"server_settings": {"search_path": f"{SCHEMA},public"}}

        async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
            engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
            maker = async_sessionmaker(engine, expire_on_commit=False)
            session = maker()

            @event.listens_for(session.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return session, engine

        async def _raise(session: AsyncSession) -> str:
            write = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
            async with session.begin():
                po = await services.raise_purchase_order(
                    session,
                    write,
                    supplier_id=data["meridian"].id,
                    warehouse="Pune Plant",
                    expected_date=date.today() + timedelta(days=7),
                    lines=_valid_lines(),
                )
                return po.number

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            n1, n2 = await asyncio.gather(_raise(s1), _raise(s2))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert n1 != n2


class TestUpdateDraftPurchaseOrder:
    async def _create_draft(self, client, org_id, data, subject) -> dict:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=subject)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 201
        return response.json()

    async def test_editing_a_draft_replaces_its_lines(self, client, org_id, data) -> None:
        subject = data["subject_a"]
        draft = await self._create_draft(client, org_id, data, subject)

        principal = principal_for(org_id, [PO_WRITE], subject_ref=subject)
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/purchase-orders/{draft['id']}",
                json={
                    "lines": [
                        {
                            "sku": "RM-STL-4501",
                            "quantity": 5,
                            "unit_price_minor": 200_00,
                        }
                    ]
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert len(body["line_items"]) == 1
        assert body["line_items"][0]["quantity"] == 5

    async def test_editing_an_approved_po_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}",
                json={"notes": "trying to edit an approved PO"},
            )
        assert response.status_code == 409

    async def test_an_own_user_cannot_edit_another_users_draft(self, client, org_id, data) -> None:
        draft = await self._create_draft(client, org_id, data, data["subject_a"])
        other_principal = principal_for(
            org_id,
            ["operations.procurement.purchase_order.write:OWN"],
            subject_ref=data["subject_b"],
        )
        with as_principal(other_principal):
            response = await client.patch(
                f"/api/v1/operations/purchase-orders/{draft['id']}", json={"notes": "hijack"}
            )
        assert response.status_code == 403

    async def test_deleting_a_draft(self, client, org_id, data) -> None:
        draft = await self._create_draft(client, org_id, data, data["subject_a"])
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/operations/purchase-orders/{draft['id']}")
        assert response.status_code == 204

    async def test_deleting_an_approved_po_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/operations/purchase-orders/{data['po_a'].id}")
        assert response.status_code == 409


class TestPoLifecycle:
    async def _create_draft(self, client, org_id, data) -> dict:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
        assert response.status_code == 201
        return response.json()

    async def test_a_user_cannot_approve_their_own_po(self, client, org_id, data) -> None:
        draft = await self._create_draft(client, org_id, data)
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/approve"
            )
        assert response.status_code == 403

    async def test_default_seat_raiser_can_neither_approve_nor_receive_their_own_draft(
        self, client, org_id, data
    ) -> None:
        """`OPERATIONS_USER` — the default seat — holds only `write:OWN`. Raising a PO and then
        immediately approving or receiving it themselves is exactly the bypass approval exists to
        close: approving stays 403 (a separate permission), and receiving a draft is now a 409
        rather than a silent approval-skip, because `draft -> received` is not an edge in
        `domain.PO_TRANSITIONS` any more."""
        subject = uuid.uuid4()
        raiser = principal_for(
            org_id, ["operations.procurement.purchase_order.write:OWN"], subject_ref=subject
        )
        with as_principal(raiser):
            created = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
            assert created.status_code == 201
            po_id = created.json()["id"]

            approve_response = await client.post(
                f"/api/v1/operations/purchase-orders/{po_id}/approve"
            )
            receive_response = await client.post(
                f"/api/v1/operations/purchase-orders/{po_id}/receive"
            )
        assert approve_response.status_code == 403
        assert receive_response.status_code == 409
        assert "approved" in receive_response.json()["detail"].lower()

    async def test_raiser_can_receive_their_own_po_once_an_org_approver_signs_off(
        self, client, org_id, data
    ) -> None:
        """The other half of the same story: once an ORG-scope approver has approved the PO, the
        `write:OWN` raiser who raised it may receive it themselves — approval is the gate, not a
        blanket "the raiser can never touch it again"."""
        subject = uuid.uuid4()
        raiser = principal_for(
            org_id, ["operations.procurement.purchase_order.write:OWN"], subject_ref=subject
        )
        with as_principal(raiser):
            created = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
            assert created.status_code == 201
            po_id = created.json()["id"]

        approver = principal_for(org_id, [PO_APPROVE], subject_ref=data["subject_a"])
        with as_principal(approver):
            approved = await client.post(f"/api/v1/operations/purchase-orders/{po_id}/approve")
        assert approved.status_code == 200

        with as_principal(raiser):
            received = await client.post(f"/api/v1/operations/purchase-orders/{po_id}/receive")
        assert received.status_code == 200
        assert received.json()["status"] == "received"

    async def test_spend_is_committed_exactly_once_across_approve_and_receive(
        self, client, org_id, data
    ) -> None:
        """`approve` commits the supplier's lifetime spend; `receive` must not commit it a second
        time now that `draft -> received` is gone — there is no path left where receive is the
        first commit."""
        draft = await self._create_draft(client, org_id, data)
        supplier_read = principal_for(
            org_id, ["operations.procurement.supplier.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(supplier_read):
            before = await client.get(f"/api/v1/operations/suppliers/{data['meridian'].id}")
        before_spend = before.json()["spend_minor"]

        approver = principal_for(org_id, [PO_APPROVE, PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(approver):
            approved = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/approve"
            )
        assert approved.status_code == 200
        line_total = sum(line["quantity"] * line["unit_price_minor"] for line in _valid_lines())

        with as_principal(supplier_read):
            after_approve = await client.get(f"/api/v1/operations/suppliers/{data['meridian'].id}")
        assert after_approve.json()["spend_minor"] == before_spend + line_total

        with as_principal(approver):
            received = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/receive"
            )
        assert received.status_code == 200

        with as_principal(supplier_read):
            after_receive = await client.get(f"/api/v1/operations/suppliers/{data['meridian'].id}")
        # Unchanged from right after approve: receive did not commit spend a second time.
        assert after_receive.json()["spend_minor"] == before_spend + line_total

    async def test_a_writer_cannot_cancel_an_approved_po_without_the_approve_permission(
        self, client, org_id, data
    ) -> None:
        """Cancelling an approved PO reverses the spend an ORG-scope approver committed. A buyer
        holding only `write:OWN` unwinding that unilaterally is the same separation-of-duty hole
        as approving their own PO, so it needs `purchase_order.approve` too."""
        draft = await self._create_draft(client, org_id, data)
        approver = principal_for(
            org_id, [PO_APPROVE, PO_WRITE, PO_READ], subject_ref=data["subject_a"]
        )
        with as_principal(approver):
            approved = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/approve"
            )
        assert approved.status_code == 200

        writer_only = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(writer_only):
            response = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/cancel",
                json={"reason": "no longer needed"},
            )
        assert response.status_code == 403

        with as_principal(approver):
            still_approved = await client.get(f"/api/v1/operations/purchase-orders/{draft['id']}")
        assert still_approved.json()["status"] == "approved"

    async def test_a_writer_can_cancel_their_own_draft_without_the_approve_permission(
        self, client, org_id, data
    ) -> None:
        """A draft never committed spend, so cancelling one is the same reach as deleting one —
        `write:OWN` is enough."""
        subject = uuid.uuid4()
        raiser = principal_for(
            org_id, ["operations.procurement.purchase_order.write:OWN"], subject_ref=subject
        )
        with as_principal(raiser):
            created = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": _valid_lines(),
                },
            )
            assert created.status_code == 201
            po_id = created.json()["id"]

            response = await client.post(
                f"/api/v1/operations/purchase-orders/{po_id}/cancel",
                json={"reason": "changed my mind"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    async def test_approve_receive_close_walks_cleanly(self, client, org_id, data) -> None:
        draft = await self._create_draft(client, org_id, data)
        approver = principal_for(org_id, [PO_APPROVE, PO_WRITE], subject_ref=data["subject_a"])

        with as_principal(approver):
            approved = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/approve"
            )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        with as_principal(approver):
            received = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/receive"
            )
        assert received.status_code == 200
        assert received.json()["status"] == "received"

        with as_principal(approver):
            closed = await client.post(f"/api/v1/operations/purchase-orders/{draft['id']}/close")
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

    async def test_cancel_an_approved_po_reverses_open_pos(self, client, org_id, data) -> None:
        draft = await self._create_draft(client, org_id, data)
        approver = principal_for(org_id, [PO_APPROVE, PO_WRITE], subject_ref=data["subject_a"])
        supplier_read = principal_for(
            org_id, ["operations.procurement.supplier.read:ORG"], subject_ref=data["subject_a"]
        )
        supplier_url = f"/api/v1/operations/suppliers/{data['meridian'].id}"
        with as_principal(supplier_read):
            before = (await client.get(supplier_url)).json()

        with as_principal(approver):
            await client.post(f"/api/v1/operations/purchase-orders/{draft['id']}/approve")
        with as_principal(supplier_read):
            committed = (await client.get(supplier_url)).json()
        assert committed["spend_minor"] > before["spend_minor"]

        with as_principal(approver):
            cancelled = await client.post(
                f"/api/v1/operations/purchase-orders/{draft['id']}/cancel",
                json={"reason": "supplier could not deliver"},
            )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        # The approver's cancel hands back exactly what their approve committed.
        with as_principal(supplier_read):
            after = (await client.get(supplier_url)).json()
        assert after["spend_minor"] == before["spend_minor"]

    async def test_receiving_twice_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_WRITE], subject_ref=data["subject_a"])
        with as_principal(principal):
            first = await client.post(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}/receive"
            )
            second = await client.post(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}/receive"
            )
        assert first.status_code == 200
        assert second.status_code == 409

    async def test_receiving_posts_stock_for_a_matching_sku(self, client, org_id, data) -> None:
        # Create a draft whose line SKU matches an existing inventory item, approve then receive.
        item_sku = data["low_stock_item"].sku
        before_on_hand = data["low_stock_item"].on_hand
        principal = principal_for(org_id, [PO_WRITE, PO_APPROVE], subject_ref=data["subject_a"])
        with as_principal(principal):
            created = await client.post(
                "/api/v1/operations/purchase-orders",
                json={
                    "supplier_id": str(data["meridian"].id),
                    "warehouse": "Pune Plant",
                    "expected_date": TOMORROW,
                    "lines": [
                        {"sku": item_sku, "quantity": 3, "unit_price_minor": 100_00},
                        {"sku": "SVC-FREIGHT", "quantity": 1, "unit_price_minor": 500_00},
                    ],
                },
            )
            po_id = created.json()["id"]
            await client.post(f"/api/v1/operations/purchase-orders/{po_id}/approve")
            received = await client.post(f"/api/v1/operations/purchase-orders/{po_id}/receive")
        assert received.status_code == 200
        # The freight line matches no inventory item: it posts no stock, and the answer says so.
        assert received.json()["unmatched_skus"] == ["SVC-FREIGHT"]

        with as_principal(
            principal_for(
                org_id, ["operations.inventory.item.read:ORG"], subject_ref=data["subject_a"]
            )
        ):
            item = await client.get(f"/api/v1/operations/inventory/{data['low_stock_item'].id}")
            movements = await client.get(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/movements"
            )
        assert item.json()["on_hand"] == before_on_hand + 3
        kinds = [m["kind"] for m in movements.json()["items"]]
        assert "receipt" in kinds


class TestFilterBySupplier:
    async def test_supplier_id_filters_the_list(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [PO_READ], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/operations/purchase-orders",
                params={"supplier_id": str(data["volt"].id)},
            )
        assert response.status_code == 200
        numbers = {item["po_number"] for item in response.json()["items"]}
        assert numbers == {"PO-2026-0140"}
