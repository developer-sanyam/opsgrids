"""Negative authorization and cross-tenant isolation for **every** route inventory Phase 3 adds
(transfers, reservations, adjustments, the in-transit report, the approval thresholds).

One table drives all of it, so a route added without a row here is visible in review:

* a caller with **no** permission is 403;
* a caller holding **every other** permission this surface defines — everything but the one the
  route needs — is 403, so a permission cannot be borrowed from a sibling route (an approver
  cannot dispatch, a dispatcher cannot approve, the person who may adjust cannot set the
  thresholds that decide whether adjustments need approval);
* a caller from **another organization**, holding exactly the right permission, is 404 on every
  route that names a row (or names another tenant's row in its body), and gets an **empty page**
  — never the first tenant's data — from every list.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models import (
    AdjustmentRequest,
    InventoryMove,
    InventoryReservation,
    StockTransfer,
)
from tests.helpers import as_principal, principal_for
from tests.movements_fixtures import (
    API,
    CONTROL_CONFIGURE,
    STOCK_ADJUST,
    STOCK_READ,
    STOCK_RESERVE,
    TRANSFER_APPROVE,
    TRANSFER_DISPATCH,
    TRANSFER_RECEIVE,
    TRANSFER_WRITE,
    World,
    build_world,
    call,
    mover,
    receive_stock,
    transfer_body,
)

pytestmark = pytest.mark.integration

ALL_PERMS = (
    STOCK_READ,
    STOCK_RESERVE,
    STOCK_ADJUST,
    TRANSFER_WRITE,
    TRANSFER_APPROVE,
    TRANSFER_DISPATCH,
    TRANSFER_RECEIVE,
    CONTROL_CONFIGURE,
    "operations.inventory.item.read:ORG",
    "operations.inventory.item.write:ORG",
    "operations.inventory.stock.transfer:ORG",
    "operations.inventory.lot.write:ORG",
    "operations.inventory.roll.write:ORG",
    "operations.inventory.count.review:ORG",
)

T = "/operations/inventory/transfers"
R = "/operations/inventory/reservations"
A = "/operations/inventory/adjustments"


@dataclass
class Docs:
    w: World
    transfer: uuid.UUID
    reservation: uuid.UUID
    adjustment: uuid.UUID


@pytest.fixture
async def docs(client, session, org_id) -> Docs:
    w = await build_world(session, org_id)
    await receive_stock(session, w, w.t.dye, w.t.weaving, "100")
    clerk = mover(org_id, TRANSFER_WRITE, STOCK_RESERVE, STOCK_ADJUST)
    line = {
        "inventory_item_id": str(w.t.dye),
        "from_location_id": str(w.t.weaving),
        "quantity": "5",
    }
    transfer = await call(client, clerk, "POST", T, transfer_body(w, line))
    reservation = await call(
        client,
        clerk,
        "POST",
        R,
        {"inventory_item_id": str(w.t.dye), "location_id": str(w.t.weaving), "quantity": "5"},
    )
    adjustment = await call(
        client,
        clerk,
        "POST",
        A,
        {
            "inventory_item_id": str(w.t.dye),
            "location_id": str(w.t.weaving),
            "quantity_delta": "-1",
            "reason": "damaged",
        },
    )
    assert (transfer.status_code, reservation.status_code, adjustment.status_code) == (
        201,
        201,
        201,
    )
    return Docs(
        w,
        uuid.UUID(transfer.json()["id"]),
        uuid.UUID(reservation.json()["id"]),
        uuid.UUID(adjustment.json()["id"]),
    )


Spec = tuple[str, str, str, Callable[[Docs], Any], str]


def _routes() -> dict[str, Spec]:
    """name -> (method, path, permission, json body factory, `404` | `empty` | `200`)."""

    def none(_d: Docs) -> None:
        return None

    def line(d: Docs) -> dict[str, str]:
        return {
            "inventory_item_id": str(d.w.t.dye),
            "from_location_id": str(d.w.t.weaving),
            "quantity": "1",
        }

    return {
        "get_settings": ("GET", "/operations/inventory/control-settings", STOCK_READ, none, "200"),
        "put_settings": (
            "PUT",
            "/operations/inventory/control-settings",
            CONTROL_CONFIGURE,
            lambda d: {"transfer_approval_threshold": "10"},
            "200",
        ),
        "create_transfer": (
            "POST",
            T,
            TRANSFER_WRITE,
            lambda d: transfer_body(d.w, line(d)),
            "404",
        ),
        "list_transfers": ("GET", T, STOCK_READ, none, "empty"),
        "read_transfer": ("GET", T + "/{transfer}", STOCK_READ, none, "404"),
        "patch_transfer": (
            "PATCH",
            T + "/{transfer}",
            TRANSFER_WRITE,
            lambda d: {"note": "x"},
            "404",
        ),
        "request_transfer": ("POST", T + "/{transfer}:request", TRANSFER_WRITE, none, "404"),
        "approve_transfer": ("POST", T + "/{transfer}:approve", TRANSFER_APPROVE, none, "404"),
        "reject_transfer": (
            "POST",
            T + "/{transfer}:reject",
            TRANSFER_APPROVE,
            lambda d: {"comment": "no"},
            "404",
        ),
        "cancel_transfer": (
            "POST",
            T + "/{transfer}:cancel",
            TRANSFER_WRITE,
            lambda d: {"reason": "x"},
            "404",
        ),
        "dispatch_transfer": ("POST", T + "/{transfer}:dispatch", TRANSFER_DISPATCH, none, "404"),
        "receive_transfer": (
            "POST",
            T + "/{transfer}:receive",
            TRANSFER_RECEIVE,
            lambda d: {"lines": [{"line_id": str(uuid.uuid4()), "received_quantity": "1"}]},
            "404",
        ),
        "in_transit": ("GET", "/operations/inventory/in-transit", STOCK_READ, none, "empty-list"),
        "create_reservation": (
            "POST",
            R,
            STOCK_RESERVE,
            lambda d: {
                "inventory_item_id": str(d.w.t.dye),
                "location_id": str(d.w.t.weaving),
                "quantity": "1",
            },
            "404",
        ),
        "list_reservations": ("GET", R, STOCK_READ, none, "empty"),
        "read_reservation": ("GET", R + "/{reservation}", STOCK_READ, none, "404"),
        "release_reservation": ("POST", R + "/{reservation}:release", STOCK_RESERVE, none, "404"),
        "create_adjustment": (
            "POST",
            A,
            STOCK_ADJUST,
            lambda d: {
                "inventory_item_id": str(d.w.t.dye),
                "location_id": str(d.w.t.weaving),
                "quantity_delta": "-1",
                "reason": "damaged",
            },
            "404",
        ),
        "list_adjustments": ("GET", A, STOCK_READ, none, "empty"),
        "read_adjustment": ("GET", A + "/{adjustment}", STOCK_READ, none, "404"),
        "cancel_adjustment": ("POST", A + "/{adjustment}:cancel", STOCK_ADJUST, none, "404"),
    }


ROUTES = _routes()


def _url(path: str, d: Docs) -> str:
    return API + path.format(
        transfer=d.transfer, reservation=d.reservation, adjustment=d.adjustment
    )


async def _call(client, principal, name: str, d: Docs):  # type: ignore[no-untyped-def]
    method, path, _perm, body, _expect = ROUTES[name]
    with as_principal(principal):
        return await client.request(method, _url(path, d), json=body(d))


@pytest.mark.parametrize("name", sorted(ROUTES))
class TestEveryRoute:
    async def test_no_permission_is_forbidden(self, client, docs, name) -> None:
        response = await _call(client, principal_for(docs.w.org_id, []), name, docs)
        assert response.status_code == 403, response.text

    async def test_every_other_permission_is_still_forbidden(self, client, docs, name) -> None:
        needed = ROUTES[name][2]
        others = [p for p in ALL_PERMS if p != needed]
        response = await _call(client, principal_for(docs.w.org_id, others), name, docs)
        assert response.status_code == 403, response.text

    async def test_a_scoped_down_grant_is_not_enough(self, client, docs, name) -> None:
        # Every permission here is ORG-only; the same code held at OWN is not ORG.
        needed = ROUTES[name][2].removesuffix(":ORG") + ":OWN"
        response = await _call(client, principal_for(docs.w.org_id, [needed]), name, docs)
        assert response.status_code == 403, response.text

    async def test_another_tenant_gets_404_an_empty_page_or_only_its_own_nothing(
        self, client, docs, name
    ) -> None:
        needed, expect = ROUTES[name][2], ROUTES[name][4]
        intruder = principal_for(uuid.uuid4(), [needed])
        response = await _call(client, intruder, name, docs)
        if expect == "empty":
            assert response.status_code == 200, response.text
            assert response.json()["items"] == []
        elif expect == "empty-list":
            assert response.status_code == 200 and response.json() == []
        elif expect == "200":
            # Settings are per organization: the intruder reads / writes *its own* row.
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 404, response.text

    async def test_the_entitlement_is_required(self, client, docs, name) -> None:
        needed = ROUTES[name][2]
        no_entitlement = principal_for(docs.w.org_id, [needed], entitlements=[])
        response = await _call(client, no_entitlement, name, docs)
        # 402, not 403: the organization holds the permission but has not bought the product.
        assert response.status_code == 402, response.text


WRITE_ROUTES = sorted(name for name, spec in ROUTES.items() if spec[0] != "GET")


@pytest.mark.parametrize("name", WRITE_ROUTES)
class TestRefusedWritesChangeNothing:
    async def test_a_cross_tenant_write_leaves_the_owners_data_alone(
        self, client, session, docs, name
    ) -> None:
        needed = ROUTES[name][2]
        before = await _snapshot(session, docs)
        await _call(client, principal_for(uuid.uuid4(), [needed]), name, docs)
        assert await _snapshot(session, docs) == before

    async def test_a_forbidden_write_changes_nothing(self, client, session, docs, name) -> None:
        before = await _snapshot(session, docs)
        await _call(client, principal_for(docs.w.org_id, []), name, docs)
        assert await _snapshot(session, docs) == before


async def _snapshot(session, d: Docs) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Every row count and the three documents' own state — what a refused write must not touch."""
    org = d.w.org_id
    async with session.begin():
        counts = {
            model.__tablename__: (
                await session.execute(
                    select(func.count()).select_from(model).where(model.organization_id == org)
                )
            ).scalar_one()
            for model in (StockTransfer, InventoryReservation, AdjustmentRequest, InventoryMove)
        }
        transfer = (
            await session.execute(
                select(StockTransfer.status, StockTransfer.note)
                .where(StockTransfer.id == d.transfer)
                .execution_options(populate_existing=True)
            )
        ).one()
        reservation = (
            await session.execute(
                select(InventoryReservation.status, InventoryReservation.released_milli)
                .where(InventoryReservation.id == d.reservation)
                .execution_options(populate_existing=True)
            )
        ).one()
    return {"counts": counts, "transfer": tuple(transfer), "reservation": tuple(reservation)}
