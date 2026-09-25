"""Engage scheduled sends (ADR-0015) — schedule, reschedule, cancel, and the firing.

A scheduled send is `platform.schedule.requested` staged on this service's own outbox; the
scheduler service holds it and, at `due_at`, republishes the target event. The scheduler is a
separate process with its own `app` package, so these tests stand in for its firing the way
finance's do: by handing `consumers.expand_campaign_send` the envelope the scheduler would have
published, built from the very payload `schedule_send` staged. What is asserted here is this
service's half of the contract — nothing is sent, or even expanded, before that firing, and the
firing that matches the campaign's current schedule sends it exactly once.

Real PostgreSQL as the app role, `RecordingTransport` for the sends, and the helpers of
`test_engage_campaigns`, whose send driver harness (`_drain`) is the one production's runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app import consumers, models, services
from app.config import settings
from app.transports import RecordingTransport
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for, envelope_for
from tests.test_engage_campaigns import (
    CAMPAIGNS,
    K_SEND,
    K_WRITE,
    _campaign,
    _contacts,
    _drain,
    _recipients,
    _reload,
    _seed_identity,
    as_principal,
    caller,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

ALL = [f"{p}:ORG" for p in (K_WRITE, K_SEND, "engage.audience.contact.write")]
SCHEDULE_REQUESTED = "platform.schedule.requested"


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    """The engine `consumers` uses, as the worker process would have it. Depends on `session`
    so the tables are truncated before the consumer sees them."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _schedule(session, org_id, campaign_id, at: datetime) -> None:
    async with session.begin():
        await services.schedule_send(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id, scheduled_at=at
        )


async def _staged(session, event_type: str) -> list[models.OutboxEvent]:
    async with session.begin():
        rows = (
            await session.execute(
                select(models.OutboxEvent)
                .where(models.OutboxEvent.event_type == event_type)
                .order_by(models.OutboxEvent.created_at)
            )
        ).scalars()
        return list(rows)


def _firing(org_id, request: models.OutboxEvent) -> dict[str, Any]:
    """The envelope the scheduler would publish when this request's `due_at` arrives."""
    target = request.payload["target"]
    return envelope_for(org_id, target["event_type"], target["payload"])


