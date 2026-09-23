"""The generated cross-tenant sweep: every route, called by the wrong tenant.

Hand-written cross-organization tests are always incomplete — they cover the routes somebody
thought of on the day. This module enumerates the route table instead, so a route added next
month is swept the moment it exists, and fails here until somebody decides how it is swept.

Each case has the shape docs/testing.md asks for: seed organization A with one of everything a
route can name, call the route as a member of organization B, and assert the reply. Two things
make the refusal meaningful rather than incidental:

* **B holds every permission at ORG scope.** A refusal therefore cannot be a permission
  refusal; the only thing standing between B and A's rows is tenancy. That is why routes
  naming A's rows are asserted to answer 404 and *not* 403 — a 403 would mean the
  authorization layer refused first and the route's tenancy was never exercised.
* **After every call, A's rows are read back** through a BYPASSRLS session and compared with
  a snapshot taken before. That second assertion is what distinguishes "refused" from
  "refused after writing".

Routes that take no identifier from the path land in B's own organization, and a 2xx there is
the product working, not a leak. For those the assertion is that the reply carries nothing of
A's and that A is untouched — and where the request body *can* name an A row (a parent
department, a role code) it does, so the route has to refuse after all.

Three routes are exempt, each with its reason next to it, and the meta-tests make the maps
complete by construction: a new path parameter or write route fails CI until it is mapped or
exempted, and a stale entry fails CI until it is removed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.catalogue import CORE
from app.config import settings
from app.main import app
from app.models import SCHEMA, Base, Membership, Organization
from app.schemas import (
    DomainIn,
    GrantIn,
    InvitationIn,
    LocationIn,
    OrganizationIn,
    OrgUnitIn,
    RoleIn,
)
from platform_core.auth import Principal, Scope, get_principal
from platform_core.db import get_session
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

PREFIX = "/api/v1"

#: Every permission the platform defines, at the widest scope. What B calls with, so that a
#: refusal can only be tenancy.
EVERYTHING_AT_ORG = [f"{code}:ORG" for code in CORE.codes()]

# What organization A is seeded with. Deliberately distinctive strings: the leak check looks
# for them in B's responses, so they must not be anything B could legitimately say.
A_UNIT_CODE = "A-ENGINEERING"
A_LOCATION_CODE = "A-MUMBAI"
A_ROLE_CODE = "A-AUDITOR"
A_INVITEE = "invitee-a@example.com"
A_DOMAIN = "a-tenant-domain.example.com"

Route = tuple[str, str]


def _api_routes() -> list[Route]:
    """(method, path) for every route under the public prefix.

    Mounted routers are *not* flattened into `app.routes` on this FastAPI — each sits behind an
    `_IncludedRouter` whose own path is None — so the table is walked with
    `iter_route_contexts`, which yields every route with the prefix applied. That this finds
    anything at all is asserted by `test_the_route_table_was_actually_enumerated`, because a
    walk that silently found nothing would parametrise zero cases and pass.
    """
    found: list[Route] = []
    for context in iter_route_contexts(app.routes):
        if isinstance(context.route, APIRoute) and context.path_format.startswith(PREFIX):
            found.extend((method, context.path_format) for method in context.methods)
    return sorted(found)


ROUTES = _api_routes()


@dataclass(frozen=True)
class TenantA:
    """Organization A's seeded identifiers, and the strings that would betray its data."""

    org_id: uuid.UUID
    membership_id: uuid.UUID
    unit_id: uuid.UUID
    location_id: uuid.UUID
    role_id: uuid.UUID
    invitation_id: uuid.UUID
    invitation_token: str
    domain_id: uuid.UUID
    markers: frozenset[str]


@dataclass(frozen=True)
class Write:
    """How to call a non-GET route: the body, and whether that body names an A row."""

    json: Callable[[TenantA], dict[str, Any] | None]
    names_a: bool = False
    params: Callable[[TenantA], dict[str, Any]] | None = None


NO_BODY = Write(json=lambda _a: None)

