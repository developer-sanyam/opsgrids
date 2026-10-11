"""Whose id OWN and TEAM compare: the membership subject (CRM overhaul R2.2, registry A1-A12).

`register_scope_resolver` used to write a dict nothing read, and `ScopeContext.apply` and
`assert_can_touch` hard-wired `Principal.subject_ref`, which is only ever an HR employee id. A
product now declares, once, whose id its OWN and TEAM rungs compare (`OwnerSubject`), `require()`
snapshots that onto the context, and `ScopeContext.own_ref` is the one place that reads it.

Nothing in the platform opts in yet: every product stays on the employee link, and these tests
are what says so.
"""

from __future__ import annotations

import ast
import itertools
import uuid
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Select, column, select
from sqlalchemy import false as sa_false
from starlette.requests import Request

import platform_core
from platform_core import auth
from platform_core.app import create_app
from platform_core.auth import (
    OwnerSubject,
    Principal,
    Scope,
    ScopeContext,
    _principal_from_claims,
    _principal_from_gateway,
    get_principal,
    owner_subject_for,
    register_scope_resolver,
    require,
    reset_scope_resolvers,
)
from platform_core.config import ServiceSettings
from platform_core.errors import ForbiddenProblem, UnauthorizedProblem
from platform_core.gateway import SPOOFABLE
from platform_core.internal_auth import (
    GATEWAY_AUDIENCE,
    forwarded_headers_digest,
    mint_service_token,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ORG = uuid.uuid4()
#: The caller's employee link and the caller's membership id: two different people-ids for the
#: one person, so a test can tell which of them a comparison used.
EMPLOYEE = uuid.uuid4()
MEMBER = uuid.uuid4()


def _principal(
    perms: list[str] | None = None,
    *,
    membership_id: uuid.UUID | None = MEMBER,
    subject_ref: uuid.UUID | None = EMPLOYEE,
) -> Principal:
    return Principal(
        sub=uuid.uuid4(),
        organization_id=ORG,
        membership_id=membership_id,
        subject_ref=subject_ref,
        perms=perms or [],
    )


class _Resolver:
    """A stand-in `ScopeResolver`. The registry stores the factory and never calls it."""

    def owner_column(self) -> Any:
        return None

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


class _OtherResolver(_Resolver):
    """A second factory, so a test can tell which one the registry kept."""


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """The registry is process-global. Empty it around every test so import order and test
    pollution can never decide which subject a context carries."""
    reset_scope_resolvers()
    yield
    reset_scope_resolvers()


class TestOwnerSubject:
    def test_values_are_the_wire_strings(self) -> None:
        assert [member.value for member in OwnerSubject] == ["employee_link", "membership"]
        assert OwnerSubject("employee_link") is OwnerSubject.EMPLOYEE_LINK
        assert OwnerSubject("membership") is OwnerSubject.MEMBERSHIP

    def test_it_is_exported_from_auth_and_the_package_root(self) -> None:
        assert "OwnerSubject" in auth.__all__
        assert "OwnerSubject" in platform_core.__all__
        assert platform_core.OwnerSubject is OwnerSubject


class TestRegistry:
    def test_two_positional_arguments_still_register_the_employee_link(self) -> None:
        # The sixteen existing `scope.register()` call shapes: (product, factory), nothing else.
        register_scope_resolver("hrms", _Resolver)
        assert auth._resolvers["hrms"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)
        assert owner_subject_for("hrms.time.leave.read") is OwnerSubject.EMPLOYEE_LINK

    def test_a_membership_registration_is_recorded(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.MEMBERSHIP

    def test_the_subject_is_keyword_only(self) -> None:
        # Called through `Any` so the type checker does not refuse the very call being pinned.
        register: Any = register_scope_resolver
        with pytest.raises(TypeError):
            register("crm", _Resolver, OwnerSubject.MEMBERSHIP)

    def test_re_registering_the_same_subject_is_a_no_op_that_keeps_the_first_factory(self) -> None:
        # customer registers `crm` twice from one process (api/routes.py and api/c1_customer360.py).
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        register_scope_resolver("crm", _OtherResolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.MEMBERSHIP)

        register_scope_resolver("hrms", _Resolver)
        register_scope_resolver("hrms", _OtherResolver)
        assert auth._resolvers["hrms"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)

    def test_re_registering_a_different_subject_raises_value_error_naming_both(self) -> None:
        register_scope_resolver("crm", _Resolver)
        with pytest.raises(ValueError, match="'crm'") as refused:
            register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert "employee_link" in str(refused.value)
        assert "membership" in str(refused.value)
        # The refused call changed nothing.
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)

    def test_dropping_the_subject_on_a_second_registration_is_a_different_subject(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        with pytest.raises(ValueError, match="'crm'"):
            register_scope_resolver("crm", _Resolver)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.MEMBERSHIP

    def test_reset_empties_the_registry(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        reset_scope_resolvers()
        assert auth._resolvers == {}
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.EMPLOYEE_LINK
        # A product can register again, with any subject, once the registry is empty.
        register_scope_resolver("crm", _Resolver)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.EMPLOYEE_LINK


class TestOwnerSubjectFor:
    def test_uses_the_text_before_the_first_dot(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm.book.company.read") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("crm.lead.lead.read_sensitive") is OwnerSubject.MEMBERSHIP
        # A product is a whole word: a permission that merely starts with the same letters is not.
        assert owner_subject_for("crmx.book.company.read") is OwnerSubject.EMPLOYEE_LINK

    def test_an_unregistered_product_is_the_employee_link(self) -> None:
        assert owner_subject_for("hrms.time.leave.read") is OwnerSubject.EMPLOYEE_LINK

    def test_a_prefix_miss_is_the_employee_link(self) -> None:
        # notification registers `notification`, while most of its routes require `engage.*`
        # codes. Opting `notification` in would therefore not reach them, and must not guess.
        register_scope_resolver("notification", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("notification.delivery.template.read") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("engage.campaign.campaign.send") is OwnerSubject.EMPLOYEE_LINK

    def test_a_permission_with_no_dot_is_looked_up_whole(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("hrms") is OwnerSubject.EMPLOYEE_LINK


class TestNoProductionReset:
    def test_no_service_calls_reset_scope_resolvers(self) -> None:
        """`reset_scope_resolvers` is a test hook. A service that called it at import or at
        request time would let ordering decide which subject a context carries."""
        files = sorted((REPO_ROOT / "services").glob("*/app/**/*.py"))
        assert (REPO_ROOT / "services" / "customer" / "app" / "scope.py") in files, (
            "the scan did not find service code; the directory layout moved"
        )
        offenders: list[str] = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                name = (
                    node.id
                    if isinstance(node, ast.Name)
                    else node.attr
                    if isinstance(node, ast.Attribute)
                    else node.name
                    if isinstance(node, ast.alias)
                    else None
                )
                if name == "reset_scope_resolvers":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', 0)}")
        assert offenders == []


class TestRequireSnapshot:
    def test_the_dependency_reads_the_registry_when_it_runs_not_when_it_is_built(self) -> None:
        # A route module builds `require(...)` at import, usually before its service's own
        # `scope.register()` line has run. The subject must still be the registered one.
        dependency = require("crm.pipeline.deal.read")
        principal = _principal(["crm.pipeline.deal.read:ORG"])

        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert dependency(principal).owner_subject is OwnerSubject.MEMBERSHIP

        reset_scope_resolvers()
        assert dependency(principal).owner_subject is OwnerSubject.EMPLOYEE_LINK

    def test_an_unregistered_product_and_a_prefix_miss_get_the_employee_link(self) -> None:
        register_scope_resolver("notification", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        principal = _principal(["engage.campaign.campaign.send:ORG", "hrms.time.leave.read:OWN"])

        assert (
            require("engage.campaign.campaign.send")(principal).owner_subject
            is OwnerSubject.EMPLOYEE_LINK
        )
        assert (
            require("hrms.time.leave.read")(principal).owner_subject is OwnerSubject.EMPLOYEE_LINK
        )

    def test_a_missing_permission_is_refused_before_any_subject_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(permission: str) -> OwnerSubject:
            raise AssertionError("the subject was read before the permission was checked")

        monkeypatch.setattr(auth, "owner_subject_for", explode)
        principal = _principal(["crm.pipeline.deal.read:ORG"])

        with pytest.raises(
            ForbiddenProblem, match=r"Missing permission: crm\.pipeline\.deal\.write"
        ):
            require("crm.pipeline.deal.write")(principal)

    def test_a_wildcard_grant_takes_the_subject_of_the_permission_being_required(self) -> None:
        # The subject follows the permission a route requires, not the grant that satisfied it.
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        ctx = require("crm.pipeline.deal.read")(_principal(["crm.*:OWN"]))
        assert ctx.scope is Scope.OWN
        assert ctx.owner_subject is OwnerSubject.MEMBERSHIP

    def test_the_scope_floor_still_applies_to_a_membership_context(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        dependency = require("crm.book.owner.write", minimum=Scope.ORG)

        with pytest.raises(ForbiddenProblem, match="requires ORG scope; this grant is OWN"):
            dependency(_principal(["crm.book.owner.write:OWN"]))

        ctx = dependency(_principal(["crm.book.owner.write:ORG"]))
        assert ctx.scope is Scope.ORG
        assert ctx.owner_subject is OwnerSubject.MEMBERSHIP


class TestHandBuiltContexts:
    def test_a_hand_built_context_defaults_to_the_employee_link(self) -> None:
        # How customer, deal and finance build the ORG contexts they run under themselves.
        ctx = ScopeContext(
            principal=_principal(), scope=Scope.ORG, permission="crm.book.contact.write"
        )
        assert ctx.owner_subject is OwnerSubject.EMPLOYEE_LINK
        assert ctx.own_ref == EMPLOYEE

    def test_a_hand_built_context_does_not_read_the_registry(self) -> None:
        # Only `require()` snapshots a registration. A synthetic context is ORG, which returns
        # before any subject is read, so the default is harmless and nothing needs to guess.
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        ctx = ScopeContext(
            principal=_principal(), scope=Scope.ORG, permission="crm.book.contact.write"
        )
        assert ctx.owner_subject is OwnerSubject.EMPLOYEE_LINK


class TestOwnRef:
    @staticmethod
    def _ctx(
        subject: OwnerSubject,
        *,
        membership_id: uuid.UUID | None = MEMBER,
        subject_ref: uuid.UUID | None = EMPLOYEE,
    ) -> ScopeContext:
        return ScopeContext(
            principal=_principal(membership_id=membership_id, subject_ref=subject_ref),
            scope=Scope.OWN,
            permission="crm.book.company.read",
            owner_subject=subject,
        )

    def test_a_membership_context_reads_the_membership_id(self) -> None:
        assert self._ctx(OwnerSubject.MEMBERSHIP).own_ref == MEMBER

    def test_an_employee_link_context_reads_the_employee_link(self) -> None:
        assert self._ctx(OwnerSubject.EMPLOYEE_LINK).own_ref == EMPLOYEE

    def test_a_missing_id_is_none_and_never_the_other_one(self) -> None:
        # A membership context whose caller has no membership id must not quietly fall back to
        # the employee link, and the reverse: that would compare the wrong person's id.
        assert self._ctx(OwnerSubject.MEMBERSHIP, membership_id=None).own_ref is None
        assert self._ctx(OwnerSubject.EMPLOYEE_LINK, subject_ref=None).own_ref is None

    def test_subject_ref_keeps_meaning_the_raw_employee_link(self) -> None:
        # HR code reads `ctx.subject_ref` and means the employee link. Declaring a membership
        # subject for a context must not change what that property returns.
        assert self._ctx(OwnerSubject.MEMBERSHIP).subject_ref == EMPLOYEE
        assert self._ctx(OwnerSubject.MEMBERSHIP, subject_ref=None).subject_ref is None


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


#: Built at import, before any test registers a resolver, the way a service's route module builds
#: its dependencies before its own `scope.register()` line has run.
ReadCtx = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.read"))]
FloorCtx = Annotated[ScopeContext, Depends(require("crm.book.owner.write", minimum=Scope.ORG))]


def _probe_client(principal: Principal | None) -> AsyncClient:
    """A real FastAPI app with two routes behind `require(...)`. `principal=None` leaves the
    platform's own `get_principal` in place, so a request with no credentials is refused the way
    a service refuses it; otherwise the caller is injected the way service tests inject one."""
    router = APIRouter()

    @router.get("/probe/read")
    async def read(ctx: ReadCtx) -> dict[str, Any]:
        return {
            "scope": ctx.scope.value,
            "owner_subject": ctx.owner_subject.value,
            "own_ref": str(ctx.own_ref) if ctx.own_ref else None,
        }

    @router.get("/probe/floor")
    async def floor(ctx: FloorCtx) -> dict[str, str]:
        return {"scope": ctx.scope.value}

    app: FastAPI = create_app(_Settings(), title="probe", routers=[router], database=False)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestRequireOverHttp:
    """The same dependency through a real FastAPI app: the statuses a caller sees. This release
    adds no route, so these are the negative-authorization proof at the layer that decides."""

    async def test_no_credentials_is_a_401(self) -> None:
        async with _probe_client(None) as client:
            response = await client.get("/api/v1/probe/read")
        assert response.status_code == 401

    async def test_a_caller_without_the_permission_is_a_403(self) -> None:
        async with _probe_client(_principal(["crm.pipeline.deal.write:ORG"])) as client:
            response = await client.get("/api/v1/probe/read")
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission: crm.pipeline.deal.read"

    async def test_a_grant_below_the_scope_floor_is_a_403(self) -> None:
        async with _probe_client(_principal(["crm.book.owner.write:OWN"])) as client:
            response = await client.get("/api/v1/probe/floor")
        assert response.status_code == 403
        assert "requires ORG scope; this grant is OWN" in response.json()["detail"]

    async def test_a_membership_product_compares_the_membership_id_end_to_end(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        async with _probe_client(_principal(["crm.pipeline.deal.read:OWN"])) as client:
            response = await client.get("/api/v1/probe/read")
        assert response.status_code == 200
        assert response.json() == {
            "scope": "OWN",
            "owner_subject": "membership",
            "own_ref": str(MEMBER),
        }

    async def test_a_membership_caller_with_no_membership_id_owns_nothing(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        caller = _principal(["crm.pipeline.deal.read:OWN"], membership_id=None)
        async with _probe_client(caller) as client:
            response = await client.get("/api/v1/probe/read")
        assert response.status_code == 200
        assert response.json()["own_ref"] is None


# ---------------------------------------------------------------------------------------------
# The frozen oracle (DD-39). "Changes nothing by default" is a claim about the whole grid of
# scope x ids x columns, not about the handful of paths the older tests walk.
#
# `_legacy_apply` and `_legacy_assert_can_touch` are the bodies of `ScopeContext.apply` and
# `ScopeContext.assert_can_touch` as they read at main@caa7f112, copied verbatim apart from
# `self` becoming `ctx`. They are never edited: the day they stop describing today's behaviour is
# the day the platform has changed what OWN means for every product that did not opt in.
# ---------------------------------------------------------------------------------------------

OTHER = uuid.uuid4()
LOCATIONS = ["MUM", "PUN"]
_OWNER: Any = column("owner_id")
_MANAGER: Any = column("manager_id")
_LOCATION: Any = column("location_code")


def _legacy_apply(
    ctx: ScopeContext,
    stmt: Any,
    *,
    owner_col: Any,
    team_col: Any | None = None,
    location_col: Any | None = None,
) -> Any:
    if ctx.scope is Scope.ORG:
        return stmt
    if ctx.scope is Scope.OWN:
        if ctx.principal.subject_ref is None:
            return stmt.where(sa_false())
        return stmt.where(owner_col == ctx.principal.subject_ref)
    if ctx.scope is Scope.TEAM:
        if team_col is None:
            raise ForbiddenProblem("TEAM scope is not supported on this resource")
        if ctx.principal.subject_ref is None:
            return stmt.where(sa_false())
        return stmt.where(team_col == ctx.principal.subject_ref)
    if ctx.scope is Scope.LOCATION:
        if location_col is None:
            raise ForbiddenProblem("LOCATION scope is not supported on this resource")
        return stmt.where(location_col.in_(ctx.principal.locations))
    raise ForbiddenProblem("Unknown scope")


def _legacy_assert_can_touch(
    ctx: ScopeContext,
    *,
    owner_id: uuid.UUID | None,
    team_owner_id: uuid.UUID | None = None,
    location_code: str | None = None,
) -> None:
    if ctx.scope is Scope.ORG:
        return
    subject = ctx.principal.subject_ref
    if ctx.scope is Scope.OWN and subject is not None and owner_id == subject:
        return
    if ctx.scope is Scope.TEAM and subject is not None and team_owner_id == subject:
        return
    if ctx.scope is Scope.LOCATION:
        if location_code is not None and location_code in ctx.principal.locations:
            return
        raise ForbiddenProblem("Out of scope for this resource")
    raise ForbiddenProblem("Out of scope for this resource")


def _outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    """What a call did, in a form two calls can be compared by: the compiled SQL text and bound
    parameters of the statement it returned, or the exception type and message it raised."""
    try:
        result = call()
    except ForbiddenProblem as refused:
        return ("raised", type(refused).__name__, str(refused))
    if isinstance(result, Select):
        compiled = result.compile()
        return ("statement", str(compiled), compiled.params)
    return ("returned", result)


def _context(
    scope: Scope,
    subject_ref: uuid.UUID | None,
    membership_id: uuid.UUID | None,
    owner_subject: OwnerSubject | None = None,
) -> ScopeContext:
    """A context for the grid. `owner_subject=None` leaves the field unset, the way a hand-built
    context does, so the default itself is under test."""
    principal = Principal(
        sub=uuid.uuid4(),
        organization_id=ORG,
        subject_ref=subject_ref,
        membership_id=membership_id,
        locations=LOCATIONS,
    )
    if owner_subject is None:
        return ScopeContext(principal=principal, scope=scope, permission="x.y.z.read")
    return ScopeContext(
        principal=principal,
        scope=scope,
        permission="x.y.z.read",
        owner_subject=owner_subject,
    )


def _swap_in_the_membership_id(ctx: ScopeContext) -> ScopeContext:
    """The defect the oracle exists to catch, built from the same context: the employee-link
    logic run against the caller's membership id instead."""
    principal = ctx.principal.model_copy(update={"subject_ref": ctx.principal.membership_id})
    return ctx.model_copy(update={"principal": principal})


_ApplyFn = Callable[..., Any]
_TouchFn = Callable[..., None]


def _current_apply(ctx: ScopeContext, stmt: Any, **columns: Any) -> Any:
    return ctx.apply(stmt, **columns)


def _current_assert_can_touch(ctx: ScopeContext, **ids: Any) -> None:
    ctx.assert_can_touch(**ids)


def _apply_grid() -> Iterator[tuple[Scope, uuid.UUID | None, uuid.UUID | None, bool, bool]]:
    """Scope x employee link set or null x membership id set or null x team column present or
    absent x location column present or absent."""
    yield from itertools.product(
        Scope, (None, EMPLOYEE), (None, MEMBER), (False, True), (False, True)
    )


def _touch_grid() -> Iterator[tuple[Scope, uuid.UUID | None, uuid.UUID | None, Any, Any, Any]]:
    """Scope x employee link x membership id x the row's owner x the row's team owner x the row's
    location. Each id is absent, the caller's employee link, the caller's membership id, or a
    stranger's; so every "equals" in the object check is both true and false somewhere."""
    ids = (None, EMPLOYEE, MEMBER, OTHER)
    yield from itertools.product(
        Scope, (None, EMPLOYEE), (None, MEMBER), ids, ids, (None, "PUN", "DEL")
    )


#: One grid row where a candidate and the legacy logic disagree: the scope, the caller's employee
#: link and membership id, and the arguments of the call.
_Mismatch = tuple[Scope, uuid.UUID | None, uuid.UUID | None, dict[str, Any]]


def _apply_mismatches(candidate: _ApplyFn, subject: OwnerSubject | None = None) -> list[_Mismatch]:
    """Every grid row where `candidate` and the legacy `apply` disagree."""
    mismatches: list[_Mismatch] = []
    for scope, subject_ref, membership_id, with_team, with_location in _apply_grid():
        ctx = _context(scope, subject_ref, membership_id, subject)
        columns = {
            "owner_col": _OWNER,
            "team_col": _MANAGER if with_team else None,
            "location_col": _LOCATION if with_location else None,
        }
        expected = _outcome(partial(_legacy_apply, ctx, select(_OWNER), **columns))
        actual = _outcome(partial(candidate, ctx, select(_OWNER), **columns))
        if actual != expected:
            mismatches.append((scope, subject_ref, membership_id, columns))
    return mismatches


def _touch_mismatches(candidate: _TouchFn, subject: OwnerSubject | None = None) -> list[_Mismatch]:
    """Every grid row where `candidate` and the legacy `assert_can_touch` disagree."""
    mismatches: list[_Mismatch] = []
    for scope, subject_ref, membership_id, owner_id, team_owner_id, location_code in _touch_grid():
        ctx = _context(scope, subject_ref, membership_id, subject)
        ids = {"owner_id": owner_id, "team_owner_id": team_owner_id, "location_code": location_code}
        expected = _outcome(partial(_legacy_assert_can_touch, ctx, **ids))
        actual = _outcome(partial(candidate, ctx, **ids))
        if actual != expected:
            mismatches.append((scope, subject_ref, membership_id, ids))
    return mismatches


class TestEmployeeLinkParity:
    def test_every_combination_matches_the_pre_change_logic(self) -> None:
        assert _apply_mismatches(_current_apply) == []
        assert _touch_mismatches(_current_assert_can_touch) == []

    def test_naming_the_employee_link_explicitly_changes_nothing(self) -> None:
        subject = OwnerSubject.EMPLOYEE_LINK
        assert _apply_mismatches(_current_apply, subject) == []
        assert _touch_mismatches(_current_assert_can_touch, subject) == []

    def test_org_returns_the_very_same_statement(self) -> None:
        stmt = select(_OWNER)
        ctx = _context(Scope.ORG, None, None)
        assert ctx.apply(stmt, owner_col=_OWNER) is stmt

    def test_the_grid_has_the_size_the_oracle_claims(self) -> None:
        # 4 scopes x 2 x 2 x 2 x 2, and 4 scopes x 2 x 2 x 4 x 4 x 3: a grid that quietly shrank
        # would still pass, so its size is pinned.
        assert len(list(_apply_grid())) == 64
        assert len(list(_touch_grid())) == 768

    def test_the_oracle_catches_the_membership_id_leaking_into_apply(self) -> None:
        def leaky(ctx: ScopeContext, stmt: Any, **columns: Any) -> Any:
            return _legacy_apply(_swap_in_the_membership_id(ctx), stmt, **columns)

        mismatches = _apply_mismatches(leaky)
        # Both rungs that read the subject are caught, no other rung is touched, and a row only
        # differs when the caller's two ids do.
        assert {scope for scope, *_ in mismatches} == {Scope.OWN, Scope.TEAM}
        assert all(sref != mem for _scope, sref, mem, _columns in mismatches)

    def test_the_oracle_catches_the_membership_id_leaking_into_assert_can_touch(self) -> None:
        def leaky(ctx: ScopeContext, **ids: Any) -> None:
            _legacy_assert_can_touch(_swap_in_the_membership_id(ctx), **ids)

        mismatches = _touch_mismatches(leaky)
        assert {scope for scope, *_ in mismatches} == {Scope.OWN, Scope.TEAM}
        assert all(sref != mem for _scope, sref, mem, _ids in mismatches)


# ---------------------------------------------------------------------------------------------
# Where the two ids come from. No token or gateway change is part of this release, so the
# premise that `mem` and `sref` already arrive in a `Principal` is pinned here: until now nothing
# in libs/platform-core tested `_principal_from_claims`, or the forwarded `X-Membership-Id` and
# `X-Subject-Ref`, at all.
# ---------------------------------------------------------------------------------------------

USER = uuid.uuid4()


def _request(headers: dict[str, str], cfg: ServiceSettings) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/anything",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "app": SimpleNamespace(state=SimpleNamespace(settings=cfg)),
    }
    return Request(scope)


def _forwarded(cfg: ServiceSettings, extra: dict[str, str] | None = None) -> Request:
    """What a service receives from the gateway: a hop token that vouches for `USER` in `ORG`,
    the identity headers, and whichever further headers (`X-Membership-Id`, `X-Subject-Ref`)
    `extra` adds. The hop token binds the digest of those headers, as the gateway's does."""
    headers = {"X-User-Id": str(USER), "X-Org-Id": str(ORG), **(extra or {})}
    hop = mint_service_token(
        cfg,
        audience=GATEWAY_AUDIENCE,
        caller="gateway",
        claims={
            "uid": str(USER),
            "org": str(ORG),
            "fwd": forwarded_headers_digest(_request(headers, cfg).headers),
        },
    )
    return _request({"X-Internal-Token": hop, **headers}, cfg)


class TestPrincipalMapping:
    def test_mem_and_sref_claims_fill_membership_id_and_subject_ref(self) -> None:
        claims = {
            "sub": str(USER),
            "act_org": str(ORG),
            "mem": str(MEMBER),
            "sref": str(EMPLOYEE),
        }
        principal = _principal_from_claims(claims)
        assert principal.membership_id == MEMBER
        assert principal.subject_ref == EMPLOYEE

    def test_a_crm_only_token_has_a_membership_and_no_employee_link(self) -> None:
        principal = _principal_from_claims(
            {"sub": str(USER), "act_org": str(ORG), "mem": str(MEMBER)}
        )
        assert principal.membership_id == MEMBER
        assert principal.subject_ref is None

    def test_forwarded_x_membership_id_and_x_subject_ref_fill_the_same_fields(self) -> None:
        cfg = _Settings()
        request = _forwarded(cfg, {"X-Membership-Id": str(MEMBER), "X-Subject-Ref": str(EMPLOYEE)})
        principal = get_principal(request)
        assert principal.membership_id == MEMBER
        assert principal.subject_ref == EMPLOYEE
        assert principal.organization_id == ORG

    def test_a_forwarded_identity_with_neither_header_has_neither_id(self) -> None:
        cfg = _Settings()
        principal = _principal_from_gateway(_forwarded(cfg), cfg)
        assert principal is not None
        assert principal.membership_id is None
        assert principal.subject_ref is None

    def test_a_malformed_x_membership_id_is_a_401_naming_the_header(self) -> None:
        cfg = _Settings()
        request = _forwarded(cfg, {"X-Membership-Id": "not-a-uuid"})
        with pytest.raises(UnauthorizedProblem, match="Malformed X-Membership-Id") as refused:
            _principal_from_gateway(request, cfg)
        assert refused.value.status_code == 401

    def test_a_malformed_x_subject_ref_is_a_401_naming_the_header(self) -> None:
        cfg = _Settings()
        request = _forwarded(cfg, {"X-Subject-Ref": "not-a-uuid"})
        with pytest.raises(UnauthorizedProblem, match="Malformed X-Subject-Ref"):
            _principal_from_gateway(request, cfg)

    def test_the_gateway_strips_both_ids_from_an_inbound_request(self) -> None:
        # A caller must not be able to assert either id; only the gateway, from a verified token,
        # may set them. Membership becomes an authorization input for CRM in R2.7.
        assert {"x-membership-id", "x-subject-ref"} <= SPOOFABLE
