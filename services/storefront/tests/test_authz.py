"""Negative authorization on every route, and cross-tenant isolation under real row-level
security.

ADR-0003 §3's rule applied per endpoint: a permission *name* without enough scope is not access,
and a UI gate is not a control. Every authenticated route in this service carries a test that it
refuses the caller it should — and another organization's rows are invisible to the list, the
detail, the edit and the delete alike, even when the ids are known.

The one route that is **deliberately** reachable with no credential at all is the anonymous
serviceability check, and the last class here is the pair of assertions that keeps that line
where it is: the public path answers a stranger, and every other path refuses one.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import (
    STORE_READ,
    STORE_WRITE,
    ZONE_READ,
    ZONE_WRITE,
    all_perms,
    principal_for,
    seed_store,
    seed_zone,
)

pytestmark = pytest.mark.integration

ZONE_ID = uuid.uuid4()

#: `(method, path, the permission the route requires)`. One row per authenticated route — a new
#: route with no row here is a route with no negative-authorization test, which is the omission
#: this table exists to make visible.
ROUTES: list[tuple[str, str, str]] = [
    ("GET", "/api/v1/storefront/store", STORE_READ),
    ("PUT", "/api/v1/storefront/store", STORE_WRITE),
    # The two named transitions. `settings.write` rather than a fifth permission: publishing is
    # the store's own setting with the largest consequence, not a different job (ADR-0011 §4).
    ("POST", "/api/v1/storefront/store/publish", STORE_WRITE),
    ("POST", "/api/v1/storefront/store/unpublish", STORE_WRITE),
    ("GET", "/api/v1/storefront/delivery/zones", ZONE_READ),
    ("POST", "/api/v1/storefront/delivery/zones", ZONE_WRITE),
    (
        "PATCH",
        f"/api/v1/storefront/delivery/zones/{ZONE_ID}",
        ZONE_WRITE,
    ),
    (
        "DELETE",
        f"/api/v1/storefront/delivery/zones/{ZONE_ID}",
        ZONE_WRITE,
    ),
    (
        "GET",
        f"/api/v1/storefront/delivery/zones/{ZONE_ID}/postal-codes",
        ZONE_READ,
    ),
    (
        "PUT",
        f"/api/v1/storefront/delivery/zones/{ZONE_ID}/postal-codes",
        ZONE_WRITE,
    ),
    ("POST", "/api/v1/storefront/delivery/postal-codes/import", ZONE_WRITE),
    ("POST", "/api/v1/storefront/delivery/check", ZONE_READ),
    ("GET", "/api/v1/storefront/delivery/demand", ZONE_READ),
]

_BODIES: dict[tuple[str, str], dict] = {
    ("PUT", "/api/v1/storefront/store"): {"slug": "a-store", "name": "A Store"},
    ("POST", "/api/v1/storefront/delivery/zones"): {
        "code": "Z",
        "name": "Z",
        "mode": "UNSERVED",
    },
    ("PATCH", f"/api/v1/storefront/delivery/zones/{ZONE_ID}"): {"name": "Z"},
    ("PUT", f"/api/v1/storefront/delivery/zones/{ZONE_ID}/postal-codes"): {
        "postal_codes": "400050"
    },
    ("POST", "/api/v1/storefront/delivery/check"): {"postal_code": "400050"},
}


async def _call(client, method: str, path: str):
    body = _BODIES.get((method, path))
    if method == "POST" and path.endswith("/postal-codes/import"):
        return await client.post(
            path, content="postal_code,zone_code\n400050,Z\n", headers={"content-type": "text/csv"}
        )
    return await client.request(method, path, json=body)


class TestEveryRouteRefusesTheWrongCaller:
    @pytest.mark.parametrize(("method", "path", "permission"), ROUTES, ids=lambda v: str(v))
    async def test_without_a_token_it_is_401(
        self, client, method: str, path: str, permission: str
    ) -> None:
        response = await _call(client, method, path)
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.parametrize(("method", "path", "permission"), ROUTES, ids=lambda v: str(v))
    async def test_with_every_other_permission_but_this_one_it_is_403(
        self, client, as_principal, org_id: uuid.UUID, method: str, path: str, permission: str
    ) -> None:
        """Holding three of the four is not holding the fourth. This is the shape of the failure
        a single `require_product` guard would hide."""
        others = [
            f"{code}:ORG"
            for code in (STORE_READ, STORE_WRITE, ZONE_READ, ZONE_WRITE)
            if code != permission
        ]
        with as_principal(principal_for(org_id, others)):
            response = await _call(client, method, path)
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.parametrize(("method", "path", "permission"), ROUTES, ids=lambda v: str(v))
    async def test_without_the_product_entitlement_it_is_402(
        self, client, as_principal, org_id: uuid.UUID, method: str, path: str, permission: str
    ) -> None:
        """402 rather than 403: the caller's permissions are fine, the subscription is not, so
        the client offers an upgrade. Checked in the service as well as at the gateway, because a
        routing-table mistake must not become an entitlement bypass."""
        principal = principal_for(org_id, [f"{permission}:ORG"])
        principal.entitlements = []
        with as_principal(principal):
            response = await _call(client, method, path)
        assert response.status_code == 402

    @pytest.mark.parametrize("scope", ["OWN", "TEAM", "LOCATION"])
    @pytest.mark.parametrize(("method", "path", "permission"), ROUTES, ids=lambda v: str(v))
    async def test_a_narrower_grant_is_refused_by_the_org_floor(
        self,
        client,
        as_principal,
        org_id: uuid.UUID,
        method: str,
        path: str,
        permission: str,
        scope: str,
    ) -> None:
        """All four permissions are ceilinged **and** defaulted at ORG, so a narrower grant
        should not exist. The floor is written anyway: the ceiling is catalogue data a later edit
        can widen, and the floor is code in front of the query (ADR-0010 §9)."""
        with as_principal(principal_for(org_id, [f"{permission}:{scope}"])):
            response = await _call(client, method, path)
        assert response.status_code == 403


class TestCrossTenantIsolation:
    async def test_another_organizations_zones_are_not_in_the_list(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        await seed_zone(admin_session, other_org, code="THEIRS")
        await seed_zone(admin_session, org_id, code="MINE")

        with as_principal(all_perms(org_id)):
            response = await client.get("/api/v1/storefront/delivery/zones")

        assert response.status_code == 200
        assert [row["code"] for row in response.json()["items"]] == ["MINE"]

    async def test_another_organizations_zone_is_a_404_by_id_and_never_a_403(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """Confirming that an id exists in *some* organization is itself a disclosure."""
        theirs = await seed_zone(admin_session, other_org, code="THEIRS")

        with as_principal(all_perms(org_id)):
            read = await client.get(f"/api/v1/storefront/delivery/zones/{theirs.id}/postal-codes")
            patch = await client.patch(
                f"/api/v1/storefront/delivery/zones/{theirs.id}", json={"name": "mine now"}
            )
            remove = await client.delete(f"/api/v1/storefront/delivery/zones/{theirs.id}")

        assert read.status_code == 404
        assert patch.status_code == 404
        assert remove.status_code == 404

    async def test_another_organizations_storefront_is_invisible(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        await seed_store(admin_session, other_org, slug)

        with as_principal(all_perms(org_id)):
            response = await client.get("/api/v1/storefront/store")

        assert response.status_code == 404

    async def test_another_organizations_demand_rows_are_invisible(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """The report is the merchant's expansion map. It is also, per ADR-0010 §9, readable by
        anyone who can read the zones — of *their own* organization and no other."""
        other_slug = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, other_slug)
        await client.post(
            "/api/v1/storefront/public/serviceability",
            json={"store": other_slug, "postal_code": "400050"},
        )

        with as_principal(all_perms(org_id)):
            response = await client.get("/api/v1/storefront/delivery/demand")

        assert response.status_code == 200
        assert response.json()["items"] == []


class TestWhereThePublicLineIs:
    async def test_the_anonymous_check_is_reachable_with_no_credential(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)

        response = await client.post(
            "/api/v1/storefront/public/serviceability",
            json={"store": slug, "postal_code": "400050"},
        )

        assert response.status_code == 200

    async def test_and_it_is_the_only_one(self, client) -> None:
        """Every other path in this service refuses a caller with no credential. One public
        route, and it is the one that was designed to be."""
        for method, path, _permission in ROUTES:
            response = await _call(client, method, path)
            assert response.status_code == 401, f"{method} {path} answered a stranger"
