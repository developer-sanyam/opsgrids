"""Negative-authz and cross-tenant tests for EVERY quality endpoint (inventory plan §4 and §6).

One table drives all fifteen routes, so a sixteenth added without an entry is a visible gap, and
each route is proven three ways:

* a caller holding **no** permission is refused (403);
* a caller holding **every other** quality permission but not the one the route needs is refused —
  the permissions are not interchangeable, and `inspect` cannot release stock;
* a caller from **another organization**, holding every permission, gets a 404 on an object that
  exists in the first organization — never a 403 that would confirm it is there.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.quality.api import router as quality_router
from tests.helpers import as_principal, principal_for
from tests.quality_fixtures import (
    ALL_PASS,
    ALL_PERMS,
    APPROVE,
    BASE,
    INSPECT,
    READ,
    REJECT,
    QcShop,
    build_qc_shop,
    call,
    make_template,
    open_ok,
    record,
)

pytestmark = pytest.mark.integration


@dataclass
class Ids:
    template: str
    inspection: str
    item: str
    location: str


@dataclass
class Route:
    name: str
    method: str
    path: Callable[[Ids], str]
    body: Callable[[Ids], Any]
    needs: str
    #: True when the route names one existing object (so another tenant must get a 404).
    by_id: bool = True


ROUTES = [
    Route("list_templates", "GET", lambda i: "/templates", lambda i: None, READ, by_id=False),
    Route(
        "create_template",
        "POST",
        lambda i: "/templates",
        lambda i: {"code": "X-1", "name": "x", "kind": "incoming", "item_id": i.item},
        APPROVE,
        by_id=False,
    ),
    Route("read_template", "GET", lambda i: f"/templates/{i.template}", lambda i: None, READ),
    Route(
        "patch_template",
        "PATCH",
        lambda i: f"/templates/{i.template}",
        lambda i: {"name": "renamed"},
        APPROVE,
    ),
    Route(
        "delete_template", "DELETE", lambda i: f"/templates/{i.template}", lambda i: None, APPROVE
    ),
    Route("list_inspections", "GET", lambda i: "/inspections", lambda i: None, READ, by_id=False),
    Route(
        "create_inspection",
        "POST",
        lambda i: "/inspections",
        lambda i: {
            "kind": "incoming",
            "item_id": i.item,
            "location_id": i.location,
            "quantity": "1",
        },
        INSPECT,
        by_id=False,
    ),
    Route("read_inspection", "GET", lambda i: f"/inspections/{i.inspection}", lambda i: None, READ),
    Route(
        "record_results",
        "PUT",
        lambda i: f"/inspections/{i.inspection}/results",
        lambda i: {"results": [{"param_name": "GSM", "value": "180"}]},
        INSPECT,
    ),
    Route(
        "hold",
        "POST",
        lambda i: f"/inspections/{i.inspection}/hold",
        lambda i: {"note": "x"},
        INSPECT,
    ),
    Route(
        "rework",
        "POST",
        lambda i: f"/inspections/{i.inspection}/rework",
        lambda i: {"note": "x"},
        INSPECT,
    ),
    Route(
        "reinspect",
        "POST",
        lambda i: f"/inspections/{i.inspection}/reinspect",
        lambda i: {"note": "x"},
        INSPECT,
    ),
    Route(
        "release",
        "POST",
        lambda i: f"/inspections/{i.inspection}/release",
        lambda i: {"note": "ok"},
        APPROVE,
    ),
    Route(
        "reject",
        "POST",
        lambda i: f"/inspections/{i.inspection}/reject",
        lambda i: {"reason": "bad"},
        REJECT,
    ),
    Route(
        "disposition",
        "POST",
        lambda i: f"/inspections/{i.inspection}/disposition",
        lambda i: {"action": "SCRAP"},
        REJECT,
    ),
]


def test_the_table_covers_every_quality_route() -> None:
    declared = {
        (method, route.path.split("/operations/quality", 1)[1])
        for route in quality_router.routes
        if "/operations/quality" in getattr(route, "path", "")
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
    }
    placeholders = Ids("{template_id}", "{inspection_id}", "", "")
    covered = {(r.method, r.path(placeholders)) for r in ROUTES}
    assert declared == covered


@pytest.fixture
async def qc(session, org_id) -> QcShop:
    return await build_qc_shop(session, org_id)


@pytest.fixture
async def ids(client, qc: QcShop) -> Ids:
    template = await make_template(client, qc.org_id)
    inspection = await open_ok(client, qc, template_id=template["id"])
    return Ids(
        template=template["id"],
        inspection=inspection["id"],
        item=str(qc.shop.widget),
        location=str(qc.location_id),
    )


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
async def test_no_permission_is_403(client, qc: QcShop, ids: Ids, route: Route) -> None:
    response = await call(
        client, qc.org_id, route.method, route.path(ids), perms=[], json=route.body(ids)
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
async def test_every_other_quality_permission_is_still_403(
    client, qc: QcShop, ids: Ids, route: Route
) -> None:
    others = [p for p in ALL_PERMS if p != route.needs]
    response = await call(
        client, qc.org_id, route.method, route.path(ids), perms=others, json=route.body(ids)
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
async def test_an_own_scope_grant_is_not_enough(client, qc: QcShop, ids: Ids, route: Route) -> None:
    """Every route is floored at ORG: an OWN-scoped grant of the right permission is refused."""
    principal = principal_for(
        qc.org_id, [route.needs.replace(":ORG", ":OWN")], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        response = await client.request(
            route.method, f"{BASE}{route.path(ids)}", json=route.body(ids)
        )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
async def test_without_the_operations_entitlement_is_refused(
    client, qc: QcShop, ids: Ids, route: Route
) -> None:
    principal = principal_for(qc.org_id, ALL_PERMS, entitlements=[])
    with as_principal(principal):
        response = await client.request(
            route.method, f"{BASE}{route.path(ids)}", json=route.body(ids)
        )
    assert response.status_code in (402, 403), response.text


@pytest.mark.parametrize("route", [r for r in ROUTES if r.by_id], ids=lambda r: r.name)
async def test_another_tenant_gets_404_on_an_object_it_cannot_see(
    client, qc: QcShop, ids: Ids, route: Route
) -> None:
    other_org = uuid.uuid4()
    response = await call(client, other_org, route.method, route.path(ids), json=route.body(ids))
    assert response.status_code == 404, response.text


async def test_another_tenant_cannot_open_an_inspection_on_our_item(
    client, qc: QcShop, ids: Ids
) -> None:
    other_org = uuid.uuid4()
    response = await call(
        client,
        other_org,
        "POST",
        "/inspections",
        json={
            "kind": "incoming",
            "item_id": ids.item,
            "location_id": ids.location,
            "quantity": "1",
        },
    )
    assert response.status_code == 404, response.text


async def test_another_tenant_cannot_attach_our_item_to_a_template(
    client, qc: QcShop, ids: Ids
) -> None:
    other_org = uuid.uuid4()
    response = await call(
        client,
        other_org,
        "POST",
        "/templates",
        json={"code": "Y-1", "name": "y", "kind": "incoming", "item_id": ids.item},
    )
    assert response.status_code == 404, response.text


async def test_lists_never_leak_another_tenants_rows(client, qc: QcShop, ids: Ids) -> None:
    other_org = uuid.uuid4()
    for path in ("/templates", "/inspections"):
        response = await call(client, other_org, "GET", path)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
    ours = await call(client, qc.org_id, "GET", "/inspections")
    assert ours.json()["total"] == 1


async def test_a_foreign_tenants_failed_attempt_changed_nothing(
    client, qc: QcShop, ids: Ids
) -> None:
    other_org = uuid.uuid4()
    await record(client, other_org, ids.inspection, [{"param_name": "GSM", "value": "1"}])
    await call(
        client, other_org, "POST", f"/inspections/{ids.inspection}/reject", json={"reason": "x"}
    )
    mine = await call(client, qc.org_id, "GET", f"/inspections/{ids.inspection}")
    body = mine.json()
    assert body["status"] == "PENDING"
    assert body["results"] == []
    # And the owner can still do the real thing afterwards.
    ok = await record(client, qc.org_id, ids.inspection, ALL_PASS)
    assert ok.status_code == 200, ok.text