#: Every path parameter in the route table, and which of A's identifiers fills it.
PATH_PARAMS: dict[str, Callable[[TenantA], uuid.UUID]] = {
    "membership_id": lambda a: a.membership_id,
    "unit_id": lambda a: a.unit_id,
    "location_id": lambda a: a.location_id,
    "role_id": lambda a: a.role_id,
    "invitation_id": lambda a: a.invitation_id,
    "domain_id": lambda a: a.domain_id,
}

#: A minimal valid request for every non-GET route. Bodies name A's rows wherever the schema
#: lets them, so the route is made to look A's data up and has to fail to find it.
BODIES: dict[Route, Write] = {
    ("PATCH", f"{PREFIX}/organization"): Write(json=lambda _a: {"name": "Renamed by tenant B"}),
    ("POST", f"{PREFIX}/org-units"): Write(
        json=lambda a: {"name": "Intruder", "code": "INTRUDER", "parent_id": str(a.unit_id)},
        names_a=True,
    ),
    # `parent_id` is optional, and the unit named in the path is what the route must not find.
    ("POST", f"{PREFIX}/org-units/{{unit_id}}/move"): NO_BODY,
    ("PATCH", f"{PREFIX}/org-units/{{unit_id}}"): Write(
        json=lambda _a: {"name": "Renamed by tenant B"}
    ),
    ("POST", f"{PREFIX}/locations"): Write(json=lambda _a: {"name": "Pune", "code": "PNQ"}),
    ("PATCH", f"{PREFIX}/locations/{{location_id}}"): Write(
        json=lambda _a: {"name": "Renamed by tenant B"}
    ),
    ("PATCH", f"{PREFIX}/members/{{membership_id}}"): Write(
        json=lambda _a: {"job_title": "Compromised"}
    ),
    ("DELETE", f"{PREFIX}/members/{{membership_id}}"): NO_BODY,
    ("PUT", f"{PREFIX}/members/{{membership_id}}/roles"): Write(
        json=lambda _a: {"role_codes": ["MEMBER"]}
    ),
    ("POST", f"{PREFIX}/roles"): Write(json=lambda _a: {"code": "B_ROLE", "name": "Tenant B"}),
    ("PUT", f"{PREFIX}/roles/{{role_id}}"): Write(
        json=lambda _a: {"code": "HIJACKED", "name": "Hijacked", "grants": []}
    ),
    ("DELETE", f"{PREFIX}/roles/{{role_id}}"): NO_BODY,
    ("POST", f"{PREFIX}/invitations"): Write(
        json=lambda _a: {"email": "newcomer@example.com", "role_codes": [A_ROLE_CODE]},
        names_a=True,
    ),
    ("DELETE", f"{PREFIX}/invitations/{{invitation_id}}"): NO_BODY,
    # A brand-new string, not A's: claiming a domain never needs to name another tenant's
    # row, so this lands in B's own organization. A pending claim is scoped per-organization
    # now (`uq_org_domains_org_domain`), so B claiming A's own pending domain would not even
    # collide; only a domain A has *verified* is globally exclusive. That takeover path —
    # 409, not 404, once the domain is verified elsewhere — is covered on its own in
    # `test_domains.py::TestTakeoverSafety`; the generic NAMING_A/OWN_ORG split here only has
    # a slot for "not found", which a pending or unclaimed domain is.
    ("POST", f"{PREFIX}/domains"): Write(
        json=lambda _a: {"domain": "beta-tenant-domain.example.com"}
    ),
    ("POST", f"{PREFIX}/domains/{{domain_id}}/verify"): NO_BODY,
    ("DELETE", f"{PREFIX}/domains/{{domain_id}}"): NO_BODY,
}

