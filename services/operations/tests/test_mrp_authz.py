"""Negative authorization and cross-tenant isolation for **every** MRP endpoint.

One table drives both: each row is a route, the single permission it needs, and a valid request.
For each row the suite proves

* no permissions at all -> 403;
* every *other* MRP permission (and every manufacturing permission) together -> 403, so a
  permission does not stand in for a neighbour: `run` is not `read`, `requisition` is not `run`;
* the right permission at a narrower scope than ORG -> 403 (the routes floor at ORG);
* the right permission alone is enough (never 401/403);
* a caller from another organization holding **every** permission gets 404 for anything that
  names a run, shortage or item of mine (never 403, which would confirm it exists), lists come
  back empty, and nothing of mine changed afterwards.

A test at the end checks the table covers every route the router registers, so a new endpoint
cannot ship without a row here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from app.mrp.api import router
from tests.manufacturing_fixtures import ALL_PERMS as MFG_PERMS
from tests.mrp_fixtures import (
    ALL_MRP_PERMS,
    BASE,
    P_READ,
    P_REQ,
    P_RUN,
    Plant,
    add_sales_order,
    build_plant,
    mrp,
    mrp_ok,
    shortages,
    start_run,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    perm: str
    body: Any = None
    #: A list endpoint: another tenant gets an empty page rather than a 404.
    is_list: bool = False
    #: Names nothing of mine in its path (it creates in the caller's own tenant); the stranger
    #: proves isolation by naming one of *my* items in the body instead.
    stranger_body: Any = None
    params: dict[str, str] = field(default_factory=dict)


def rows(ids: dict[str, str]) -> list[Row]:
    run, shortage = ids["run"], ids["shortage"]
    return [
        Row("GET", "/demand-sources", P_READ, is_list=True),
        Row("GET", "/runs", P_READ, is_list=True),
        Row(
            "POST",
            "/runs",
            P_RUN,
            {"sources": ["SALES_ORDER"]},
            stranger_body={
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": ids["item"], "quantity": "1"}],
            },
        ),
        Row("GET", f"/runs/{run}", P_READ),
        Row("GET", f"/runs/{run}/demands", P_READ),
        Row("GET", f"/runs/{run}/shortages", P_READ),
        Row("GET", f"/runs/{run}/shortages/{shortage}", P_READ),
        Row("POST", f"/runs/{run}/requisitions", P_REQ, {"note": "authz"}),
    ]


@pytest_asyncio.fixture
async def world(client, session, org_id) -> tuple[Plant, dict[str, str]]:
    p = await build_plant(client, session, org_id)
    await add_sales_order(session, org_id, "SO-1", [(p.m.shirt, "3")])
    run = await start_run(client, org_id, sources=["SALES_ORDER"])
    s = await shortages(client, org_id, run["id"])
    return p, {
        "run": run["id"],
        "shortage": s["BTN-14MM"]["id"],
        "item": str(p.m.shirt),
    }


def _uuid_like(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


def label(row: Row) -> str:
    parts = ["{id}" if _uuid_like(part) else part for part in row.path.split("/")]
    return f"{row.method} {'/'.join(parts)}"


_SHAPES = rows({k: str(uuid.uuid4()) for k in ("run", "shortage", "item")})


class TestEveryEndpoint:
    async def test_no_permissions_is_403(self, client, world) -> None:
        p, ids = world
        failures = []
        for row in rows(ids):
            r = await mrp(client, p.org_id, row.method, row.path, perms=[], json=row.body)
            if r.status_code != 403:
                failures.append((label(row), r.status_code))
        assert not failures, failures

    async def test_every_other_permission_is_not_enough(self, client, world) -> None:
        p, ids = world
        failures = []
        for row in rows(ids):
            others = [x for x in ALL_MRP_PERMS if x != row.perm] + MFG_PERMS
            r = await mrp(client, p.org_id, row.method, row.path, perms=others, json=row.body)
            if r.status_code != 403:
                failures.append((label(row), r.status_code))
        assert not failures, failures

    async def test_a_narrower_scope_than_org_is_403(self, client, world) -> None:
        p, ids = world
        failures = []
        for row in rows(ids):
            r = await mrp(
                client,
                p.org_id,
                row.method,
                row.path,
                perms=[row.perm.replace(":ORG", ":OWN")],
                json=row.body,
            )
            if r.status_code != 403:
                failures.append((label(row), r.status_code))
        assert not failures, failures

    async def test_the_right_permission_alone_is_enough(self, client, world) -> None:
        p, ids = world
        forbidden = []
        for row in rows(ids):
            r = await mrp(client, p.org_id, row.method, row.path, perms=[row.perm], json=row.body)
            if r.status_code in (401, 403):
                forbidden.append((label(row), r.status_code))
        assert not forbidden, forbidden

    async def test_an_organization_without_the_product_is_refused(self, client, world) -> None:
        from tests.helpers import as_principal, principal_for

        p, ids = world
        principal = principal_for(p.org_id, ALL_MRP_PERMS, entitlements=[])
        failures = []
        for row in rows(ids):
            with as_principal(principal):
                r = await client.request(row.method, f"{BASE}{row.path}", json=row.body)
            if r.status_code not in (402, 403):
                failures.append((label(row), r.status_code))
        assert not failures, failures

    async def test_another_tenant_cannot_see_or_touch_any_of_it(self, client, world) -> None:
        _, ids = world
        stranger = uuid.uuid4()
        failures = []
        for row in rows(ids):
            body = row.stranger_body or row.body
            r = await mrp(client, stranger, row.method, row.path, perms=ALL_MRP_PERMS, json=body)
            if row.is_list:
                data = r.json() if r.status_code == 200 else None
                if row.path == "/demand-sources":
                    empty = data is not None and (
                        data["sales_order_lines"],
                        data["production_order_materials"],
                    ) == (0, 0)
                else:
                    empty = data is not None and data["items"] == []
                if not empty:
                    failures.append((label(row), r.status_code, "not empty"))
            elif r.status_code != 404:
                failures.append((label(row), r.status_code, r.text[:120]))
        assert not failures, failures


class TestIsolationAfterwards:
    async def test_strangers_changed_nothing_and_see_nothing(self, client, world) -> None:
        p, ids = world
        stranger = uuid.uuid4()
        for row in rows(ids):
            await mrp(
                client,
                stranger,
                row.method,
                row.path,
                perms=ALL_MRP_PERMS,
                json=row.stranger_body or row.body,
            )
        s = await shortages(client, p.org_id, ids["run"], requested="true")
        assert s == {}  # the stranger's requisition request did not link a thing
        assert (await mrp_ok(client, stranger, "GET", "/runs"))["items"] == []
        mine = await mrp_ok(client, p.org_id, "GET", "/runs")
        assert mine["total"] == 1

    async def test_a_stranger_can_run_only_over_their_own_data(self, client, world) -> None:
        _, _ = world
        stranger = uuid.uuid4()
        run = await start_run(client, stranger, sources=["SALES_ORDER"])
        assert run["demand_count"] == 0 and run["shortage_count"] == 0

    async def test_my_run_id_under_another_runs_shortage_path_is_a_404(self, client, world) -> None:
        p, ids = world
        other = await start_run(client, p.org_id, sources=["SALES_ORDER"])
        r = await mrp(client, p.org_id, "GET", f"/runs/{other['id']}/shortages/{ids['shortage']}")
        assert r.status_code == 404


def test_the_table_covers_every_registered_route() -> None:
    registered = {
        (method, route.path.removeprefix(router.prefix))  # type: ignore[attr-defined]
        for route in router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    def norm(path: str) -> str:
        return "/".join(
            "{}" if part.startswith("{") and part.endswith("}") else part
            for part in path.split("/")
        )

    def shape(path: str) -> str:
        return "/".join("{}" if _uuid_like(part) else part for part in path.split("/"))

    covered = {(r.method, shape(r.path)) for r in _SHAPES}
    needed = {(method, norm(path)) for method, path in registered}
    assert needed == covered, {"missing": needed - covered, "extra": covered - needed}
    assert BASE.endswith("/mrp")
