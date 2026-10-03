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
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import security, services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Credential, MfaFactor
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC, get_session, get_tenant_session
from tests.conftest import MEMBERSHIP_A, ORG_A, PASSWORD, USER

pytestmark = pytest.mark.integration

NEW_PASSWORD = "a-brand-new-pw-9"  # pragma: allowlist secret

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
    "/api/v1/auth/oauth/{provider}/start": (
        "Starting self-serve Google/Microsoft sign-in has no principal — there is no token "
        "yet — and unlike SSO's own start route, nothing to bind either: the provider is a "
        "fixed platform-wide registration (`PlatformOAuthProvider`), not something read from "
        "an organization's own schema, so there is no tenant for this request to resolve."
    ),
    "/api/v1/auth/oauth/{provider}/callback": (
        "The provider redirects a browser here with no token of ours, same as SSO's own "
        "callback — but unlike SSO, there is still nothing to bind: the pending request names "
        "a platform-wide provider, not an organization's own schema, and the session this "
        "mints is deliberately unpinned (`_federated_organization_pin`), free to move between "
        "every organization the resolved person belongs to rather than bound to one."
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
    "/api/v1/auth/mfa/enrol": (
        "Starting an enrolment re-asks for the member's password (and a code from an existing "
        "authenticator). A wrong one is counted against the sign-in lockouts and can end the "
        "session; that write has to be committed before the route raises, which the "
        "rolling-back tenant chokepoint would undo. `services.begin_member_enrolment` binds "
        "the tenant GUC itself, first."
    ),
    "/api/v1/auth/mfa/enrol/confirm": (
        "Confirming an enrolment counts every wrong code against the pending factor and voids "
        "the factor at the cap (ADR-0023, m1); both have to be committed before the route "
        "raises, which the rolling-back tenant chokepoint would undo. "
        "`services.confirm_member_enrolment` binds the tenant GUC itself, first."
    ),
    "/api/v1/auth/password/change": (
        "Changing a password re-asks for the current one, and a wrong guess is counted against "
        "the sign-in lockout and audited (ADR-0023, risk 9: otherwise it is a password oracle). "
        "That write has to be committed before the route raises, which the rolling-back tenant "
        "chokepoint would undo. `services.change_password` binds the tenant GUC itself, first."
    ),
}