#: Routes the sweep does not call as B, with the reason each is safe to leave out. Two of them
#: are covered by a dedicated test below instead.
ALLOW_LIST: dict[Route, str] = {
    ("GET", f"{PREFIX}/account/info"): (
        "Static service identity: the service name and its schema. Takes no principal and "
        "touches no table, so there is nothing tenant-shaped for it to refuse or to leak."
    ),
    ("GET", f"{PREFIX}/permissions"): (
        "The permission catalogue is the platform's vocabulary, global by design — every "
        "organization reads all of it. Swept by asserting A and B receive the identical list."
    ),
    ("POST", f"{PREFIX}/invitations/accept"): (
        "The token is the credential and the body names no organization. Swept by asserting "
        "that an A token presented through a B-bound request does not find A's invitation."
    ),
    ("POST", f"{PREFIX}/account/signup"): (
        "Public sign-up names no organization: it creates one, bound to an id generated "
        "before the row exists (ADR-0013 §3). Swept by asserting that a sign-up made through "
        "a B-bound request writes nothing into A or B and lands in a third organization."
    ),
}

SWEPT = [route for route in ROUTES if route not in ALLOW_LIST]

#: What a route answers when B's request lands in B's own organization and succeeds. Exact,
#: because "some 2xx" would also accept a route that silently started doing something else.
OWN_ORG_STATUS = {"GET": 200, "PATCH": 200, "PUT": 200, "POST": 201, "DELETE": 204}


def _names_tenant_a(route: Route) -> bool:
    """Does this request name one of A's rows — in the path, or in the body?"""
    method, path = route
    write = BODIES.get(route)
    return "{" in path or (method != "GET" and write is not None and write.names_a)


NAMING_A = [route for route in SWEPT if _names_tenant_a(route)]
OWN_ORG = [route for route in SWEPT if not _names_tenant_a(route)]


def _route_id(route: Route) -> str:
    return f"{route[0]} {route[1]}"


# --------------------------------------------------------------------- fixtures


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest_asyncio.fixture
async def tenant_a(
    session: AsyncSession,
    admin_session: AsyncSession,
    org: Organization,
    people: dict[str, Membership],
) -> TenantA:
    """Organization A with one of everything a route can name, created the way the product does.

    Through `services.*` on the A-bound session rather than by inserting rows, so the seeds
    carry the audit and outbox rows a real organization would — the snapshot compares those
    too.
    """
    manager = people["manager"]

    def ctx(permission: str) -> Any:
        return ctx_for(org.id, manager, permission, Scope.ORG)

    async with session.begin():
        unit = await services.create_org_unit(
            session,
            ctx("core.org.unit.write"),
            OrgUnitIn(name="Acme Engineering", code=A_UNIT_CODE),
        )
        location = await services.create_location(
            session,
            ctx("core.org.location.write"),
            LocationIn(name="Acme Mumbai", code=A_LOCATION_CODE),
        )
        # `manager` holds `ORG_ADMIN`, which does not carry `identity.audit.read` — the
        # grant has to be something the caller actually holds, now that `create_role`
        # enforces the same ceiling `invite` and `set_member_roles` do.
        role = await services.create_role(
            session,
            ctx("core.identity.role.write"),
            RoleIn(
                code=A_ROLE_CODE,
                name="Acme Auditor",
                grants=[GrantIn(permission_code="core.identity.role.read", scope="ORG")],
            ),
        )
        invitation, token = await services.invite(
            session, ctx("core.identity.membership.write"), InvitationIn(email=A_INVITEE)
        )
        domain_row = await services.claim_domain(
            session, ctx("core.identity.domain.write"), DomainIn(domain=A_DOMAIN)
        )

    # The people's emails and names, read back rather than repeated from conftest so the
    # leak check keeps up if the fixture people change.
    async with admin_session.begin():
        rows = await admin_session.execute(
            text(
                "SELECT u.email, u.full_name FROM account.users u "
                "JOIN account.memberships m ON m.user_id = u.id WHERE m.organization_id = :o"
            ),
            {"o": org.id},
        )
        person_markers = {value for row in rows.all() for value in row}

    identifiers = [
        org.id,
        unit.id,
        location.id,
        role.id,
        invitation.id,
        domain_row.id,
        *(m.id for m in people.values()),
        *(m.user_id for m in people.values()),
    ]
    markers = (
        {str(identifier) for identifier in identifiers}
        | person_markers
        | {org.slug, org.name, A_UNIT_CODE, A_LOCATION_CODE, A_ROLE_CODE, A_INVITEE, A_DOMAIN}
    )
    return TenantA(
        org_id=org.id,
        membership_id=people["outsider"].id,
        unit_id=unit.id,
        location_id=location.id,
        role_id=role.id,
        invitation_id=invitation.id,
        invitation_token=token,
        domain_id=domain_row.id,
        markers=frozenset(markers),
    )


