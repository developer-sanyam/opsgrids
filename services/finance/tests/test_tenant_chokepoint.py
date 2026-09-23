"""Every transaction on the public finance surface carries its organization, and no route can
avoid the chokepoint. Selectors that narrow a list by scope call `ctx.apply`, the one enforcement
point every such query goes through.

Mirrors `services/employee/tests/test_tenant_chokepoint.py` and
`services/account/tests/test_tenant_chokepoint.py`. Three different claims are asserted:

1. `get_tenant_session` really does set the GUC, inside the transaction, and the value really is
   transaction-local. A runtime fact.
2. **No route in `app/api/routes.py` can skip it.** A static fact about the route table, and the
   one that stays true as routes are added — a route on a bare `get_session` would read and write
   with no organization bound, a cross-tenant leak under RLS.
3. **Every selector that takes a `ScopeContext` calls `ctx.apply`.** `app/selectors.py`'s own
   docstring states this as the discipline; this is what keeps it true as selectors are added.

`app/api/internal.py` is deliberately out of scope for claim 2 — it is service-to-service, takes
no principal, and binds the transaction to the organization named in its request body instead
(its own docstring explains why, the same idiom `services/payment/app/api/internal.py` uses).
That is a different, already-reviewed chokepoint-equivalent, not an escape from this one.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC, get_tenant_session
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

ROUTES = Path(__file__).resolve().parent.parent / "app" / "api" / "routes.py"
SELECTORS = Path(__file__).resolve().parent.parent / "app" / "selectors.py"

#: Selector functions that legitimately take no `ctx` and are exempt from the `ctx.apply` scan
#: below because they are not scope-filtered list reads. Named here, reviewably, rather than
#: guessed at by the scanner — the same discipline ADR-0002 §3 asks of the RLS table
#: classification: a loop over "every function" gets the shape wrong, so the exemption is a
#: decision, not an inference.
_NOT_SCOPE_FILTERED: frozenset[str] = frozenset(
    {
        # The one cross-tenant read in this service (D3): discovers which organizations have a
        # reportable invoice the automatic filing pipeline has lost track of, so there is no
        # tenant to bind and no scope to narrow while asking. Runs on the outbox role; every
        # write it leads to happens back on the app role under a bound tenant. Same shape as
        # payment's and subscription's own `due_*` discovery selectors.
        "due_filing_reclaims",
        # Aggregate/grouped reports, not scope-narrowed *list* reads: each groups every row this
        # organization owns into a handful of summary rows rather than returning one row per
        # record for a caller to page through, so there is nothing an OWN/TEAM scope would ever
        # narrow — finance's own ceiling is ORG regardless. Both filter explicitly on
        # `ctx.organization_id`, the other half of the discipline this module's docstring states
        # ("and filters organization_id explicitly").
        "get_overview",
        "withholding_summary",
    }
)


def _request() -> Request:
    """Only `request.app.state.settings` is read — to decide whether the probe runs."""
    return Request({"type": "http", "app": app})


@contextmanager
def _as(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestTheGucIsSet:
    async def test_the_chokepoint_binds_the_transaction_to_the_organization(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, ["finance.billing.invoice.read:ORG"])

        agen = get_tenant_session(principal, session, _request())
        bound = await agen.__anext__()
        try:
            got = (
                await bound.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
            ).scalar()
            assert got == str(org_id)
        finally:
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()

    async def test_the_binding_does_not_survive_the_transaction(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The property that stops a pooled connection leaking one tenant into the next."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models import SCHEMA

        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as fresh:
                principal = principal_for(org_id, ["finance.billing.invoice.read:ORG"])
                agen = get_tenant_session(principal, fresh, _request())
                await agen.__anext__()
                with pytest.raises(StopAsyncIteration):
                    await agen.__anext__()

                after = (
                    await fresh.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
                ).scalar()
                assert after in (None, ""), (
                    f"the tenant binding survived its transaction as {after!r}. It must be "
                    "transaction-local, or the pool carries it into the next request."
                )
        finally:
            await engine.dispose()


class TestNoRouteEscapesTheChokepoint:
    def test_the_route_module_binds_sessions_through_the_chokepoint(self) -> None:
        source = ROUTES.read_text()
        assert "get_tenant_session" in source, "the route module no longer uses the chokepoint"
        assert "Depends(get_session)" not in source, (
            "a route depends on `get_session` directly, so its transaction carries no "
            "organization. Use the tenant session."
        )

    def test_no_route_opens_its_own_transaction(self) -> None:
        assert "session.begin()" not in ROUTES.read_text(), (
            "a route opens its own transaction. The chokepoint already owns one."
        )

    def test_every_route_handler_takes_a_session_or_needs_none(self) -> None:
        """A handler that reaches for a session some other way is caught by its signature."""
        tree = ast.parse(ROUTES.read_text())
        offenders: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in {"get", "post", "put", "patch", "delete"}
                for d in node.decorator_list
            ):
                continue
            annotations = {
                arg.arg: ast.unparse(arg.annotation) if arg.annotation else ""
                for arg in node.args.args + node.args.kwonlyargs
            }
            session_args = [a for a, ann in annotations.items() if ann == "SessionDep"]
            if "session" in annotations and not session_args:
                offenders.append(node.name)

        assert not offenders, (
            f"{offenders} take a session that is not the tenant-bound one. Every database "
            "access in a tenant-scoped route must carry an organization."
        )


class TestSelectorsApplyScope:
    """`app/selectors.py`'s own docstring: every scoped query calls `ctx.apply(...)`. This is
    what keeps that true as selectors are added rather than merely asserted in a comment."""

    def test_every_ctx_taking_selector_calls_ctx_apply(self) -> None:
        """A function passes either by calling `ctx.apply(...)` itself, or by **delegating**:
        passing `ctx` on to another function defined in this same module (`list_ims_documents`
        building its page from `ims_documents_stmt(ctx, ...)`, which does call it) — one level of
        indirection is the shape every such wrapper in this file already takes, so the scan
        follows exactly that one hop rather than either being fooled by it or trying to resolve
        arbitrary call graphs.
        """
        tree = ast.parse(SELECTORS.read_text())
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def _calls_ctx_apply_directly(node: ast.AST) -> bool:
            return any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "apply"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "ctx"
                for n in ast.walk(node)
            )

        def _delegates_to_a_scoping_function(node: ast.AST) -> bool:
            for n in ast.walk(node):
                if not isinstance(n, ast.Call):
                    continue
                callee_name = n.func.id if isinstance(n.func, ast.Name) else None
                if callee_name is None or callee_name not in functions:
                    continue
                passes_ctx = any(
                    (isinstance(a, ast.Name) and a.id == "ctx") for a in n.args
                ) or any(kw.arg == "ctx" for kw in n.keywords)
                if passes_ctx and _calls_ctx_apply_directly(functions[callee_name]):
                    return True
            return False

        offenders: list[str] = []
        for name, node in functions.items():
            if name in _NOT_SCOPE_FILTERED:
                continue
            takes_ctx = any(
                arg.arg == "ctx"
                and arg.annotation is not None
                and "ScopeContext" in ast.unparse(arg.annotation)
                for arg in node.args.args + node.args.kwonlyargs
            )
            if not takes_ctx:
                continue
            if _calls_ctx_apply_directly(node) or _delegates_to_a_scoping_function(node):
                continue
            offenders.append(name)

        assert not offenders, (
            f"{offenders} take a `ctx: ScopeContext` but never call `ctx.apply(...)` — a "
            "scope-filtered read that does not narrow by scope is a leak between an OWN "
            "reader and the whole organization's book. Add the call, or add the function to "
            "`_NOT_SCOPE_FILTERED` above with a reason if it genuinely is not a list read."
        )

    def test_the_exemption_list_is_recognised_as_present(self) -> None:
        """Guards the guard: if `due_filing_reclaims` stopped taking no `ctx` (or was renamed),
        the exemption above would silently stop meaning anything."""
        source = SELECTORS.read_text()
        for name in _NOT_SCOPE_FILTERED:
            assert f"def {name}(" in source, (
                f"{name!r} is on the exemption list but no longer exists in selectors.py"
            )


class TestTheProbeIsWired:
    """The ADR-0002 §4 probe is wired into the request path, not merely present in db.py.

    The binding is made *wrong* rather than skipped, because the shared test session re-binds
    the tenant on every `begin()` of its own accord — so a skipped `set_tenant` would leave the
    fixture's correct binding in place and prove nothing.
    """

    async def test_a_misbound_transaction_cannot_serve_a_request_quietly(
        self, client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from platform_core import db as db_module

        async def _misbind(session: AsyncSession, organization_id: uuid.UUID) -> None:
            await session.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(uuid.uuid4())},
            )

        monkeypatch.setattr(db_module, "set_tenant", _misbind)
        principal = principal_for(org_id, ["finance.billing.invoice.read:ORG"])
        with _as(principal), pytest.raises(RuntimeError, match=TENANT_GUC):
            await client.get("/api/v1/finance/invoices")
