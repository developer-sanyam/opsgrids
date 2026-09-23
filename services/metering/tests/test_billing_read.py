"""The internal usage read the billing run prices a closed period from.

Three properties:

1. **The window is exact and half-open** — `[period_start, period_end)`. An event at the closing
   instant belongs to the next period, so a period boundary never bills one reading twice.
2. **Only billable usage is billable** — the total and the billable count are reported apart,
   and billing prices the second.
3. **It is billing's read and nobody else's** — no token is 401, a token for another service is
   401, a valid token from any caller but billing is 403, and one organization's read never
   includes another's events.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.schemas import UsageEventIn
from tests.helpers import service_token_header

pytestmark = pytest.mark.integration

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


def _event(
    org_id: uuid.UUID, at: datetime, quantity: int, *, billable: bool = True
) -> UsageEventIn:
    return UsageEventIn(
        organization_id=org_id,
        meter_key="core.api_call",
        quantity=quantity,
        unit="api_call",
        idempotency_key=uuid.uuid4().hex,
        occurred_at=at,
        is_billable=billable,
    )


def _params() -> dict[str, str]:
    return {"period_start": START.isoformat(), "period_end": END.isoformat()}


async def test_the_closed_period_is_counted_half_open(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await services.record_usage_batch(
        session,
        [
            _event(org_id, START - timedelta(microseconds=1), 1000),  # previous period
            _event(org_id, START, 5),  # the opening instant is in
            _event(org_id, END - timedelta(seconds=1), 7),
            _event(org_id, END, 2000),  # the closing instant is the next period's
            _event(org_id, START + timedelta(days=3), 11, billable=False),
        ],
    )
    r = await client.get(
        f"/internal/v1/organizations/{org_id}/usage",
        params=_params(),
        headers=service_token_header(caller="billing"),
    )
    assert r.status_code == 200, r.text
    [line] = r.json()["meters"]
    assert line["meter_key"] == "core.api_call"
    assert line["billable_quantity"] == 12
    assert line["total_quantity"] == 23


async def test_one_organization_never_reads_anothers_usage(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
) -> None:
    await services.record_usage_batch(session, [_event(org_b, START + timedelta(days=1), 99)])
    r = await client.get(
        f"/internal/v1/organizations/{org_id}/usage",
        params=_params(),
        headers=service_token_header(caller="billing"),
    )
    assert r.status_code == 200
    assert r.json()["meters"] == []


async def test_no_token_is_401(client: AsyncClient, org_id: uuid.UUID) -> None:
    r = await client.get(f"/internal/v1/organizations/{org_id}/usage", params=_params())
    assert r.status_code == 401


async def test_a_token_for_another_service_is_401(client: AsyncClient, org_id: uuid.UUID) -> None:
    r = await client.get(
        f"/internal/v1/organizations/{org_id}/usage",
        params=_params(),
        headers=service_token_header(caller="billing", audience="billing"),
    )
    assert r.status_code == 401


async def test_a_caller_other_than_billing_is_403(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    """A product service may record usage; it may not read an organization's usage back."""
    await services.record_usage_batch(session, [_event(org_id, START + timedelta(days=1), 3)])
    r = await client.get(
        f"/internal/v1/organizations/{org_id}/usage",
        params=_params(),
        headers=service_token_header(caller="product"),
    )
    assert r.status_code == 403


async def test_a_backwards_window_is_refused(client: AsyncClient, org_id: uuid.UUID) -> None:
    r = await client.get(
        f"/internal/v1/organizations/{org_id}/usage",
        params={"period_start": END.isoformat(), "period_end": START.isoformat()},
        headers=service_token_header(caller="billing"),
    )
    assert r.status_code == 422
