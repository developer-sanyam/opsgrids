"""The `platform.schedule.requested` consumer (ADR-0015): idempotent at the event id, idempotent
at the domain key, tenant-scoped, and never raises on a payload it cannot parse.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.config import settings
from app.consumers import handle_schedule_requested
from app.models import ProcessedEvent, ScheduledJob
from platform_core.db import dispose_engine, init_engine

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


def _envelope(
    organization_id: uuid.UUID,
    *,
    event_id: uuid.UUID | None = None,
    idempotency_key: str = "job-1",
    due_at: datetime | None = None,
    target_aggregate_id: uuid.UUID | None = None,
    target_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "platform.schedule.requested",
        "organization_id": str(organization_id),
        "correlation_id": "corr-1",
        "payload": {
            "idempotency_key": idempotency_key,
            "due_at": (due_at or datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "target": {
                "event_type": "finance.compliance.invoice.filing_requested",
                "aggregate_type": "invoice",
                "aggregate_id": str(target_aggregate_id or uuid.uuid4()),
                "payload": target_payload or {"attempt": 2, "trigger": "retry"},
                "actor": None,
            },
        },
    }


async def _rows(session: Any, organization_id: uuid.UUID) -> list[ScheduledJob]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(ScheduledJob).where(ScheduledJob.organization_id == organization_id)
                )
            )
            .scalars()
            .all()
        )


class TestIngestion:
    async def test_a_schedule_request_becomes_one_pending_row(
        self, session: Any, org_id: uuid.UUID
    ) -> None:
        target_id = uuid.uuid4()
        await handle_schedule_requested(
            _envelope(org_id, target_aggregate_id=target_id, target_payload={"attempt": 2})
        )

        rows = await _rows(session, org_id)
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].target_event_type == "finance.compliance.invoice.filing_requested"
        assert rows[0].target_aggregate_id == target_id
        assert rows[0].target_payload["attempt"] == 2

    async def test_a_malformed_payload_is_dropped_not_raised(
        self, session: Any, org_id: uuid.UUID
    ) -> None:
        envelope = _envelope(org_id)
        del envelope["payload"]["due_at"]

        await handle_schedule_requested(envelope)  # must not raise

        assert await _rows(session, org_id) == []


class TestEventIdIdempotency:
    async def test_redelivering_the_same_event_writes_nothing_twice(
        self, session: Any, org_id: uuid.UUID
    ) -> None:
        envelope = _envelope(org_id)

        await handle_schedule_requested(envelope)
        await handle_schedule_requested(envelope)

        assert len(await _rows(session, org_id)) == 1
        async with session.begin():
            processed = (await session.execute(select(ProcessedEvent))).scalars().all()
        assert len(processed) == 1


class TestDomainIdempotency:
    async def test_two_distinct_events_with_the_same_key_still_produce_one_row(
        self, session: Any, org_id: uuid.UUID
    ) -> None:
        """Not a redelivery — two different event ids, the same `idempotency_key`. This is what
        the unique `(organization_id, idempotency_key)` constraint is for, on top of the event-id
        ledger above: the early-arrival defence in finance's own consumer deliberately produces
        this shape (a fresh event id, the same retry's key) when it is *not* re-arming, and a
        client that computes a key twice for the same reason must not schedule the same delivery
        twice either."""
        await handle_schedule_requested(_envelope(org_id, idempotency_key="job-1"))
        await handle_schedule_requested(_envelope(org_id, idempotency_key="job-1"))

        assert len(await _rows(session, org_id)) == 1

    async def test_the_same_key_in_a_different_organization_is_a_different_row(
        self, session: Any, bind_session: Any, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        await handle_schedule_requested(_envelope(org_id, idempotency_key="job-1"))
        await handle_schedule_requested(_envelope(other_org, idempotency_key="job-1"))

        assert len(await _rows(session, org_id)) == 1
        async with bind_session(other_org) as other_session:
            other_rows = (await other_session.execute(select(ScheduledJob))).scalars().all()
        assert len(other_rows) == 1


class TestTenantIsolation:
    async def test_a_row_is_invisible_from_a_session_bound_to_a_different_organization(
        self, session: Any, bind_session: Any, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        await handle_schedule_requested(_envelope(org_id))

        assert len(await _rows(session, org_id)) == 1

        async with bind_session(other_org) as other_session:
            visible = (await other_session.execute(select(ScheduledJob))).scalars().all()
        assert visible == []