@pytest_asyncio.fixture
async def tenant_b(admin_session: AsyncSession) -> Principal:
    """A member of a second organization, holding every permission at ORG scope.

    The organization is created where organizations legitimately are — on the admin session,
    because the policy on `organizations` compares `id` to the tenant binding and a tenant
    cannot name an id that does not exist yet. The principal is what the gateway would forward
    for this person, with the widest grant set the platform can express.
    """
    async with admin_session.begin():
        other = await services.create_organization(
            admin_session, OrganizationIn(name="Beta Corp", slug="beta"), actor_user_id=None
        )
        member = await services.add_member(
            admin_session,
            org_id=other.id,
            email="person@beta.example.com",
            full_name="Someone Else",
            actor_user_id=None,
            role_codes=["ORG_OWNER"],
        )
    return principal_for(other.id, member, EVERYTHING_AT_ORG)


@asynccontextmanager
async def _client_for(principal: Principal) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose requests run as `principal`, bound the way production binds them.

    A fresh app-role session with no tenant listener on it: the chokepoint binds each request's
    transaction to `principal.organization_id` as its first statement, exactly as
    `get_tenant_session` does for a real request. Not the conftest `client` — its session is
    wired to organization A, and the sweep must not start from A's binding and rely on the
    chokepoint quietly rebinding it.
    """
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    previous = dict(app.dependency_overrides)
    async with maker() as db:

        async def _session_override() -> AsyncIterator[AsyncSession]:
            yield db

        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield c
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    await engine.dispose()


@pytest_asyncio.fixture
async def as_tenant_b(tenant_b: Principal) -> AsyncIterator[AsyncClient]:
    async with _client_for(tenant_b) as client:
        yield client


# --------------------------------------------------------------------- the snapshot

#: Everything organization A owns, table by table. Tenant tables by their own column; the two
#: children through their parent; `users` through memberships, since it carries no
#: organization of its own. Whole rows rather than counts, so a changed column is caught too.
SNAPSHOT_QUERIES: dict[str, str] = {
    "organizations": "SELECT * FROM account.organizations WHERE id = :o",
    "org_domains": "SELECT * FROM account.org_domains WHERE organization_id = :o ORDER BY id",
    "org_units": "SELECT * FROM account.org_units WHERE organization_id = :o ORDER BY id",
    "locations": "SELECT * FROM account.locations WHERE organization_id = :o ORDER BY id",
    "memberships": "SELECT * FROM account.memberships WHERE organization_id = :o ORDER BY id",
    "roles": "SELECT * FROM account.roles WHERE organization_id = :o ORDER BY id",
    "invitations": "SELECT * FROM account.invitations WHERE organization_id = :o ORDER BY id",
    "audit_logs": "SELECT * FROM account.audit_logs WHERE organization_id = :o ORDER BY id",
    "outbox": "SELECT * FROM account.outbox WHERE organization_id = :o ORDER BY id",
    "membership_roles": (
        "SELECT mr.* FROM account.membership_roles mr "
        "JOIN account.memberships m ON m.id = mr.membership_id "
        "WHERE m.organization_id = :o ORDER BY mr.id"
    ),
    "role_permissions": (
        "SELECT rp.* FROM account.role_permissions rp "
        "JOIN account.roles r ON r.id = rp.role_id "
        "WHERE r.organization_id = :o ORDER BY rp.id"
    ),
    "users": (
        "SELECT u.* FROM account.users u "
        "JOIN account.memberships m ON m.user_id = u.id "
        "WHERE m.organization_id = :o ORDER BY u.id"
    ),
}

#: Tables with no organization in them at all, which the snapshot therefore cannot scope.
GLOBAL_TABLES = frozenset({"permissions", "processed_events"})


async def _snapshot(admin: AsyncSession, org_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """A's rows, read with BYPASSRLS so nothing is hidden from the comparison."""
    async with admin.begin():
        return {
            name: [dict(row) for row in (await admin.execute(text(sql), {"o": org_id})).mappings()]
            for name, sql in SNAPSHOT_QUERIES.items()
        }


