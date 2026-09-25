"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

The operator tokens are forged directly, the way the gateway and catalog tests forge theirs: the
real `stf` staff-token minter lives in auth-service and the gateway is what gates on it (and does
not forward the flag downstream). What this service sees, and all it can check as defence in depth,
is the audience the operator gateway stamps — so these principals carry it (mirrors catalog's
`operator_principal`).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from app.main import app
from platform_core.auth import Principal, get_principal

#: What the product gateway stamps on a customer's token.
APP_AUDIENCE = "app"
#: What the operator gateway stamps. `require_operator` accepts only this.
OPERATOR_AUDIENCE = "operator"


def operator_principal(
    *, staff: bool = True, roles: list[str] | None = None, amr: list[str] | None = None
) -> Principal:
    """A staff operator as the operator gateway would have forwarded them.

    `audience="operator"` is the marker `require_operator` checks. `staff=False` swaps the audience
    to a product token, producing the negative case a staff route must refuse with 404 — the
    in-service mirror of the gateway's own staff gate. `roles` defaults to `["owner"]`, which holds
    every route's role requirement (ADR-0003 addendum): existing tests predate the per-route role
    gate and keep exercising the case they were written for unless a test asks for less.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        audience=OPERATOR_AUDIENCE if staff else APP_AUDIENCE,
        roles=roles if roles is not None else ["owner"],
        # Every real operator token carries `otp` once sign-in completes (MFA is mandatory for
        # staff, ADR-0003 addendum); defaulted on here so existing role/audience tests don't also
        # have to spell out the amr axis.
        amr=amr if amr is not None else ["pwd", "otp"],
    )


def envelope(
    event_type: str,
    *,
    organization_id: uuid.UUID,
    payload: dict[str, Any],
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """A domain-event envelope in the wire shape `build_envelope` produces (ADR-0000 §7)."""
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "producer": "tests",
        "payload": payload,
    }


def subscription_payload(
    organization_id: uuid.UUID,
    *,
    product_key: str,
    state: str,
    plan_version_id: str | None = None,
    trial_ends_at: str | None = None,
) -> dict[str, Any]:
    """A `commerce.subscription.*` payload, as subscription-service documents it."""
    return {
        "organization_id": str(organization_id),
        "product_key": product_key,
        "plan_version_id": plan_version_id,
        "state": state,
        "trial_ends_at": trial_ends_at,
    }


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    """Override the resolved caller for one block — how the audience boundary is exercised."""
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class Downstream:
    """A mock transport that answers by URL path and records every request it saw.

    `routes` maps a path **suffix** to either a `httpx.Response` or a callable taking the request
    and returning one; the first suffix a request's path ends with wins, and a path nothing
    matches is a 503 — the same "downstream unavailable" default the `client` fixture installs.
    Installed on the app with `install()`; `calls` is what a test asserts the delegated hops on.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[Any] = []

    def _handle(self, request: Any) -> Any:
        import httpx

        self.calls.append(request)
        for suffix, answer in self.routes.items():
            if request.url.path.endswith(suffix):
                return answer(request) if callable(answer) else answer
        return httpx.Response(503)

    def install(self) -> None:
        from collections.abc import AsyncIterator

        import httpx

        from app.api.routes import get_delegation_client

        downstream = self

        async def _override() -> AsyncIterator[httpx.AsyncClient]:
            transport = httpx.MockTransport(downstream._handle)
            async with httpx.AsyncClient(transport=transport) as mock_client:
                yield mock_client

        app.dependency_overrides[get_delegation_client] = _override


def make_fake_resolver(prices: dict[str, tuple[int, str]]):
    """A price resolver keyed by plan_version_id, so the fold needs no catalog or broker."""

    async def _resolve(plan_version_id: str) -> tuple[int, str | None]:
        amount, currency = prices.get(plan_version_id, (0, None))
        return amount, currency

    return _resolve
