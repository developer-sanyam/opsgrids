"""Every transaction carries its organization, and no route can avoid the chokepoint.

Two different claims are asserted here:

1. `get_tenant_session` really does set the GUC, inside the transaction, and the value really
   is transaction-local. A runtime fact.
2. **No route can skip it.** A static fact about the route table, and the one that stays true
   as routes are added — a route on a bare `get_session` would read and write with no
   organization bound, a cross-tenant leak under RLS.
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
from platform_core import db
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC, get_tenant_session
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

ROUTES = Path(__file__).resolve().parent.parent / "app" / "api" / "routes.py"


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
        self, session: AsyncSession, org_id, people
    ) -> None:
        principal = principal_for(org_id, people["manager"], [])

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
        self, session: AsyncSession, org_id, people
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
                principal = principal_for(org_id, people["manager"], [])
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


class TestTheProbeIsWired:
    """The ADR-0002 §4 probe is wired into the request path, not merely present in db.py.

    The binding is made *wrong* rather than skipped, because the shared test session re-binds
    the tenant on every `begin()` of its own accord — so a skipped `set_tenant` would leave the
    fixture's correct binding in place and prove nothing.
    """

    async def test_a_misbound_transaction_cannot_serve_a_request_quietly(
        self, client: AsyncClient, org_id, people, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _misbind(session: AsyncSession, organization_id: uuid.UUID) -> None:
            await session.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(uuid.uuid4())},
            )

        monkeypatch.setattr(db, "set_tenant", _misbind)
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.read:ORG"])
        with _as(principal), pytest.raises(RuntimeError, match=TENANT_GUC):
            await client.get("/api/v1/employees")