def _request_for(route: Route, a: TenantA) -> tuple[str, dict[str, Any]]:
    """The URL with A's identifiers substituted, and the keyword arguments for the call."""
    method, path = route
    url = path
    for name, pick in PATH_PARAMS.items():
        url = url.replace(f"{{{name}}}", str(pick(a)))
    assert "{" not in url, f"{path} has a path parameter PATH_PARAMS does not map"

    kwargs: dict[str, Any] = {}
    if method != "GET":
        write = BODIES.get(route)
        assert write is not None, f"{_route_id(route)} has no BODIES entry and is not exempt"
        body = write.json(a)
        if body is not None:
            kwargs["json"] = body
        if write.params is not None:
            kwargs["params"] = write.params(a)
    return url, kwargs


def _leaked(a: TenantA, response: str, *, sent: str) -> list[str]:
    """Which of A's identifiers or values appear in a reply B received and did not send.

    A problem body echoes the request path as `instance`, and a "no role named X" message
    echoes the code from the body. B supplied those; the reply repeating them back is not a
    disclosure. Anything of A's that B did *not* send is.
    """
    return sorted(marker for marker in a.markers if marker in response and marker not in sent)


# --------------------------------------------------------------------- the sweep


class TestEveryRouteAsTheWrongTenant:
    @pytest.mark.parametrize("route", NAMING_A, ids=[_route_id(r) for r in NAMING_A])
    async def test_a_route_naming_tenant_a_rows_answers_404_not_403_to_tenant_b(
        self,
        route: Route,
        tenant_a: TenantA,
        as_tenant_b: AsyncClient,
        admin_session: AsyncSession,
    ) -> None:
        """404, the same reply an id that never existed would get.

        Not 403: B holds every permission at ORG scope, so a 403 could only mean the
        authorization layer refused before the route ever looked the row up — and then the
        route's tenancy was never tested at all. And not 200, which would be the finding this
        whole module exists to make.
        """
        method, _path = route
        url, kwargs = _request_for(route, tenant_a)
        sent = f"{url} {kwargs!r}"
        before = await _snapshot(admin_session, tenant_a.org_id)

        response = await as_tenant_b.request(method, url, **kwargs)

        assert response.status_code == 404, (
            f"{_route_id(route)} answered {response.status_code} to tenant B naming tenant A's "
            "row. 2xx means the row was served across tenants — a BLOCKER. 403 means the "
            "permission layer refused first; B holds every permission at ORG, so that cannot "
            "be the reason, and the route's tenancy went untested."
        )
        assert not _leaked(tenant_a, response.text, sent=sent), (
            f"{_route_id(route)} refused, but the reply still carried tenant A's data: "
            f"{_leaked(tenant_a, response.text, sent=sent)}"
        )
        after = await _snapshot(admin_session, tenant_a.org_id)
        assert after == before, (
            f"{_route_id(route)} refused tenant B and still changed tenant A. A refusal that "
            "arrives after the write is not a refusal."
        )

    @pytest.mark.parametrize("route", OWN_ORG, ids=[_route_id(r) for r in OWN_ORG])
    async def test_a_route_naming_no_tenant_a_row_serves_tenant_b_only_its_own_organization(
        self,
        route: Route,
        tenant_a: TenantA,
        as_tenant_b: AsyncClient,
        admin_session: AsyncSession,
    ) -> None:
        """B's request lands in B, and A is neither visible in the reply nor touched by it.

        Success is asserted rather than tolerated, and it is the control for the test above:
        if B's full grant set were refused here, the 404s there could be permission refusals
        in disguise and the sweep would prove nothing about tenancy.
        """
        method, _path = route
        url, kwargs = _request_for(route, tenant_a)
        sent = f"{url} {kwargs!r}"
        before = await _snapshot(admin_session, tenant_a.org_id)

        response = await as_tenant_b.request(method, url, **kwargs)

        assert response.status_code == OWN_ORG_STATUS[method], (
            f"{_route_id(route)} answered {response.status_code} to tenant B in its own "
            "organization, holding every permission at ORG. Either the BODIES entry is no "
            f"longer a valid request, or the route is broken: {response.text}"
        )
        assert not _leaked(tenant_a, response.text, sent=sent), (
            f"{_route_id(route)} served tenant A's data to tenant B: "
            f"{_leaked(tenant_a, response.text, sent=sent)}. This is the cross-tenant leak."
        )
        after = await _snapshot(admin_session, tenant_a.org_id)
        assert after == before, (
            f"{_route_id(route)}, called by tenant B in its own organization, changed tenant A."
        )


