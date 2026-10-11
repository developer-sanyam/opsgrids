"""Negative authorization and cross-tenant isolation for **every** manufacturing endpoint.

One table drives both: each row is a route, the single permission it needs, and a valid request
body. For each row the suite proves

* no permissions at all -> 403;
* every *other* manufacturing permission together -> 403 (so a permission does not stand in for
  a neighbour: `publish` is not `write`, `approve` is not `write`, `complete` is not `issue`);
* the right permission at a narrower scope than ORG -> 403 (the routes floor at ORG);
* a caller from another organization holding **every** permission gets 404 for a row they cannot
  see (never 403, which would confirm it exists), and the row is untouched afterwards.

A test at the end checks the table covers every route the router registers, so a new endpoint
cannot ship without a row here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.manufacturing.api import router
from tests.manufacturing_fixtures import (
    ALL_PERMS,
    BASE,
    P_BOM_PUBLISH,
    P_BOM_READ,
    P_BOM_WRITE,
    P_ORD_APPROVE,
    P_ORD_COMPLETE,
    P_ORD_ISSUE,
    P_ORD_READ,
    P_ORD_WRITE,
    Mfg,
    build_mfg,
    call,
    make_bom,
    make_order,
    material,
    ok,
    stock,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    perm: str
    body: Any = None
    #: A list endpoint: another tenant gets an empty page / list rather than a 404.
    is_list: bool = False
    #: Creates its own row in the caller's tenant, so another tenant's call succeeds for itself
    #: (isolation is then shown by the list tests, not by a 404).
    creates: bool = False
    params: dict[str, str] = field(default_factory=dict)


def rows(ids: dict[str, str]) -> list[Row]:
    bom, v, d, c, s = ids["bom"], ids["version"], ids["draft"], ids["component"], ids["substitute"]
    o, mat, pick = ids["order"], ids["material"], ids["pick"]
    item, loc = ids["item"], ids["location"]
    return [
        Row("GET", "/boms", P_BOM_READ, is_list=True),
        Row(
            "POST",
            "/boms",
            P_BOM_WRITE,
            {"code": "AUTHZ-NEW", "name": "n", "item_id": ids["spare_item"]},
        ),
        Row("GET", f"/boms/{bom}", P_BOM_READ),
        Row("PATCH", f"/boms/{bom}", P_BOM_WRITE, {"name": "renamed"}),
        Row("GET", f"/boms/{bom}/versions", P_BOM_READ),
        Row("POST", f"/boms/{bom}/versions", P_BOM_WRITE, {}),
        Row("GET", f"/boms/{bom}/versions/{v}", P_BOM_READ),
        Row("PATCH", f"/boms/{bom}/versions/{d}", P_BOM_WRITE, {"notes": "x"}),
        Row("DELETE", f"/boms/{bom}/versions/{d}", P_BOM_WRITE),
        Row(
            "POST",
            f"/boms/{bom}/versions/{d}/components",
            P_BOM_WRITE,
            {"component_item_id": item, "quantity": "1"},
        ),
        Row("PATCH", f"/boms/{bom}/versions/{d}/components/{c}", P_BOM_WRITE, {"quantity": "2"}),
        Row("DELETE", f"/boms/{bom}/versions/{d}/components/{c}", P_BOM_WRITE),
        Row(
            "POST",
            f"/boms/{bom}/versions/{d}/components/{c}/substitutes",
            P_BOM_WRITE,
            {"substitute_item_id": ids["spare_item"]},
        ),
        Row("DELETE", f"/boms/{bom}/versions/{d}/components/{c}/substitutes/{s}", P_BOM_WRITE),
        Row("POST", f"/boms/{bom}/versions/{d}/publish", P_BOM_PUBLISH),
        Row("POST", f"/boms/{bom}/versions/{v}/retire", P_BOM_PUBLISH),
        Row("GET", f"/boms/{bom}/versions/{v}/explosion", P_BOM_READ),
        Row("GET", "/orders", P_ORD_READ, is_list=True),
        Row(
            "POST",
            "/orders",
            P_ORD_WRITE,
            {"item_id": item, "quantity": "1", "output_location_id": loc},
        ),
        Row("GET", f"/orders/{o}", P_ORD_READ),
        Row("PATCH", f"/orders/{o}", P_ORD_WRITE, {"notes": "x"}),
        Row("POST", f"/orders/{o}/materials/{mat}/substitute", P_ORD_WRITE, {}),
        Row("POST", f"/orders/{o}/approve", P_ORD_APPROVE),
        Row("POST", f"/orders/{o}/start", P_ORD_ISSUE),
        Row("POST", f"/orders/{o}/cancel", P_ORD_WRITE, {"reason": "x"}),
        Row("POST", f"/orders/{o}/reserve", P_ORD_ISSUE, {}),
        Row("POST", f"/orders/{o}/issue", P_ORD_ISSUE, {}),
        Row("POST", f"/orders/{o}/returns", P_ORD_ISSUE, {"lines": [{"pick_id": pick}]}),
        Row("POST", f"/orders/{o}/outputs", P_ORD_COMPLETE, {"quantity": "1"}),
        Row(
            "POST",
            f"/orders/{o}/wastage",
            P_ORD_ISSUE,
            {"material_id": mat, "wastage_type": "CUTTING", "quantity": "1"},
        ),
        Row("POST", f"/orders/{o}/complete", P_ORD_COMPLETE, {}),
        Row("GET", f"/orders/{o}/outputs", P_ORD_READ),
        Row("GET", f"/orders/{o}/wastage", P_ORD_READ),
        Row("GET", f"/orders/{o}/genealogy", P_ORD_READ),
        Row("GET", f"/orders/{o}/variance", P_ORD_READ),
        Row("GET", "/wastage-thresholds", P_ORD_READ, is_list=True),
        Row("POST", "/wastage-thresholds", P_BOM_WRITE, {"max_percent": "3"}, creates=True),
        Row("DELETE", f"/wastage-thresholds/{ids['threshold']}", P_BOM_WRITE),
    ]


@pytest.fixture
async def world(client, session, org_id) -> tuple[Mfg, dict[str, str]]:
    m = await build_mfg(session, org_id)
    await stock(session, m, m.t.fabric, "100")
    await stock(session, m, m.t.buttons, "100")
    published = await make_bom(
        client, m, m.shirt, "BOM-SHIRT", [(m.t.fabric, "1", "0"), (m.t.buttons, "2", "0")]
    )
    order = await make_order(client, m, m.shirt, "5")
    await ok(client, m.org_id, "POST", f"/orders/{order['id']}/reserve", json={})
    running = await ok(client, m.org_id, "POST", f"/orders/{order['id']}/issue", json={})
    threshold = await ok(client, m.org_id, "POST", "/wastage-thresholds", json={"max_percent": "9"})
    ids = {
        # The route table needs a BOM that has both a published and a draft version.
        "bom": published["bom_id"],
        "version": published["id"],
        "draft": "",
        "component": "",
        "substitute": "",
        "order": order["id"],
        "material": material(running, "COT-180-NAVY")["id"],
        "pick": running["picks"][0]["id"],
        "threshold": threshold["id"],
        "item": str(m.t.fabric),
        "spare_item": str(m.t.dye),
        "location": str(m.t.finishing),
    }
    # A draft on the same BOM (for the draft-only routes), with a component and a substitute.
    draft = await ok(client, m.org_id, "POST", f"/boms/{ids['bom']}/versions", expect=201, json={})
    comp = await ok(
        client,
        m.org_id,
        "POST",
        f"/boms/{ids['bom']}/versions/{draft['id']}/components",
        expect=201,
        json={"component_item_id": str(m.t.dye), "quantity": "1"},
    )
    sub_detail = await ok(
        client,
        m.org_id,
        "POST",
        f"/boms/{ids['bom']}/versions/{draft['id']}/components/{comp['components'][0]['id']}"
        "/substitutes",
        expect=201,
        json={"substitute_item_id": str(m.t.buttons)},
    )
    ids["draft"] = draft["id"]
    ids["component"] = comp["components"][0]["id"]
    ids["substitute"] = sub_detail["components"][0]["substitutes"][0]["id"]
    return m, ids


def label(row: Row) -> str:
    # The id-bearing path would make unreadable ids; show the route shape.
    parts = [p if not _looks_like_uuid(p) else "{id}" for p in row.path.split("/")]
    return f"{row.method} {'/'.join(parts)}"


def _looks_like_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


# Row shapes with throwaway ids, only to build the parametrisation ids.
_SHAPE_IDS = {k: str(uuid.uuid4()) for k in (
    "bom", "version", "draft", "component", "substitute", "order", "material", "pick",
    "threshold", "item", "spare_item", "location",
)}  # fmt: skip
SHAPES = rows(_SHAPE_IDS)


class TestEveryEndpoint:
    """One world, every route: a failure names the route, so this is as precise as a
    parametrised test at a fraction of the set-up cost (a world is ~1 s to build)."""

    async def test_no_permissions_is_403(self, client, world) -> None:
        m, ids = world
        failures = []
        for row in rows(ids):
            response = await call(
                client, m.org_id, row.method, row.path, perms=[], json=row.body, params=row.params
            )
            if response.status_code != 403:
                failures.append((label(row), response.status_code))
        assert not failures, failures

    async def test_every_other_permission_is_not_enough(self, client, world) -> None:
        m, ids = world
        failures = []
        for row in rows(ids):
            others = [p for p in ALL_PERMS if p != row.perm]
            response = await call(
                client,
                m.org_id,
                row.method,
                row.path,
                perms=others,
                json=row.body,
                params=row.params,
            )
            if response.status_code != 403:
                failures.append((label(row), response.status_code))
        assert not failures, failures

    async def test_a_narrower_scope_than_org_is_403(self, client, world) -> None:
        m, ids = world
        failures = []
        for row in rows(ids):
            response = await call(
                client,
                m.org_id,
                row.method,
                row.path,
                perms=[row.perm.replace(":ORG", ":OWN")],
                json=row.body,
                params=row.params,
            )
            if response.status_code != 403:
                failures.append((label(row), response.status_code))
        assert not failures, failures

    async def test_the_right_permission_alone_is_enough(self, client, world) -> None:
        """The negative tests above only mean something if the same request succeeds (or fails
        for a business reason, never 403) with exactly the one permission."""
        m, ids = world
        forbidden = []
        for row in rows(ids):
            response = await call(
                client,
                m.org_id,
                row.method,
                row.path,
                perms=[row.perm],
                json=row.body,
                params=row.params,
            )
            if response.status_code in (401, 403):
                forbidden.append((label(row), response.status_code))
        assert not forbidden, forbidden

    async def test_another_tenant_cannot_see_or_touch_any_of_it(self, client, world) -> None:
        _, ids = world
        stranger = uuid.uuid4()
        failures = []
        for row in rows(ids):
            response = await call(
                client,
                stranger,
                row.method,
                row.path,
                perms=ALL_PERMS,
                json=row.body,
                params=row.params,
            )
            if row.is_list:
                body = response.json() if response.status_code == 200 else None
                empty = (
                    body is not None and (body["items"] if isinstance(body, dict) else body) == []
                )
                if not empty:
                    failures.append((label(row), response.status_code, "list not empty"))
            elif row.creates:
                if response.status_code != 200:
                    failures.append((label(row), response.status_code))
            elif response.status_code != 404:
                failures.append((label(row), response.status_code, response.text[:120]))
        assert not failures, failures


class TestIsolationAfterwards:
    async def test_strangers_changed_nothing_and_see_nothing(self, client, world) -> None:
        m, ids = world
        stranger = uuid.uuid4()
        for row in rows(ids):
            await call(
                client,
                stranger,
                row.method,
                row.path,
                perms=ALL_PERMS,
                json=row.body,
                params=row.params,
            )
        order = await ok(client, m.org_id, "GET", f"/orders/{ids['order']}")
        assert order["status"] == "IN_PROGRESS" and order["notes"] == ""
        bom = await ok(client, m.org_id, "GET", f"/boms/{ids['bom']}")
        assert bom["name"] == "BOM-SHIRT recipe" and bom["version_count"] == 2
        mine = await ok(client, m.org_id, "GET", "/wastage-thresholds")
        assert [t["max_percent"] for t in mine] == ["9"]
        theirs = await ok(client, stranger, "GET", "/wastage-thresholds")
        assert [t["max_percent"] for t in theirs] == ["3"]  # only what the stranger created
        assert (await ok(client, stranger, "GET", "/boms"))["items"] == []
        assert (await ok(client, stranger, "GET", "/orders"))["items"] == []

    async def test_a_stranger_cannot_use_my_items_or_locations(self, client, world) -> None:
        _, ids = world
        stranger = uuid.uuid4()
        no_item = await call(
            client,
            stranger,
            "POST",
            "/boms",
            json={"code": "X", "name": "x", "item_id": ids["item"]},
        )
        assert no_item.status_code == 404
        no_bom = await call(
            client,
            stranger,
            "POST",
            "/orders",
            json={"bom_id": ids["bom"], "quantity": "1", "output_location_id": ids["location"]},
        )
        assert no_bom.status_code == 404

    async def test_my_own_order_cannot_point_at_a_strangers_location(self, client, world) -> None:
        m, _ = world
        # a location id that exists in nobody's organization looks the same as another tenant's
        response = await call(
            client,
            m.org_id,
            "POST",
            "/orders",
            json={
                "item_id": str(m.shirt),
                "quantity": "1",
                "output_location_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 404


def test_the_table_covers_every_registered_route() -> None:
    registered = {
        (method, route.path.removeprefix(router.prefix))  # type: ignore[attr-defined]
        for route in router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    def shape(path: str) -> str:
        parts = []
        for part in path.split("/"):
            parts.append("{}" if _looks_like_uuid(part) else part)
        return "/".join(parts)

    def norm(path: str) -> str:
        out = []
        for part in path.split("/"):
            out.append("{}" if part.startswith("{") and part.endswith("}") else part)
        return "/".join(out)

    covered = {(r.method, shape(r.path)) for r in SHAPES}
    needed = {(method, norm(path)) for method, path in registered}
    assert needed == covered, {"missing": needed - covered, "extra": covered - needed}
    assert BASE.endswith("/manufacturing")
