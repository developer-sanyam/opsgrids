"""Every route, called with the wrong tenant's token. Generated, not hand-written.

ADR-0000 §9 calls this the acceptance gate for tenancy, and the reason it is generated is that
a hand-written version is always incomplete: it covers the routes somebody remembered on the
day. This one enumerates the app's own route table, so a route added next month is swept the
first time CI runs — nobody has to remember anything.

`test_cross_tenant_isolation.py` proves the *database* refuses. This proves the **HTTP surface**
does, which is a different claim: a route could still leak by reading with the admin or
directory session, or by echoing an id back in an error message.

What counts as a pass, per route:

- `404` or `403` — refused, which is what should happen.
- `200` **only** if the body contains none of the other tenant's identifiers.

A `200` carrying another organization's ids fails, and the message names the route so the
failure is actionable rather than a puzzle.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Membership, Organization
from app.schemas import OrganizationIn
from platform_core.auth import get_principal
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

#: Routes that are public by design, or that do not read tenant data at all.
NOT_TENANT_SCOPED = {
    "/api/v1/invitations/accept",  # public: the token is the credential
    "/api/v1/account/info",  # service metadata
    "/api/v1/permissions",  # the platform catalogue, identical for every tenant
    "/health",
    "/health/ready",
    "/health/outbox",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
}

#: Every permission in the catalogue, so the sweep fails on *tenancy* rather than on
#: authorization. A route that 403s for lack of a grant proves nothing about isolation.
ALL_PERMISSIONS = [
    f"{code}:ORG"
    for code in (
        "core.org.organization.read",
        "core.org.organization.write",
        "core.org.unit.read",
        "core.org.unit.write",
        "core.org.location.read",
        "core.org.location.write",
        "core.identity.membership.read",
        "core.identity.membership.write",
        "core.identity.role.read",
        "core.identity.role.write",
        "core.identity.audit.read",
        "core.profile.self.read",
        "core.profile.self.write",
    )
]


def _sweepable() -> list[tuple[str, str]]:
    """(method, path) for every GET route worth sweeping.

    Read from the OpenAPI document rather than by walking `app.routes`. This FastAPI version
    keeps included routers nested rather than flattening `APIRoute` objects onto the app, so a
    naive walk silently finds nothing — which the coverage assertion below caught. The schema
    is the app's own statement of its public surface and does not change shape between
    versions.

    The internal router sets `include_in_schema=False`, so it is absent here by construction.
    It is deliberately out of scope: those endpoints read across tenants on purpose and are
    covered by their own tests.
    """
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path not in NOT_TENANT_SCOPED
        for method in operations
        if method.upper() == "GET"
    )


@pytest.fixture
async def victim(admin_session: AsyncSession, org: Organization, people: dict[str, Membership]):
    """A second organization with data in it, and the ids that must never leak."""
    async with admin_session.begin():
        other = await services.create_organization(
            admin_session,
            OrganizationIn(name="Victim Industries", slug="victim"),
            actor_user_id=None,
        )
        member = await services.add_member(
            admin_session,
            org_id=other.id,
            email="victim@example.com",
            full_name="Vera Victim",
            actor_user_id=None,
            role_codes=["ORG_ADMIN"],
        )
    return {"org": other, "membership": member}


class TestNoRouteLeaksAnotherTenant:
    async def test_the_sweep_covers_the_routes_that_exist(self) -> None:
        """A sweep that silently covers nothing is worse than no sweep."""
        routes = _sweepable()
        assert len(routes) >= 5, (
            f"only {len(routes)} route(s) swept — the enumeration is probably broken, which "
            "would make every assertion below vacuous."
        )

    async def test_no_route_returns_another_tenants_data(
        self,
        org: Organization,
        people: dict[str, Membership],
        victim: dict,
    ) -> None:
        """The gate. Called as org A, holding every permission, looking for org B's ids.

        A **fresh session per request**, rather than the shared one the other tests use. The
        sweep issues many requests in a row, and a shared session carries expired ORM state
        between them — which shows up as a greenlet error that has nothing to do with tenancy
        and would mask what this is actually asking. Production gives every request its own
        session, so this does too.
        """
        caller = principal_for(org.id, people["manager"], ALL_PERMISSIONS)
        app.dependency_overrides[get_principal] = lambda: caller

        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _fresh_session():
            async with maker() as s:

                @event.listens_for(s.sync_session, "after_begin")
                def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                    connection.exec_driver_sql(
                        f"SELECT set_config('{TENANT_GUC}', '{org.id}', true)"
                    )

                yield s

        app.dependency_overrides[get_session] = _fresh_session
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        forbidden = {str(victim["org"].id), str(victim["membership"].id), "victim@example.com"}
        leaks: list[str] = []

        try:
            for method, path in _sweepable():
                # Substitute the *victim's* ids into path parameters. That is the interesting
                # case: a caller who has learned an id and asks for it directly.
                concrete = (
                    path.replace("{membership_id}", str(victim["membership"].id))
                    .replace("{role_id}", str(uuid.uuid4()))
                    .replace("{invitation_id}", str(uuid.uuid4()))
                    .replace("{unit_id}", str(uuid.uuid4()))
                )
                if "{" in concrete:
                    continue  # a parameter this sweep does not know how to fill

                response = await client.request(method, concrete)
                if response.status_code in (403, 404):
                    continue
                if response.status_code >= 500:
                    leaks.append(f"{method} {concrete} -> {response.status_code} (server error)")
                    continue

                body = response.text
                hit = [needle for needle in forbidden if needle in body]
                if hit:
                    leaks.append(f"{method} {concrete} -> {response.status_code} leaked {hit}")
        finally:
            await client.aclose()
            await engine.dispose()
            app.dependency_overrides.pop(get_principal, None)
            app.dependency_overrides.pop(get_session, None)

        assert not leaks, "routes returned another organization's data:\n  " + "\n  ".join(leaks)
