"""Negative authorization and cross-tenant isolation for **every** route inventory Phase 2 adds:
requisitions, goods receipts, the receiving view, and per-item unit conversions.

One table drives all of it, so a route added without a row here is visible in review:

* a caller with **no** permission is 403;
* a caller holding **every other** receiving permission — all but the one(s) the route needs — is
  403, so a permission cannot be borrowed from a sibling route; routes that need two keys
  (posting a receipt, converting a requisition) are checked with each key missing in turn;
* a caller from **another organization**, holding exactly the right permissions, is 404 on every
  route that names a row (or a row of another tenant in its body), and gets an **empty page** —
  never the first tenant's data — from every list.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from tests.helpers import as_principal, principal_for
from tests.receiving_fixtures import (
    ALL_RECEIVING_PERMS,
    API,
    ITEM_READ,
    ITEM_WRITE,
    PO_WRITE,
    RECEIPT_READ,
    RECEIPT_WRITE,
    REQ_APPROVE,
    REQ_READ,
    REQ_WRITE,
    STOCK_RECEIVE,
    RWorld,
    build_rworld,
    future,
    make_po,
)

pytestmark = pytest.mark.integration


@dataclass
class Ids:
    w: RWorld
    requisition: uuid.UUID
    po: uuid.UUID
    po_line: uuid.UUID
    receipt: uuid.UUID
    conversion: uuid.UUID

    @property
    def item(self) -> uuid.UUID:
        return self.w.buttons


@pytest.fixture
async def ids(client, session, org_id) -> Ids:
    w = await build_rworld(session, org_id)
    po_id, lines = await make_po(
        session, w, [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 100}]
    )
    with as_principal(principal_for(org_id, [REQ_WRITE, REQ_READ])):
        made = await client.post(
            f"{API}/operations/purchase-requisitions",
            json={"lines": [{"inventory_item_id": str(w.buttons), "quantity": "5"}]},
        )
    assert made.status_code == 201, made.text
    with as_principal(principal_for(org_id, [RECEIPT_WRITE, STOCK_RECEIVE])):
        posted = await client.post(
            f"{API}/operations/goods-receipts",
            json={
                "purchase_order_id": str(po_id),
                "lines": [{"purchase_order_line_id": str(lines["BTN-14MM"]), "quantity": "10"}],
            },
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
    assert posted.status_code == 201, posted.text
    return Ids(
        w=w,
        requisition=uuid.UUID(made.json()["id"]),
        po=po_id,
        po_line=lines["BTN-14MM"],
        receipt=uuid.UUID(posted.json()["id"]),
        conversion=w.box_conversion_id,
    )


Spec = tuple[str, str, list[str], Callable[[Ids], Any], str]


def _routes() -> dict[str, Spec]:
    """name -> (method, path, required permissions, json body factory or None, `404` | `empty`)."""

    def none(_i: Ids) -> None:
        return None

    return {
        "list_conversions": (
            "GET",
            "/operations/inventory/{item}/uom-conversions",
            [ITEM_READ],
            none,
            "404",
        ),
        "create_conversion": (
            "POST",
            "/operations/inventory/{item}/uom-conversions",
            [ITEM_WRITE],
            lambda i: {"uom": "carton", "numerator": 24},
            "404",
        ),
        "update_conversion": (
            "PATCH",
            "/operations/inventory/uom-conversions/{conversion}",
            [ITEM_WRITE],
            lambda i: {"numerator": 6},
            "404",
        ),
        "delete_conversion": (
            "DELETE",
            "/operations/inventory/uom-conversions/{conversion}",
            [ITEM_WRITE],
            none,
            "404",
        ),
        "list_requisitions": (
            "GET",
            "/operations/purchase-requisitions",
            [REQ_READ],
            none,
            "empty",
        ),
        "create_requisition": (
            "POST",
            "/operations/purchase-requisitions",
            [REQ_WRITE],
            lambda i: {"lines": [{"inventory_item_id": str(i.item), "quantity": "5"}]},
            "404",
        ),
        "read_requisition": (
            "GET",
            "/operations/purchase-requisitions/{requisition}",
            [REQ_READ],
            none,
            "404",
        ),
        "update_requisition": (
            "PATCH",
            "/operations/purchase-requisitions/{requisition}",
            [REQ_WRITE],
            lambda i: {"reason": "x"},
            "404",
        ),
        "submit_requisition": (
            "POST",
            "/operations/purchase-requisitions/{requisition}/submit",
            [REQ_WRITE],
            none,
            "404",
        ),
        "approve_requisition": (
            "POST",
            "/operations/purchase-requisitions/{requisition}/approve",
            [REQ_APPROVE],
            lambda i: {},
            "404",
        ),
        "reject_requisition": (
            "POST",
            "/operations/purchase-requisitions/{requisition}/reject",
            [REQ_APPROVE],
            lambda i: {"reason": "no"},
            "404",
        ),
        "cancel_requisition": (
            "POST",
            "/operations/purchase-requisitions/{requisition}/cancel",
            [REQ_WRITE],
            lambda i: {},
            "404",
        ),
        "convert_requisition": (
            "POST",
            "/operations/purchase-requisitions/{requisition}/convert",
            [REQ_WRITE, PO_WRITE],
            lambda i: {
                "supplier_id": str(i.w.supplier_id),
                "expected_date": future().isoformat(),
                "warehouse": "Weaving Store",
            },
            "404",
        ),
        "receiving_context": (
            "GET",
            "/operations/purchase-orders/{po}/receiving",
            [RECEIPT_READ],
            none,
            "404",
        ),
        "post_receipt": (
            "POST",
            "/operations/goods-receipts",
            [RECEIPT_WRITE, STOCK_RECEIVE],
            lambda i: {
                "purchase_order_id": str(i.po),
                "lines": [{"purchase_order_line_id": str(i.po_line), "quantity": "1"}],
            },
            "404",
        ),
        "list_receipts": ("GET", "/operations/goods-receipts", [RECEIPT_READ], none, "empty"),
        "read_receipt": (
            "GET",
            "/operations/goods-receipts/{receipt}",
            [RECEIPT_READ],
            none,
            "404",
        ),
    }


ROUTES = _routes()


def _call(client, i: Ids, spec: Spec, perms: list[str], org: uuid.UUID):  # type: ignore[no-untyped-def]
    method, template, _required, body, _expect = spec
    path = template.format(
        item=i.item,
        conversion=i.conversion,
        requisition=i.requisition,
        po=i.po,
        receipt=i.receipt,
    )
    json_body = body(i)

    async def run():  # type: ignore[no-untyped-def]
        with as_principal(principal_for(org, perms)):
            return await client.request(
                method,
                API + path,
                json=json_body,
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )

    return run()


def test_every_route_this_phase_adds_has_a_row() -> None:
    from app.main import app

    served = {
        (method.upper(), path.removeprefix("/api/v1"))
        for path, operations in app.openapi()["paths"].items()
        for method, op in operations.items()
        if "receiving" in op.get("tags", [])
    }
    path_of = {
        "{item}": "{item_id}",
        "{conversion}": "{conversion_id}",
        "{requisition}": "{requisition_id}",
        "{po}": "{po_id}",
        "{receipt}": "{receipt_id}",
    }
    covered = set()
    for method, template, *_ in ROUTES.values():
        path = template
        for ours, theirs in path_of.items():
            path = path.replace(ours, theirs)
        covered.add((method, path))
    assert served <= covered, f"routes without an authz row: {sorted(served - covered)}"
    assert covered <= served, f"rows for routes that do not exist: {sorted(covered - served)}"


@pytest.mark.parametrize("name", list(ROUTES))
async def test_no_permission_is_403(client, org_id, ids, name) -> None:
    response = await _call(client, ids, ROUTES[name], [], org_id)
    assert response.status_code == 403, (name, response.text)


@pytest.mark.parametrize("name", list(ROUTES))
async def test_every_other_permission_is_not_enough(client, org_id, ids, name) -> None:
    """Missing exactly one required key at a time, holding all the rest — including, for the
    two-key routes, the other key."""
    required = ROUTES[name][2]
    for missing in required:
        perms = [p for p in ALL_RECEIVING_PERMS if p != missing]
        response = await _call(client, ids, ROUTES[name], perms, org_id)
        assert response.status_code == 403, (name, missing, response.text)


ORG_FLOORED = {REQ_APPROVE, RECEIPT_READ, RECEIPT_WRITE, STOCK_RECEIVE}


@pytest.mark.parametrize("name", list(ROUTES))
async def test_a_narrower_scope_than_org_is_not_enough_where_org_is_the_floor(
    client, org_id, ids, name
) -> None:
    """The ORG-floored keys (approve, receipt.*, stock.receive) refuse an OWN grant."""
    required = ROUTES[name][2]
    if not ORG_FLOORED & set(required):
        pytest.skip("no ORG-floored permission on this route")
    own = [p.replace(":ORG", ":OWN") if p in ORG_FLOORED else p for p in required]
    response = await _call(client, ids, ROUTES[name], own, org_id)
    assert response.status_code == 403, (name, response.text)


@pytest.mark.parametrize("name", list(ROUTES))
async def test_another_tenant_gets_404_or_an_empty_page(
    client, org_id, ids, name, admin_session
) -> None:
    spec = ROUTES[name]
    stranger = uuid.uuid4()
    # the stranger needs a world of their own only so the call is well-formed; nothing of the
    # first tenant's is reachable through it
    response = await _call(client, ids, spec, list(spec[2]), stranger)
    expect = spec[4]
    if expect == "empty":
        assert response.status_code == 200, (name, response.text)
        assert response.json()["items"] == [], name
    else:
        assert response.status_code == 404, (name, response.text)
