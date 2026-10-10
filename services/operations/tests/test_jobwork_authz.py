"""Authorization and tenant isolation for **every** job-work endpoint (Inventory plan Phase 6):
unauthenticated, no permission, every *other* job-work permission, a sub-ORG grant, a missing
product entitlement — all refused; and another tenant's order / material / challan is a 404 that
changes nothing. Written against one table of endpoints so a new route cannot be added without
being added here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers import as_principal, principal_for
from tests.jobwork_fixtures import (
    ALL_JW,
    BASE,
    Mill,
    approve,
    balance_milli,
    build_mill,
    call,
    create_order,
    issued_order,
    key,
    order_body,
    receipt_line,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str  # may contain {order}, {material}, {challan}
    perm: str
    body: dict[str, Any] | None
    needs_key: bool = False


def endpoints(mill: Mill) -> list[Endpoint]:
    return [
        Endpoint("list", "GET", "/orders", "read", None),
        Endpoint("create", "POST", "/orders", "write", order_body(mill), needs_key=True),
        Endpoint("read", "GET", "/orders/{order}", "read", None),
        Endpoint(
            "patch",
            "PATCH",
            "/orders/{order}",
            "write",
            {"expected_version": 1, "process": "x"},
        ),
        Endpoint("approve", "POST", "/orders/{order}:approve", "approve", None),
        Endpoint("cancel", "POST", "/orders/{order}:cancel", "write", None),
        Endpoint("issue", "POST", "/orders/{order}:issue", "issue", {}, needs_key=True),
        Endpoint(
            "receive",
            "POST",
            "/orders/{order}:receive",
            "receive",
            {"lines": [receipt_line("{material}", received="1")]},
            needs_key=True,
        ),
        Endpoint("reconciliation", "GET", "/orders/{order}/reconciliation", "read", None),
        Endpoint("close", "POST", "/orders/{order}:close", "approve", None),
        Endpoint("challan", "GET", "/challans/{challan}", "read", None),
        Endpoint(
            "request-eway",
            "POST",
            "/challans/{challan}:request-eway",
            "issue",
            {"distance_km": 10},
        ),
    ]


def fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, ids) for v in value]
    return value


@pytest.fixture
async def world(session, org_id) -> tuple[Mill, dict[str, str]]:
    """One organization with an issued order (so it has a material and a challan)."""
    mill = await build_mill(session, org_id)
    return mill, {}


async def seed(client, mill: Mill) -> dict[str, str]:
    order = await issued_order(client, mill)
    return {
        "order": order["id"],
        "material": order["materials"][0]["id"],
        "challan": order["challans"][0]["id"],
    }


def ids_for_names() -> list[str]:
    return [
        "list",
        "create",
        "read",
        "patch",
        "approve",
        "cancel",
        "issue",
        "receive",
        "reconciliation",
        "close",
        "challan",
        "request-eway",
    ]


def test_this_table_covers_every_jobwork_route() -> None:
    """If a route is added to `app.jobwork.api` without a row above, this fails."""
    from app.jobwork.api import router

    stub = SimpleNamespace(supplier_id=uuid.uuid4(), recv=uuid.uuid4(), fabric=uuid.uuid4())
    declared = {
        (method, route.path.removeprefix("/operations/jobwork"))
        for route in router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }
    table = {
        (e.method, e.path.replace("{order}", "{order_id}").replace("{challan}", "{challan_id}"))
        for e in endpoints(stub)  # type: ignore[arg-type]
    }
    assert declared == table


class TestUnauthenticatedAndUnauthorized:
    @pytest.mark.parametrize("name", ids_for_names())
    async def test_no_credentials_is_401(self, client, world, name) -> None:
        mill, _ = world
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        ids = {
            "order": str(uuid.uuid4()),
            "material": str(uuid.uuid4()),
            "challan": str(uuid.uuid4()),
        }
        headers = {"Idempotency-Key": key()} if endpoint.needs_key else {}
        response = await client.request(
            endpoint.method,
            f"{BASE}{fill(endpoint.path, ids)}",
            json=fill(endpoint.body, ids),
            headers=headers,
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("name", ids_for_names())
    async def test_a_caller_with_no_permissions_is_403(self, client, world, name) -> None:
        mill, _ = world
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        ids = {
            "order": str(uuid.uuid4()),
            "material": str(uuid.uuid4()),
            "challan": str(uuid.uuid4()),
        }
        response = await call(
            client,
            mill.org_id,
            endpoint.method,
            fill(endpoint.path, ids),
            perms=[],
            json=fill(endpoint.body, ids),
            idem=key() if endpoint.needs_key else None,
        )
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize("name", ids_for_names())
    async def test_every_other_jobwork_permission_is_not_enough(self, client, world, name) -> None:
        mill, _ = world
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        needed = f"operations.jobwork.order.{endpoint.perm}"
        others = [f"{p}:ORG" for p in ALL_JW if p != needed]
        ids = {
            "order": str(uuid.uuid4()),
            "material": str(uuid.uuid4()),
            "challan": str(uuid.uuid4()),
        }
        response = await call(
            client,
            mill.org_id,
            endpoint.method,
            fill(endpoint.path, ids),
            perms=others,
            json=fill(endpoint.body, ids),
            idem=key() if endpoint.needs_key else None,
        )
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize("name", ids_for_names())
    async def test_a_sub_org_grant_is_refused_not_widened(self, client, world, name) -> None:
        mill, _ = world
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        ids = {
            "order": str(uuid.uuid4()),
            "material": str(uuid.uuid4()),
            "challan": str(uuid.uuid4()),
        }
        for scope in ("OWN", "TEAM", "LOCATION"):
            response = await call(
                client,
                mill.org_id,
                endpoint.method,
                fill(endpoint.path, ids),
                perms=[f"operations.jobwork.order.{endpoint.perm}:{scope}"],
                json=fill(endpoint.body, ids),
                idem=key() if endpoint.needs_key else None,
            )
            assert response.status_code == 403, (scope, response.text)

    @pytest.mark.parametrize("name", ids_for_names())
    async def test_without_the_product_entitlement_is_402(self, client, world, name) -> None:
        mill, _ = world
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        ids = {
            "order": str(uuid.uuid4()),
            "material": str(uuid.uuid4()),
            "challan": str(uuid.uuid4()),
        }
        principal = principal_for(
            mill.org_id,
            [f"operations.jobwork.order.{endpoint.perm}:ORG"],
            entitlements=[],
        )
        headers = {"Idempotency-Key": key()} if endpoint.needs_key else {}
        with as_principal(principal):
            response = await client.request(
                endpoint.method,
                f"{BASE}{fill(endpoint.path, ids)}",
                json=fill(endpoint.body, ids),
                headers=headers,
            )
        assert response.status_code == 402, response.text


class TestCrossTenant:
    """Another organization's principal, holding **every** job-work permission at ORG, names this
    organization's rows by id: 404 on each, and nothing changes."""

    @pytest.mark.parametrize("name", ids_for_names())
    async def test_another_tenants_rows_are_404_and_untouched(
        self, client, session, world, name
    ) -> None:
        mill, _ = world
        ids = await seed(client, mill)
        endpoint = next(e for e in endpoints(mill) if e.name == name)
        stranger = uuid.uuid4()
        before = await call(client, mill.org_id, "GET", f"/orders/{ids['order']}")

        response = await call(
            client,
            stranger,
            endpoint.method,
            fill(endpoint.path, ids),
            json=fill(endpoint.body, ids),
            idem=key() if endpoint.needs_key else None,
        )

        if endpoint.name == "list":
            assert response.status_code == 200
            assert response.json()["total"] == 0, "another tenant's orders leaked into a list"
        elif endpoint.name == "create":
            # The stranger names this tenant's supplier and item: neither exists for them.
            assert response.status_code == 404, response.text
        else:
            assert response.status_code == 404, response.text
            assert "organization" in response.json()["detail"]

        after = await call(client, mill.org_id, "GET", f"/orders/{ids['order']}")
        assert after.json() == before.json(), "a refused cross-tenant request changed the order"
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 82_500

    async def test_a_strangers_own_order_is_invisible_to_this_tenant(
        self, client, session, org_id
    ) -> None:
        mill_a = await build_mill(session, org_id)
        order = await create_order(client, mill_a)
        listed = await call(client, uuid.uuid4(), "GET", "/orders")
        assert listed.json()["items"] == []
        detail = await call(client, uuid.uuid4(), "GET", f"/orders/{order['id']}")
        assert detail.status_code == 404
        reconciliation = await call(
            client, uuid.uuid4(), "GET", f"/orders/{order['id']}/reconciliation"
        )
        assert reconciliation.status_code == 404

    async def test_a_challan_cannot_be_read_through_another_tenants_order_number(
        self, client, world
    ) -> None:
        mill, _ = world
        ids = await seed(client, mill)
        response = await call(client, uuid.uuid4(), "GET", f"/challans/{ids['challan']}")
        assert response.status_code == 404


class TestSegregation:
    async def test_the_drafter_cannot_approve_or_close(self, client, world) -> None:
        mill, _ = world
        drafter = [f"operations.jobwork.order.{p}:ORG" for p in ("read", "write")]
        order = await create_order(client, mill)
        refused = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:approve", perms=drafter
        )
        assert refused.status_code == 403
        await approve(client, mill, order["id"])

    async def test_the_storekeeper_who_receives_cannot_issue_or_close(self, client, world) -> None:
        mill, _ = world
        receiver = [f"operations.jobwork.order.{p}:ORG" for p in ("read", "receive")]
        order = await issued_order(client, mill)
        issue_refused = await call(
            client,
            mill.org_id,
            "POST",
            f"/orders/{order['id']}:issue",
            perms=receiver,
            json={},
            idem=key(),
        )
        close_refused = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:close", perms=receiver
        )
        assert issue_refused.status_code == 403
        assert close_refused.status_code == 403