#: Every route that carries a principal, exactly. A new one is a decision — does it take the
#: chokepoint, or does it need the reasoned exemption above — so it has to be added here on
#: purpose; the previous `>= 5` floor let a route appear (or vanish) unnoticed. (Eleven: the
#: member's own six, the two SSO configuration routes, and the three operator ones. Three of the
#: six — step-up, enrolment start and confirm — and password change take a bare session and bind
#: the tenant themselves: `PRINCIPAL_ON_BARE_SESSION_ROUTES`.)
PRINCIPAL_ROUTES = {
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/mfa/enrol"),
    ("POST", "/api/v1/auth/mfa/enrol/confirm"),
    ("POST", "/api/v1/auth/mfa/step-up"),
    ("POST", "/api/v1/auth/operator/enrollment-codes"),
    ("GET", "/api/v1/auth/operator/me"),
    ("POST", "/api/v1/auth/operator/staff/{staff_id}/mfa:reset"),
    ("POST", "/api/v1/auth/password/change"),
    ("GET", "/api/v1/auth/sso/config"),
    ("PUT", "/api/v1/auth/sso/config"),
    ("POST", "/api/v1/auth/switch-org"),
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

    def test_the_set_of_routes_with_a_principal_is_exactly_the_pinned_one(self) -> None:
        """Exact, so it is also the proof the walk is looking at something: zero would mean the
        route iterator is broken, and an extra or missing route fails with its name."""
        found = {
            (method, route.path)
            for route in _routes()
            if get_principal in _every_call(route.dependant)
            for method in route.methods
        }
        added, removed = sorted(found - PRINCIPAL_ROUTES), sorted(PRINCIPAL_ROUTES - found)
        assert found == PRINCIPAL_ROUTES, f"added: {added}, removed: {removed}"


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


def _signed_in(session_id: uuid.UUID | None = None) -> Principal:
    return Principal(
        sub=USER,
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        session_id=str(session_id or uuid.uuid4()),
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
        statements: list[Statement],
    ) -> None:
        """The service function commits nothing; the transaction the chokepoint opened is the one
        that ends, and it has to end with a commit or every write behind a principal silently
        disappears. `GET /auth/me` is the write here: it creates the member's credential row on
        first sight (there is none, on purpose — this test does not ask for `registered`).
        Enrolment confirmation and password change used to be the examples; both now count
        failures and so commit for themselves (`PRINCIPAL_ON_BARE_SESSION_ROUTES`)."""
        with _as(_signed_in()):
            statements.clear()
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        _assert_bound_first(statements, touches="credentials")

        # A fresh transaction on the same session sees only what was committed.
        await session.rollback()
        created = (await session.execute(select(func.count()).select_from(Credential))).scalar_one()
        await session.rollback()
        assert created == 1

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

    async def test_the_enrolment_start_binds_first_probes_and_commits(
        self,
        client: AsyncClient,
        session: AsyncSession,
        registered: None,
        statements: list[Statement],
    ) -> None:
        """The same guarantee the chokepoint gives, by hand: the tenant is the first statement,
        the read-back probe follows it outside production, and the factor it creates survives —
        this route commits for itself, so a missing commit would make the write vanish."""
        auth_session = await services.start_session(
            session, user_id=USER, cfg=settings(), amr=["pwd"]
        )
        await session.commit()
        with _as(_signed_in(auth_session.id)):
            statements.clear()
            response = await client.post("/api/v1/auth/mfa/enrol", json={"password": PASSWORD})
        assert response.status_code == 201, response.text
        _assert_bound_first(statements, touches="mfa_factors")

        await session.rollback()
        count = (await session.execute(select(func.count()).select_from(MfaFactor))).scalar_one()
        await session.rollback()
        assert count == 1

    async def test_the_confirm_binds_first_and_a_refused_one_still_does(
        self, client: AsyncClient, registered: None, statements: list[Statement]
    ) -> None:
        """Refused at the session check (`sid` names no session): the transaction's first
        statement was still `set_config` for ORG_A."""
        with _as(_signed_in()):
            statements.clear()
            response = await client.post(
                "/api/v1/auth/mfa/enrol/confirm",
                json={"factor_id": str(uuid.uuid4()), "code": "123456"},
            )
        assert response.status_code == 401, response.text
        first, params = statements[0]
        assert f"set_config('{TENANT_GUC}'" in first, f"first statement was {first!r}"
        assert str(ORG_A) in str(params), "bound to the wrong organization"

    async def test_the_confirm_commits_for_itself(
        self,
        client: AsyncClient,
        session: AsyncSession,
        registered: None,
        statements: list[Statement],
    ) -> None:
        """Confirmation is a write on a bare session, so a missing commit would make the
        confirmed factor and the recovery codes vanish with the request."""
        cfg = settings()
        auth_session = await services.start_session(session, user_id=USER, cfg=cfg, amr=["pwd"])
        factor, secret, _uri = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg, session_id=str(auth_session.id)
        )
        await session.commit()
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        with _as(_signed_in(auth_session.id)):
            statements.clear()
            response = await client.post(
                "/api/v1/auth/mfa/enrol/confirm",
                json={"factor_id": str(factor.id), "code": code},
            )
        assert response.status_code == 200, response.text
        _assert_bound_first(statements, touches="mfa_factors")

        await session.rollback()
        confirmed = (
            await session.execute(
                select(func.count())
                .select_from(MfaFactor)
                .where(MfaFactor.confirmed_at.isnot(None))
            )
        ).scalar_one()
        await session.rollback()
        assert confirmed == 1

    async def test_the_password_change_binds_first_probes_and_commits(
        self,
        client: AsyncClient,
        session: AsyncSession,
        registered: None,
        statements: list[Statement],
    ) -> None:
        auth_session = await services.start_session(
            session, user_id=USER, cfg=settings(), amr=["pwd"]
        )
        await session.commit()
        with _as(_signed_in(auth_session.id)):
            statements.clear()
            response = await client.post(
                "/api/v1/auth/password/change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            )
        assert response.status_code == 204, response.text
        _assert_bound_first(statements, touches="credentials")

        await session.rollback()
        stored = (
            await session.execute(
                select(Credential.password_hash).where(Credential.user_id == USER)
            )
        ).scalar_one()
        await session.rollback()
        assert security.verify_password(stored, NEW_PASSWORD), "the change did not commit"

    async def test_a_refused_password_change_still_binds_first(
        self, client: AsyncClient, registered: None, statements: list[Statement]
    ) -> None:
        with _as(_signed_in()):
            statements.clear()
            response = await client.post(
                "/api/v1/auth/password/change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            )
        assert response.status_code == 401, response.text
        first, params = statements[0]
        assert f"set_config('{TENANT_GUC}'" in first, f"first statement was {first!r}"
        assert str(ORG_A) in str(params), "bound to the wrong organization"

    async def test_a_refused_enrolment_start_still_binds_first(
        self, client: AsyncClient, registered: None, statements: list[Statement]
    ) -> None:
        with _as(_signed_in()):
            statements.clear()
            response = await client.post("/api/v1/auth/mfa/enrol", json={"password": PASSWORD})
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

    async def test_platform_oauth_providers_not_under_rls(self, session: AsyncSession) -> None:
        """Platform-wide config, not a tenant table — see models.py docstring."""
        result = await session.execute(
            text(
                "SELECT relrowsecurity FROM pg_class "
                "WHERE relname = 'platform_oauth_providers' "
                "AND relnamespace = 'auth'::regnamespace"
            )
        )
        assert result.scalar_one() is False
