"""ADR-0002 slice one, exit gate for auth: every route with a principal is tenant-bound.

Most of this service runs before an organization exists — sign-in, the second factor,
refresh, logout, password reset — and those routes are allow-listed below with the reason.
The rest carry a `CurrentPrincipal`, and for those the transaction must be bound to the
principal's organization as its first statement, by the chokepoint, not by the route.
The account service carries the full version of these tests; this is the smaller gate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.main import app
from app.models import SCHEMA, MfaFactor
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC, get_session, get_tenant_session
from tests.conftest import MEMBERSHIP_A, ORG_A, USER

pytestmark = pytest.mark.integration

#: Routes under /api/v1 allowed on a bare session, each with the reason.
PRE_ORGANIZATION = "runs before an organization has been chosen; there is no tenant to bind"
BARE_SESSION_ROUTES = {
    "/api/v1/auth/login": f"Sign-in {PRE_ORGANIZATION}.",
    "/api/v1/auth/select-org": f"Finishing sign-in {PRE_ORGANIZATION} until it completes.",
    "/api/v1/auth/mfa/verify": f"The second factor {PRE_ORGANIZATION}.",
    "/api/v1/auth/refresh": (
        "Refresh presents a cookie, not a principal; the organization is read from the "
        "stored token inside the service, so there is nothing to bind before it runs."
    ),
    "/api/v1/auth/logout": (
        "Logout presents a cookie, not a principal, and must work for a stale one."
    ),
    "/api/v1/auth/sso/start": (
        "Starting single sign-on has no principal — there is no token yet. It does not stay "
        "unbound, though: it resolves the organization from a DNS-verified domain through "
        "account-service and binds the transaction to it before reading `org_idp_configs`, "
        "which is the one table here under a policy (ADR-0002, path 4: bypass then narrow)."
    ),
    "/api/v1/auth/sso/callback": (
        "The provider redirects a browser here with no token of ours. The organization comes "
        "from the server-side state this service issued, and the transaction is bound to it "
        "before the configuration is read."
    ),
    "/api/v1/auth/password/forgot": f"A reset request is anonymous and {PRE_ORGANIZATION}.",
    "/api/v1/auth/password/reset": f"A reset confirmation is anonymous and {PRE_ORGANIZATION}.",
    "/api/v1/auth/operator/login": (
        "Operator sign-in authenticates a platform staff account that belongs to no "
        "organization; there is no tenant to bind."
    ),
    "/api/v1/auth/operator/refresh": (
        "Operator refresh presents a cookie, not a principal; the staff subject is read "
        "from the stored token, and an operator has no organization to bind."
    ),
    "/api/v1/auth/operator/logout": (
        "Operator logout presents a cookie, not a principal, and must work for a stale one."
    ),
    "/api/v1/auth/operator/mfa/enrol": (
        "Enrolling an operator's first factor happens against a pre-auth token, before any "
        "principal exists — the account being enrolled belongs to no organization to bind."
    ),
    "/api/v1/auth/operator/mfa/enrol/confirm": (
        "Same pre-auth-token shape as enrolment start; it completes the sign-in but still "
        "runs before a principal exists."
    ),
    "/api/v1/auth/operator/mfa/verify": (
        "The operator's second factor, mirroring the tenant one above: presented against a "
        "pre-auth token, not a principal, for an account with no organization to bind."
    ),
}


#: Routes that carry a principal **and** take a bare session, each with the reason. The chokepoint
#: (`get_tenant_session`) rolls back on an exception, which is exactly wrong for a route whose
#: whole job on the failure path is to *persist* something — a wrong second-factor code has to be
#: counted, and the lockout it trips has to stick, even though the request answers an error. Such
#: a route must bind the tenant itself, as the transaction's first statement; the behavioural
#: half of that is `TestThePrincipalRouteOnABareSessionBindsItself` below.
PRINCIPAL_ON_BARE_SESSION_ROUTES = {
    "/api/v1/auth/mfa/step-up": (
        "A signed-in member proves a one-time code. A wrong code is counted and can trip a "
        "lockout that ends the session; that write has to be committed before the route "
        "raises, which the rolling-back tenant chokepoint would undo. `services.step_up` "
        "binds the tenant GUC itself, first."
    ),
}


def _every_call(dependant: Dependant) -> set[Any]:
    found: set[Any] = set()
    stack = list(dependant.dependencies)
    while stack:
        sub = stack.pop()
        if sub.call is not None:
            found.add(sub.call)
        stack.extend(sub.dependencies)
    return found


def _direct_calls(dependant: Dependant) -> set[Any]:
    return {sub.call for sub in dependant.dependencies if sub.call is not None}


@dataclass(frozen=True)
class _Route:
    path: str
    methods: set[str]
    dependant: Dependant


def _routes() -> list[_Route]:
    """Every mounted API route with the path a client would use.

    Included routers are not flattened into `app.routes` — each is a wrapper carrying its
    prefix — so the walk goes through FastAPI's own route-context iterator rather than
    the raw list, which would silently audit nothing.
    """
    found: list[_Route] = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            path = context.path or route.path
            methods = set(context.methods or route.methods)
            found.append(_Route(path=path, methods=methods, dependant=route.dependant))
    return found


class TestRouteAudit:
    def test_every_route_with_a_principal_goes_through_the_chokepoint(self) -> None:
        offenders = sorted(
            route.path
            for route in _routes()
            if get_principal in _every_call(route.dependant)
            and get_tenant_session not in _every_call(route.dependant)
            and route.path not in PRINCIPAL_ON_BARE_SESSION_ROUTES
        )
        assert not offenders, f"{offenders} resolve a principal on an unbound transaction"

    def test_no_public_route_takes_a_bare_session_unless_named_here(self) -> None:
        offenders = sorted(
            route.path
            for route in _routes()
            if route.path.startswith("/api/v1")
            and get_session in _direct_calls(route.dependant)
            and route.path not in BARE_SESSION_ROUTES
            and route.path not in PRINCIPAL_ON_BARE_SESSION_ROUTES
        )
        assert not offenders, f"{offenders} take `get_session` directly and are not allow-listed"

    def test_the_allow_list_is_real_and_documented(self) -> None:
        bare = {route.path for route in _routes() if get_session in _direct_calls(route.dependant)}
        for path, reason in BARE_SESSION_ROUTES.items():
            assert path in bare, f"{path} is allow-listed but no longer takes a bare session"
            assert len(reason) > 40, f"{path} is exempt without a real reason"

    def test_the_allow_listed_routes_carry_no_principal(self) -> None:
        """A route that stays on a bare session must genuinely have nobody to bind for."""
        for route in _routes():
            if route.path in BARE_SESSION_ROUTES:
                assert get_principal not in _every_call(route.dependant), route.path

    def test_the_principal_exemptions_are_real_and_documented(self) -> None:
        """An exemption is only honest while the route still holds a principal, still takes a
        bare session, and does not *also* go through the chokepoint (which would make it a
        second, redundant transaction opener)."""
        by_path = {route.path: route for route in _routes()}
        for path, reason in PRINCIPAL_ON_BARE_SESSION_ROUTES.items():
            assert path in by_path, f"{path} is exempt but no longer exists"
            calls = _every_call(by_path[path].dependant)
            assert get_principal in calls, f"{path} is exempt but carries no principal"
            assert get_session in _direct_calls(by_path[path].dependant), path
            assert get_tenant_session not in calls, f"{path} no longer needs the exemption"
            assert len(reason) > 40, f"{path} is exempt without a real reason"

    def test_the_audit_is_looking_at_something(self) -> None:
        """Six routes with a principal exist today: switch-org, me, the two enrolment steps,
        the step-up and the password change. Zero would mean the walk is broken."""
        guarded = [r for r in _routes() if get_principal in _every_call(r.dependant)]
        assert len(guarded) >= 5


Statement = tuple[str, Any]


@pytest_asyncio.fixture
async def statements(session: AsyncSession) -> AsyncIterator[list[Statement]]:
    seen: list[Statement] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append((statement, parameters))

    assert isinstance(session.bind, AsyncEngine)
    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine, "before_cursor_execute", _record)


@contextmanager
def _as(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _signed_in() -> Principal:
    return Principal(
        sub=USER,
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        session_id=str(uuid.uuid4()),
        roles=["MEMBER"],
        amr=["pwd"],
    )


def _assert_bound_first(seen: list[Statement], *, touches: str) -> None:
    assert seen, "the request executed nothing"
    first, params = seen[0]
    assert f"set_config('{TENANT_GUC}'" in first, f"first statement was {first!r}"
    assert str(ORG_A) in str(params), "bound to the wrong organization"
    # Outside production the probe reads it straight back, still before any table.
    assert "current_setting" in seen[1][0]
    assert any(touches in sql for sql, _ in seen[2:]), f"nothing touched {touches}"


class TestTheGucIsSetInsideEveryRequest:
    async def test_a_read_binds_the_tenant_before_anything_else(
        self, client: AsyncClient, registered: None, statements: list[Statement]
    ) -> None:
        with _as(_signed_in()):
            statements.clear()
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        _assert_bound_first(statements, touches="credentials")

    async def test_a_write_binds_the_tenant_and_commits_through_the_chokepoint(
        self,
        client: AsyncClient,
        session: AsyncSession,
        registered: None,
        statements: list[Statement],
    ) -> None:
        """The service function commits nothing now; the transaction the chokepoint
        opened is the one that ends, and it has to end with a commit or every write
        behind a principal silently disappears."""
        with _as(_signed_in()):
            statements.clear()
            response = await client.post("/api/v1/auth/mfa/enrol")
        assert response.status_code == 201, response.text
        _assert_bound_first(statements, touches="mfa_factors")

        # A fresh transaction on the same session sees only what was committed.
        await session.rollback()
        count = (await session.execute(select(func.count()).select_from(MfaFactor))).scalar_one()
        await session.rollback()
        assert count == 1

    async def test_the_guc_does_not_leak_into_the_next_transaction(
        self, client: AsyncClient, session: AsyncSession, registered: None
    ) -> None:
        with _as(_signed_in()):
            assert (await client.get("/api/v1/auth/me")).status_code == 200
        value = (
            await session.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
        ).scalar_one()
        await session.rollback()
        assert value in ("", None), f"the tenant leaked: {value!r}"


class TestThePrincipalRouteOnABareSessionBindsItself:
    async def test_the_step_up_binds_the_tenant_first_even_when_it_refuses(
        self, client: AsyncClient, registered: None, statements: list[Statement]
    ) -> None:
        """A refusal is the path with the least reason to have bound anything, so it is the one
        worth pinning: the request below is turned away at the session check (`sid` names no
        session), and the first thing the transaction still did was `set_config` for ORG_A."""
        with _as(_signed_in()):
            statements.clear()
            response = await client.post("/api/v1/auth/mfa/step-up", json={"code": "123456"})
        assert response.status_code == 401, response.text
        first, params = statements[0]
        assert f"set_config('{TENANT_GUC}'" in first, f"first statement was {first!r}"
        assert str(ORG_A) in str(params), "bound to the wrong organization"


class TestThePolicyClassificationHolds:
    """ADR-0002 §6 for auth: policy `org_idp_configs`, exempt the rest and say so.

    Auth is deliberately "last and minimal": almost everything here is user-keyed or
    platform-global and predates any organization, so `org_idp_configs` — per-organization
    IdP secrets, certificates and the `enforce_sso` switch — is the one table a tenant
    policy is expressable on. This pins both halves: the mandated table is under a forced
    policy, and nothing else silently grows one (a policy on `processed_events` or on the
    sentinel-org tables would break idempotency and the pre-organization flows).
    """

    async def test_exactly_org_idp_configs_has_row_security(self, session: AsyncSession) -> None:
        secured = (
            (
                await session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :s AND rowsecurity"),
                    {"s": SCHEMA},
                )
            )
            .scalars()
            .all()
        )
        await session.rollback()
        assert secured == ["org_idp_configs"]

    async def test_the_policy_is_forced_and_checks_writes(self, session: AsyncSession) -> None:
        """FORCE covers a future owner mistake; WITH CHECK covers cross-tenant INSERTs."""
        forced = (
            await session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
                {"t": f"{SCHEMA}.org_idp_configs"},
            )
        ).scalar_one()
        with_check = (
            await session.execute(
                text(
                    "SELECT polwithcheck IS NOT NULL FROM pg_policy "
                    "WHERE polrelid = to_regclass(:t) AND polname = 'tenant_isolation'"
                ),
                {"t": f"{SCHEMA}.org_idp_configs"},
            )
        ).scalar_one()
        await session.rollback()
        assert forced is True
        assert with_check is True