class TestTheExemptRoutes:
    async def test_the_permission_catalogue_is_identical_for_every_tenant(
        self,
        tenant_a: TenantA,
        tenant_b: Principal,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """Global by design, so the right assertion is sameness rather than refusal.

        The catalogue is the platform's vocabulary: a customer-invented permission could not be
        enforced by any service, and every organization reads the whole list. If the two replies
        ever differed, the catalogue would have become tenant-scoped without anyone deciding it.
        """
        as_a_principal = principal_for(org.id, people["manager"], EVERYTHING_AT_ORG)
        async with _client_for(as_a_principal) as as_a:
            seen_by_a = await as_a.get(f"{PREFIX}/permissions")
        async with _client_for(tenant_b) as as_b:
            seen_by_b = await as_b.get(f"{PREFIX}/permissions")

        assert seen_by_a.status_code == 200 and seen_by_b.status_code == 200
        assert seen_by_a.json(), "the catalogue is empty — nothing was compared"
        assert seen_by_a.json() == seen_by_b.json(), (
            "the permission catalogue differs between organizations; it is global by design"
        )

    async def test_a_signup_through_a_tenant_b_request_touches_neither_a_nor_b(
        self,
        tenant_a: TenantA,
        tenant_b: Principal,
        as_tenant_b: AsyncClient,
        admin_session: AsyncSession,
    ) -> None:
        """Sign-up names no organization and is bound to the id it generates (ADR-0013 §3).

        Made through a request bound to B, it must not write into B — the route never reads
        the caller's tenant — and cannot reach A. The proof is a third organization with
        exactly one membership, and two snapshots that did not move.
        """
        run = uuid.uuid4().hex[:8]
        before_a = await _snapshot(admin_session, tenant_a.org_id)
        before_b = await _snapshot(admin_session, tenant_b.organization_id)

        response = await as_tenant_b.post(
            f"{PREFIX}/account/signup",
            json={
                "organization_name": "Third Party",
                "slug": f"third-{run}",
                "full_name": "Third Owner",
                "email": f"third-{run}@example.com",
            },
        )

        assert response.status_code == 201, response.text
        new_org = uuid.UUID(response.json()["organization_id"])
        assert new_org not in {tenant_a.org_id, tenant_b.organization_id}
        assert await _snapshot(admin_session, tenant_a.org_id) == before_a, (
            "a sign-up through a B-bound request changed tenant A"
        )
        assert await _snapshot(admin_session, tenant_b.organization_id) == before_b, (
            "a sign-up through a B-bound request changed tenant B"
        )
        members = (
            (
                await admin_session.execute(
                    select(Membership).where(Membership.organization_id == new_org)
                )
            )
            .scalars()
            .all()
        )
        assert len(members) == 1 and members[0].is_owner is True

    async def test_a_tenant_a_invitation_token_presented_through_a_tenant_b_request_finds_nothing(
        self, tenant_a: TenantA, as_tenant_b: AsyncClient, admin_session: AsyncSession
    ) -> None:
        """The token is the credential, but the row it names lives in A.

        The request's transaction is bound to B, so the policy hides A's invitation and the
        route answers the same 404 it gives an unknown token — nothing is learned, nothing is
        accepted, nobody is created. A membership in A can only be created by a request bound
        to A.
        """
        before = await _snapshot(admin_session, tenant_a.org_id)

        response = await as_tenant_b.post(
            f"{PREFIX}/invitations/accept",
            json={"token": tenant_a.invitation_token, "full_name": "Gate Crasher"},
        )

        assert response.status_code == 404, (
            f"accepting tenant A's invitation through a tenant B request answered "
            f"{response.status_code}: {response.text}"
        )
        assert await _snapshot(admin_session, tenant_a.org_id) == before, (
            "tenant A's invitation was consumed, or a membership created, by a request bound "
            "to tenant B"
        )
        async with admin_session.begin():
            created = (
                await admin_session.execute(
                    text("SELECT count(*) FROM account.users WHERE email = :e"), {"e": A_INVITEE}
                )
            ).scalar_one()
        assert created == 0, "the invitee was created even though the invitation was not found"


# --------------------------------------------------------------------- the maps


class TestTheMapsAreCompleteByConstruction:
    def test_the_route_table_was_actually_enumerated(self) -> None:
        """A walk that found nothing would parametrise nothing and pass. So: one known route."""
        assert ("GET", f"{PREFIX}/members") in ROUTES, (
            "the route enumeration found no member list. Either the public router is no longer "
            "mounted under /api/v1 or `app.routes` no longer flattens mounted routers."
        )
        assert NAMING_A and OWN_ORG, "the sweep has no cases on one side of the partition"

    def test_every_path_parameter_is_mapped_to_a_tenant_a_identifier(self) -> None:
        """A new `{thing_id}` fails here until somebody says which of A's rows fills it."""
        in_routes = {name for _method, path in ROUTES for name in re.findall(r"{(\w+)}", path)}
        unmapped = in_routes - PATH_PARAMS.keys()
        assert not unmapped, (
            f"{sorted(unmapped)} appear in the route table and are not in PATH_PARAMS. Decide "
            "which of tenant A's identifiers the sweep substitutes for each, and seed it."
        )
        stale = PATH_PARAMS.keys() - in_routes
        assert not stale, f"PATH_PARAMS maps {sorted(stale)}, which no route uses any more"

    def test_every_write_route_has_a_body_or_is_exempt(self) -> None:
        """A new write route fails here until somebody decides how it is swept."""
        writes = {route for route in ROUTES if route[0] != "GET"}
        unmapped = writes - BODIES.keys() - ALLOW_LIST.keys()
        assert not unmapped, (
            f"{sorted(unmapped)} are write routes with no BODIES entry and no exemption. Give "
            "each a minimal valid body — naming tenant A's rows wherever the schema allows."
        )
        stale = (BODIES.keys() | ALLOW_LIST.keys()) - set(ROUTES)
        assert not stale, f"{sorted(stale)} are mapped or exempt but are no longer routes"

    def test_every_exemption_carries_a_real_reason(self) -> None:
        """A stale or bare exemption is worse than none: it excuses whatever takes the path next."""
        for route, reason in ALLOW_LIST.items():
            assert len(reason) > 40, f"{_route_id(route)} is exempt without a real reason"

    def test_the_snapshot_covers_every_table_in_the_schema(self) -> None:
        """A table added without a snapshot query is a table the sweep cannot see writes to."""
        tables = {table.name for table in Base.metadata.sorted_tables}
        uncovered = tables - SNAPSHOT_QUERIES.keys() - GLOBAL_TABLES
        assert not uncovered, (
            f"{sorted(uncovered)} have no snapshot query. Add one scoped to the organization, "
            "or add the table to GLOBAL_TABLES with a reason it carries no tenant."
        )
        stale = (SNAPSHOT_QUERIES.keys() | GLOBAL_TABLES) - tables
        assert not stale, f"{sorted(stale)} are snapshotted or exempt but are not tables"
