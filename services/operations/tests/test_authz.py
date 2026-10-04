"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only the purchase orders they raised must
also be refused another's by id.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from platform_core.auth import Scope, ScopeContext
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _numbers(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.purchase_orders_stmt(ctx))).scalars().all()
        return {po.number for po in rows}

    async def test_org_scope_sees_every_purchase_order(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.read", Scope.ORG)
        assert await self._numbers(session, ctx) == {"PO-2026-0148", "PO-2026-0140"}

    async def test_own_scope_sees_only_the_pos_you_raised(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id,
            "operations.procurement.purchase_order.read",
            Scope.OWN,
            subject_ref=data["subject_a"],
        )
        assert await self._numbers(session, ctx) == {"PO-2026-0148"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(
            org_id, "operations.procurement.purchase_order.read", Scope.OWN, subject_ref=None
        )
        assert await self._numbers(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id,
            "operations.procurement.purchase_order.read",
            Scope.OWN,
            subject_ref=data["subject_a"],
        )
        # "Priya" raised PO-2026-0140 (subject_b) — a search must not reach across scope.
        stmt = selectors.purchase_orders_stmt(ctx, q="priya")
        assert (await session.execute(stmt)).all() == []


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        """A caller with OWN scope is refused another's PO by id, but not their own."""
        # Capture the ids while the ORM objects are fresh: the refused request rolls its
        # transaction back, which expires the shared session's identity map, and a later
        # attribute access would then trigger a lazy refresh outside the request greenlet.
        po_a_id = str(data["po_a"].id)
        po_b_id = str(data["po_b"].id)
        principal = principal_for(
            org_id,
            ["operations.procurement.purchase_order.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            refused = await client.get(f"/api/v1/operations/purchase-orders/{po_b_id}")
            allowed = await client.get(f"/api/v1/operations/purchase-orders/{po_a_id}")
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["po_number"] == "PO-2026-0148"


# --------------------------------------------------------------------- endpoint authz


READ_ENDPOINTS = [
    "/api/v1/operations/suppliers",
    "/api/v1/operations/purchase-orders",
    "/api/v1/operations/inventory",
    "/api/v1/operations/warehouses",
    "/api/v1/operations/overview",
]

# The permission each read endpoint requires — a principal holding exactly one of these must be
# admitted to that endpoint and no other.
PERM_FOR = {
    "/api/v1/operations/suppliers": "operations.procurement.supplier.read:ORG",
    "/api/v1/operations/purchase-orders": "operations.procurement.purchase_order.read:ORG",
    "/api/v1/operations/inventory": "operations.inventory.item.read:ORG",
    "/api/v1/operations/warehouses": "operations.inventory.warehouse.read:ORG",
    "/api/v1/operations/overview": "operations.procurement.purchase_order.read:ORG",
}


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in READ_ENDPOINTS:
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to operations, but holding no operations permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in READ_ENDPOINTS:
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_the_right_permission_admits(self, client, org_id, data) -> None:
        """Each endpoint's own permission is enough to reach it."""
        for path in READ_ENDPOINTS:
            principal = principal_for(org_id, [PERM_FOR[path]], subject_ref=data["subject_a"])
            with as_principal(principal):
                response = await client.get(path)
            assert response.status_code == 200, path

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            [
                "operations.procurement.purchase_order.read:ORG",
                "operations.procurement.supplier.read:ORG",
            ],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            pos = await client.get("/api/v1/operations/purchase-orders")
            suppliers = await client.get("/api/v1/operations/suppliers")
        assert pos.status_code == 402
        assert suppliers.status_code == 402

    async def test_a_reader_cannot_receive_a_purchase_order(self, client, org_id, data) -> None:
        """Holding purchase_order.read but not .write cannot receive — the write guard is by
        name, not merely a UI affordance."""
        principal = principal_for(
            org_id,
            ["operations.procurement.purchase_order.read:ORG"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}/receive"
            )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "perms",
        [
            ["operations.procurement.purchase_order.write:ORG"],
            [
                "operations.procurement.purchase_order.write:ORG",
                "operations.procurement.receipt.write:ORG",
            ],
            [
                "operations.procurement.purchase_order.write:ORG",
                "operations.inventory.stock.receive:ORG",
            ],
            [
                "operations.procurement.receipt.write:ORG",
                "operations.inventory.stock.receive:ORG",
            ],
            [
                "operations.procurement.purchase_order.write:ORG",
                "operations.procurement.receipt.write:OWN",
                "operations.inventory.stock.receive:ORG",
            ],
        ],
        ids=[
            "po-write-only",
            "no-stock-receive",
            "no-receipt-write",
            "no-po-write",
            "receipt-write-own-only",
        ],
    )
    async def test_receiving_a_po_needs_the_warehouse_keys_not_only_po_write(
        self, client, org_id, data, perms
    ) -> None:
        """`POST /purchase-orders/{id}/receive` posts stock, so `purchase_order.write` alone (a
        buying permission) is not enough: it also needs `receipt.write` and `stock.receive` at ORG,
        the same two keys as `POST /goods-receipts`."""
        principal = principal_for(org_id, perms, subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}/receive"
            )
        assert response.status_code == 403

    async def test_a_writer_may_receive_a_purchase_order(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id,
            [
                "operations.procurement.purchase_order.write:ORG",
                "operations.procurement.receipt.write:ORG",
                "operations.inventory.stock.receive:ORG",
            ],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/purchase-orders/{data['po_a'].id}/receive"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "received"
        assert body["received_date"] is not None
        # Every line's received quantity is filled to the ordered quantity.
        assert all(line["received_qty"] == line["quantity"] for line in body["line_items"])

    async def test_an_own_reader_sees_only_their_own_pos_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["operations.procurement.purchase_order.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.get("/api/v1/operations/purchase-orders")
        assert response.status_code == 200
        numbers = {item["po_number"] for item in response.json()["items"]}
        assert numbers == {"PO-2026-0148"}


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_receiving_an_already_received_po_is_refused(self, session, org_id, data) -> None:
        from app import services
        from platform_core.errors import ConflictProblem

        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="cannot be received"):
            async with session.begin():
                await services.receive_purchase_order(session, ctx, data["po_b"])

    async def test_a_po_against_an_unknown_supplier_is_refused(self, session, org_id, data) -> None:
        import datetime

        from app import services
        from platform_core.errors import NotFoundProblem

        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="supplier"):
            async with session.begin():
                await services.create_purchase_order(
                    session,
                    ctx,
                    number="PO-9999",
                    supplier_code="SUP-NOPE",
                    created_date=datetime.date(2026, 8, 1),
                    expected_date=datetime.date(2026, 8, 10),
                )

    async def test_a_duplicate_po_number_is_refused(self, session, org_id, data) -> None:
        import datetime

        from app import services
        from platform_core.errors import ConflictProblem

        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_purchase_order(
                    session,
                    ctx,
                    number="PO-2026-0148",
                    supplier_id=data["meridian"].id,
                    created_date=datetime.date(2026, 8, 1),
                    expected_date=datetime.date(2026, 8, 10),
                )

    async def test_receiving_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app import services
        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await services.receive_purchase_order(session, ctx, data["po_a"])
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "purchase_order.received",
                    AuditLog.entity_id == data["po_a"].id,
                )
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == data["po_a"].id,
                        OutboxEvent.event_type == "operations.procurement.purchase_order.received",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].published_at is None