async def test_a_scheduled_campaign_is_not_sent_before_its_time_and_is_sent_once_after(
    session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    transport = RecordingTransport()
    first = datetime.now(UTC) + timedelta(hours=1)
    second = first + timedelta(hours=2)

    # Scheduled through the same gate as an immediate send: held, not sent, nothing published.
    await _schedule(session, org_id, campaign_id, first)
    held = await _reload(session, campaign_id)
    assert held.status == "scheduled" and held.started_at is None
    assert held.scheduled_at == first
    assert await _staged(session, services.EVT_CAMPAIGN_SEND_REQUESTED) == []
    (requested,) = await _staged(session, SCHEDULE_REQUESTED)
    assert requested.payload["due_at"] == first.astimezone(UTC).isoformat()
    assert requested.payload["target"]["event_type"] == services.EVT_CAMPAIGN_SEND_REQUESTED
    # What the scheduler will publish must satisfy the committed contract, `scheduled_for` and all.
    validate_event_schema(
        services.EVT_CAMPAIGN_SEND_REQUESTED,
        1,
        requested.payload["target"]["payload"],
        contracts_root=CONTRACTS_ROOT,
    )
    # The send driver leaves a campaign that is not `sending` alone: nothing to claim, nothing sent.
    assert await _drain(session, org_id, campaign_id, transport=transport) == "stopped"
    assert transport.sent == [] and await _recipients(session, campaign_id) == {}

    # Rescheduling asks for a new instant; the first request is still out there in the scheduler.
    await _schedule(session, org_id, campaign_id, second)
    _, replacement = await _staged(session, SCHEDULE_REQUESTED)
    assert replacement.payload["idempotency_key"] != requested.payload["idempotency_key"]
    assert await consumers.expand_campaign_send(_firing(org_id, requested)) == "not_sending"
    assert (await _reload(session, campaign_id)).status == "scheduled"
    assert await _recipients(session, campaign_id) == {}

    # The current schedule fires: expanded once, sent once, however often it is redelivered.
    # `now=second` stands in for the scheduler actually waiting until `second` arrives — this
    # test does not sleep two hours to get there.
    firing = _firing(org_id, replacement)
    assert await consumers.expand_campaign_send(firing, now=second) == "expanded"
    assert await consumers.expand_campaign_send(firing, now=second) == "redelivered"
    # The consumer committed on its own connection; refresh this session's cached row (the send
    # driver reads through a session of its own per pass, so production never sees it stale).
    started = await _reload(session, campaign_id)
    assert started.status == "sending" and started.started_at is not None
    assert await _drain(session, org_id, campaign_id, transport=transport) == "done"
    done = await _reload(session, campaign_id)
    assert done.status == "sent" and done.expanded_at is not None and done.sent_count == 2
    assert len(transport.sent) == 2
    # The same request firing again under a fresh event id finds nothing left to send.
    assert await consumers.expand_campaign_send(_firing(org_id, replacement)) == "not_sending"
    assert len(transport.sent) == 2


async def test_cancelling_a_scheduled_campaign_stops_its_firing(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _schedule(session, org_id, campaign_id, datetime.now(UTC) + timedelta(hours=1))
    (requested,) = await _staged(session, SCHEDULE_REQUESTED)

    async with session.begin():
        await services.cancel_campaign(
            session, ctx_for(org_id, K_WRITE, Scope.ORG), campaign_id=campaign_id
        )
    assert await consumers.expand_campaign_send(_firing(org_id, requested)) == "not_sending"
    assert (await _reload(session, campaign_id)).status == "cancelled"
    assert await _recipients(session, campaign_id) == {}


async def test_a_campaign_that_stops_being_sendable_before_it_fires_is_paused_not_sent(
    session, org_id, worker_engine
):
    """The gate that let this campaign be scheduled is re-run the instant it actually fires
    (`services.activate_scheduled_campaign`). A route that broke in between — here, the
    provider disconnecting — must not be allowed to send anyway."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    at = datetime.now(UTC) + timedelta(hours=1)
    await _schedule(session, org_id, campaign_id, at)
    (requested,) = await _staged(session, SCHEDULE_REQUESTED)

    async with session.begin():
        await session.execute(
            update(models.EmailConnection)
            .where(models.EmailConnection.organization_id == org_id)
            .values(status="disconnected")
        )

    assert await consumers.expand_campaign_send(_firing(org_id, requested), now=at) == "not_sending"
    failed = await _reload(session, campaign_id)
    assert failed.status == "paused"
    assert "provider" in failed.pause_reason
    assert failed.started_at is None
    assert await _recipients(session, campaign_id) == {}

    async with session.begin():
        (audit,) = (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "engage.campaign.scheduled_send_failed"
                )
            )
        ).scalars()
    assert audit.entity_id == campaign_id
    assert audit.reason and "provider" in audit.reason
    assert audit.actor_id is None and audit.acting_as == services.CAMPAIGN_ACTOR

    # Nothing to send, and resuming without fixing the route is refused the same as any other
    # paused campaign — an operator has to reconnect a provider first.
    transport = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=transport) == "paused"
    assert transport.sent == []


async def test_a_scheduled_campaign_fired_early_is_dropped(session, org_id):
    """Defence in depth (ADR-0015 §5): a firing that arrives well before its own `scheduled_at`
    — a hand redelivery, a replayed dead letter, a scheduler with a bad clock — is dropped
    outright rather than trusted, and the campaign is left alone for its real firing later."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    at = datetime.now(UTC) + timedelta(hours=1)
    await _schedule(session, org_id, campaign_id, at)

    async with session.begin():
        activated = await services.activate_scheduled_campaign(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            scheduled_for=at,
            now=at - timedelta(minutes=5),
        )
    assert activated is False
    untouched = await _reload(session, campaign_id)
    assert untouched.status == "scheduled" and untouched.started_at is None
    assert untouched.pause_reason == ""
    assert await _recipients(session, campaign_id) == {}


async def test_firing_a_scheduled_campaign_twice_sends_once(session, org_id, worker_engine):
    """Two distinct firings of the same schedule — not a broker redelivery of one message, but
    two separate `platform.schedule.requested` deliveries naming the same `scheduled_for` —
    activate at most once (`activate_scheduled_campaign`'s own status guard) and expand into the
    same recipient rows (`ON CONFLICT DO NOTHING`), so the send driver still sends each recipient
    exactly once."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    at = datetime.now(UTC) + timedelta(hours=1)
    await _schedule(session, org_id, campaign_id, at)
    (requested,) = await _staged(session, SCHEDULE_REQUESTED)

    first_firing = _firing(org_id, requested)
    second_firing = _firing(org_id, requested)
    second_firing["event_id"] = str(uuid.uuid4())
    assert first_firing["event_id"] != second_firing["event_id"]

    assert await consumers.expand_campaign_send(first_firing, now=at) == "expanded"
    assert await consumers.expand_campaign_send(second_firing, now=at) == "expanded"
    started = await _reload(session, campaign_id)
    assert started.status == "sending"

    transport = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=transport) == "done"
    assert len(transport.sent) == 2
    done = await _reload(session, campaign_id)
    assert done.sent_count == 2 and done.status == "sent"


async def test_schedule_endpoint_needs_a_future_timezone_aware_time(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    url = f"{CAMPAIGNS}/{campaign_id}/schedule"
    future = datetime.now(UTC) + timedelta(days=1)
    with as_principal(caller(org_id, ALL)):
        past = await client.post(url, json={"scheduled_at": "2001-01-01T00:00:00Z"})
        naive = await client.post(url, json={"scheduled_at": "2099-01-01T00:00:00"})
        extra = await client.post(url, json={"scheduled_at": future.isoformat(), "x": 1})
        ok = await client.post(url, json={"scheduled_at": future.isoformat()})
        again = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
    assert (past.status_code, naive.status_code, extra.status_code) == (422, 422, 422)
    assert ok.status_code == 202 and ok.json()["status"] == "scheduled"
    assert ok.json()["scheduled_at"] is not None
    # A scheduled campaign is not a draft: sending it now is refused rather than double-sent.
    assert again.status_code == 409
