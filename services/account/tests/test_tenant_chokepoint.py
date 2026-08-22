"""The exit gate for the tenancy prerequisites: every transaction carries its organization.

No row-level security policy exists yet, and that is deliberate — this lands and is proven on
its own, so that when a policy later looks inert nobody has to wonder whether the cause is the
role, the ownership, the tenant setting or the policy itself.

Two properties are asserted, and they are different claims:

1. The chokepoint really does set the GUC, inside the transaction, and the value really is
   transaction-local. That is a runtime fact about `get_tenant_session`.
2. **No route can avoid it.** That is a static fact about the route table, and it is the one
   that stays true as routes are added.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db import TENANT_GUC, get_tenant_session
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

ROUTES = Path(__file__).resolve().parent.parent / "app" / "api" / "routes.py"


class TestTheGucIsSet:
    async def test_the_chokepoint_binds_the_transaction_to_the_organization(
        self, session: AsyncSession, org, people
    ) -> None:
        principal = principal_for(org.id, people["manager"], [])

        agen = get_tenant_session(principal, session)
        bound = await agen.__anext__()
        try:
            got = (
                await bound.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
            ).scalar()
            assert got == str(org.id), (
                f"the chokepoint yielded a transaction bound to {got!r}, not the caller's "
                "organization. Every policy written later reads this value."
            )
        finally:
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()

    async def test_the_binding_does_not_survive_the_transaction(
        self, session: AsyncSession, org, people
    ) -> None:
        """The property that stops a pooled connection leaking one tenant into the next.

        If this ever reads back the previous organization instead of empty, a request that
        forgets the chokepoint would inherit whoever used the connection last — and return
        HTTP 200 with their rows, with no error anywhere. That is the worst failure available
        in this system.
        """
        principal = principal_for(org.id, people["manager"], [])
        agen = get_tenant_session(principal, session)
        await agen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

        after = (
            await session.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
        ).scalar()
        assert after in (None, ""), (
            f"the tenant binding survived its transaction as {after!r}. It must be "
            "transaction-local, or the connection pool carries it into the next request."
        )

    async def test_a_different_organization_gets_a_different_binding(
        self, session: AsyncSession, org, people
    ) -> None:
        """Guards against the GUC being set once and reused — it is per transaction."""
        other = uuid.uuid4()
        principal = principal_for(other, people["manager"], [])
        agen = get_tenant_session(principal, session)
        bound = await agen.__anext__()
        try:
            got = (
                await bound.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
            ).scalar()
            assert got == str(other)
        finally:
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()


class TestNoRouteEscapesTheChokepoint:
    """The half that survives new routes being written.

    A route taking a bare `get_session` would read and write with no organization bound —
    invisible today, and a cross-tenant leak the moment policies land.
    """

    def test_the_route_module_binds_sessions_through_the_chokepoint(self) -> None:
        source = ROUTES.read_text()
        assert "get_tenant_session" in source, "the route module no longer uses the chokepoint"
        assert "Depends(get_session)" not in source, (
            "a route depends on `get_session` directly, so its transaction carries no "
            "organization. Use the tenant session."
        )

    def test_no_route_opens_its_own_transaction(self) -> None:
        """`session.begin()` in a route would collide with the chokepoint's transaction.

        SQLAlchemy raises "a transaction is already begun" — verified, not assumed — so this
        would be caught at runtime too. It is asserted here because the failure would only
        appear on whichever route was touched, and only when it was exercised.
        """
        assert "session.begin()" not in ROUTES.read_text(), (
            "a route opens its own transaction. The chokepoint already owns one."
        )

    def test_every_route_handler_takes_a_session_or_needs_none(self) -> None:
        """Catches a handler that reaches for a session some other way.

        Parsed rather than grepped so a handler is examined by its actual signature.
        """
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
