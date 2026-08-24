"""The customer usage read and the authorization boundaries around both surfaces.

Three properties carry the weight:

1. **Cross-tenant isolation** — one organization's usage read never returns another's events,
   enforced by row-level security (the read runs as the NOBYPASSRLS app role, bound to the
   caller's organization by the chokepoint).
2. **Customer negative-authz** — the usage read needs the permission and a real organization; a
   token without the permission is refused, an unauthenticated caller is refused.
3. **Internal ingest negative-authz** — usage is recorded server-to-server behind a service
   token addressed to metering; a call with no token, or a token minted for another service, is
   refused.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.domain import current_period
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import customer_principal, service_token_header, usage_event

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- cross-tenant isolation


async def test_the_read_returns_only_the_callers_organization(
    session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
) -> None:
    """Org B's events are recorded, and org A's usage read never sees them — RLS, at the selector.

    The ingest binds each organization's own GUC as it writes, so both tenants' rows exist; the
    read then runs bound to one organization and reads only its own.
    """
    await services.record_usage_batch(
        session, [usage_event(org_id, meter_key="core.api_call", quantity=5)]
    )
    await services.record_usage_batch(
        session, [usage_event(org_b, meter_key="core.api_call", quantity=99)]
    )

    start, end = current_period()
    async with session.begin():
        # `session` is bound to org_id, so this read is org A's.
        rows = await selectors.period_usage(session, org_id, period_start=start, period_end=end)

    totals = {meter: total for meter, _unit, total, _billable, _count in rows}
    assert totals == {"core.api_call": 5}, "org A must see only its own 5, never org B's 99"


async def test_the_http_read_is_scoped_to_the_token_organization(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
) -> None:
    """End to end over HTTP: one event stream, read by two organizations, yields two answers."""
    await services.record_usage_batch(
        session, [usage_event(org_id, meter_key="core.api_call", quantity=5, is_billable=True)]
    )
    await services.record_usage_batch(
        session, [usage_event(org_b, meter_key="core.api_call", quantity=99)]
    )

    with as_principal(customer_principal(org_id)):
        resp_a = await client.get("/api/v1/usage")
    with as_principal(customer_principal(org_b)):
        resp_b = await client.get("/api/v1/usage")

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    a_lines = {line["meter_key"]: line for line in resp_a.json()["lines"]}
    b_lines = {line["meter_key"]: line for line in resp_b.json()["lines"]}
    assert a_lines["core.api_call"]["total_quantity"] == 5
    assert a_lines["core.api_call"]["billable_quantity"] == 5
    assert b_lines["core.api_call"]["total_quantity"] == 99
    assert b_lines["core.api_call"]["billable_quantity"] == 0, "org B's usage is not billable"


# --------------------------------------------------------------------- customer negative-authz


async def test_a_customer_without_the_permission_is_forbidden(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(customer_principal(org_id, perms=[])):
        resp = await client.get("/api/v1/usage")
    assert resp.status_code == 403


async def test_an_unauthenticated_caller_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/usage")
    assert resp.status_code == 401


# ----------------------------------------------------------------- internal ingest negative-authz


async def test_internal_ingest_requires_a_service_token(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    body = {"events": [usage_event(org_id).model_dump(mode="json")]}
    resp = await client.post("/internal/v1/usage", json=body)
    assert resp.status_code == 401


async def test_internal_ingest_rejects_a_token_for_another_service(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    """A service token is addressed to metering by name; one minted for billing is refused."""
    body = {"events": [usage_event(org_id).model_dump(mode="json")]}
    resp = await client.post(
        "/internal/v1/usage",
        json=body,
        headers=service_token_header(audience="billing"),
    )
    assert resp.status_code == 401


async def test_internal_ingest_accepts_a_valid_service_token(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    """The positive case: a token addressed to metering records the batch and reports the count."""
    key = uuid.uuid4().hex
    body = {
        "events": [
            usage_event(org_id, idempotency_key=key, quantity=3).model_dump(mode="json"),
        ]
    }
    resp = await client.post("/internal/v1/usage", json=body, headers=service_token_header())
    assert resp.status_code == 200
    assert resp.json() == {"recorded": 1, "duplicates": 0}

    # And a full re-post of the same batch is idempotent end to end.
    resp2 = await client.post("/internal/v1/usage", json=body, headers=service_token_header())
    assert resp2.status_code == 200
    assert resp2.json() == {"recorded": 0, "duplicates": 1}
