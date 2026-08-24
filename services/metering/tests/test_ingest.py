"""The load-bearing behaviours of the usage sink, against real PostgreSQL.

Four properties carry the weight, and each is a guarantee a downstream (billing) will rely on:

1. **Idempotent ingest** — posting the same `idempotency_key` twice is exactly one row and one
   event, including a duplicate inside a single batch. A product service must be able to retry.
2. **Append-only at the database** — UPDATE and DELETE on `usage_events` are refused for the app
   role by a table privilege, so no code path can rewrite or erase a recorded reading.
3. **`is_billable` defaults to false** — recording is lossless and free; billing is a later,
   separate decision made in the catalogue, never by the emitter (ADR-0004 §7).
4. **Every recorded event stages `commerce.usage.recorded`** on the outbox, in the same
   transaction as the insert.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.models import OutboxEvent, UsageEvent
from tests.helpers import usage_event

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- idempotency


async def test_the_same_key_posted_twice_is_one_row(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    key = uuid.uuid4().hex

    first = await services.record_usage_batch(session, [usage_event(org_id, idempotency_key=key)])
    assert (first.recorded, first.duplicates) == (1, 0)

    # A retry with the same key — even carrying a different quantity — records nothing new.
    second = await services.record_usage_batch(
        session, [usage_event(org_id, idempotency_key=key, quantity=999)]
    )
    assert (second.recorded, second.duplicates) == (0, 1)

    async with session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.idempotency_key == key)
            )
        ).scalar_one()
        stored = (
            await session.execute(
                select(UsageEvent.quantity).where(UsageEvent.idempotency_key == key)
            )
        ).scalar_one()
    assert count == 1
    assert stored == 1, "the first write wins; the retry does not overwrite it"


async def test_a_duplicate_inside_one_batch_collapses_to_one_row(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    key = uuid.uuid4().hex
    result = await services.record_usage_batch(
        session,
        [
            usage_event(org_id, idempotency_key=key),
            usage_event(org_id, idempotency_key=key),
        ],
    )
    assert (result.recorded, result.duplicates) == (1, 1)

    async with session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.idempotency_key == key)
            )
        ).scalar_one()
    assert count == 1


# --------------------------------------------------------------------- append-only at the DB


async def test_update_on_usage_events_is_denied_to_the_app_role(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """The net under append-only: even a raw UPDATE, bypassing every service check, is refused —
    the app role simply does not hold the privilege."""
    await services.record_usage_batch(session, [usage_event(org_id, idempotency_key="upd")])

    with pytest.raises(ProgrammingError, match="permission denied"):
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE metering.usage_events SET quantity = 999 WHERE idempotency_key = 'upd'"
                )
            )


async def test_delete_on_usage_events_is_denied_to_the_app_role(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    await services.record_usage_batch(session, [usage_event(org_id, idempotency_key="del")])

    with pytest.raises(ProgrammingError, match="permission denied"):
        async with session.begin():
            await session.execute(
                text("DELETE FROM metering.usage_events WHERE idempotency_key = 'del'")
            )


# --------------------------------------------------------------------- is_billable default


async def test_ingested_usage_is_not_billable_by_default(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """Turning billing on is catalogue configuration, not a metering change — so a plainly
    recorded event is non-billable."""
    await services.record_usage_batch(session, [usage_event(org_id, idempotency_key="bill")])

    async with session.begin():
        is_billable = (
            await session.execute(
                select(UsageEvent.is_billable).where(UsageEvent.idempotency_key == "bill")
            )
        ).scalar_one()
    assert is_billable is False


async def test_the_database_default_for_is_billable_is_false(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    """A raw insert that omits the column lands non-billable — the guarantee holds even below the
    application, so no future write path can make usage billable by forgetting a field."""
    key = uuid.uuid4().hex
    async with admin_session.begin():
        await admin_session.execute(
            text(
                "INSERT INTO metering.usage_events "
                "(id, organization_id, meter_key, quantity, unit, idempotency_key) "
                "VALUES (gen_random_uuid(), CAST(:org AS uuid), 'core.api_call', 1, 'api_call', :k)"
            ),
            {"org": str(org_id), "k": key},
        )
        stored = (
            await admin_session.execute(
                text("SELECT is_billable FROM metering.usage_events WHERE idempotency_key = :k"),
                {"k": key},
            )
        ).scalar_one()
    assert stored is False


# --------------------------------------------------------------------- events


async def test_recording_stages_a_usage_recorded_event(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    await services.record_usage_batch(
        session, [usage_event(org_id, meter_key="core.api_call", idempotency_key="evt")]
    )

    async with session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "commerce.usage.recorded")
            )
        ).scalar_one()
        payload = (
            await session.execute(
                select(OutboxEvent.payload).where(
                    OutboxEvent.event_type == "commerce.usage.recorded"
                )
            )
        ).scalar_one()
    assert count == 1
    assert payload["meter_key"] == "core.api_call"
    assert payload["is_billable"] is False


async def test_a_duplicate_stages_no_second_event(session: AsyncSession, org_id: uuid.UUID) -> None:
    """A retry is a no-op for the event stream too — billing must not see the same usage twice."""
    key = uuid.uuid4().hex
    await services.record_usage_batch(session, [usage_event(org_id, idempotency_key=key)])
    await services.record_usage_batch(session, [usage_event(org_id, idempotency_key=key)])

    async with session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "commerce.usage.recorded")
            )
        ).scalar_one()
    assert count == 1
