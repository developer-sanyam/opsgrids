"""The dispatch path: claim, record, and the decisions in between.

Exercised at the service layer rather than through `dispatcher.dispatch`, because the three
transactions are the behaviour worth pinning and `session_scope` only wraps them. What the
dispatcher itself adds — reading the envelope and choosing a transport — is covered by
`test_transports.py` and by the e2e journey.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import services
from app.domain import STATE_FAILED, STATE_PENDING, STATE_SENDING, STATE_SENT, STATE_SUPPRESSED
from app.models import Delivery, Message, OutboxEvent
from platform_core.auth import Scope
from tests.conftest import seed_automation
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration

TICKET = "support.desk.ticket.created"


async def _stage_one(session, org_id) -> Message:
    await seed_automation(session, org_id)
    event = envelope_for(
        org_id,
        TICKET,
        {
            "number": "TK-0001",
            "requester_name": "Helena Vogel",
            "requester_email": "h@northwind.example.com",
        },
    )
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=event)
    return staged[0]


async def test_claiming_moves_pending_to_sending_and_counts_the_attempt(session, org_id) -> None:
    message = await _stage_one(session, org_id)
    async with session.begin():
        claimed = await services.claim_for_send(
            session, organization_id=org_id, message_id=message.id
        )
    assert claimed is not None
    assert claimed.state == STATE_SENDING
    assert claimed.attempts == 1


async def test_a_message_cannot_be_claimed_twice(session, org_id) -> None:
    """The second net under `processed_events`: two dispatchers, one copy to the customer."""
    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        again = await services.claim_for_send(
            session, organization_id=org_id, message_id=message.id
        )
    assert again is None


async def test_a_recipient_who_opts_out_after_staging_is_not_sent_to(session, org_id) -> None:
    """Of the two suppression checks, the later one is the one that is true. Honouring a stale
    answer is the failure mode that gets a sender blocked."""
    message = await _stage_one(session, org_id)
    write = ctx_for(org_id, "notification.delivery.preference.write", Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session,
            write,
            recipient="h@northwind.example.com",
            channel="email",
            suppressed=True,
        )

    async with session.begin():
        claimed = await services.claim_for_send(
            session, organization_id=org_id, message_id=message.id
        )

    assert claimed is None
    refreshed = (await session.execute(select(Message))).scalars().one()
    assert refreshed.state == STATE_SUPPRESSED


async def test_recording_a_send_writes_the_attempt_and_the_event(session, org_id) -> None:
    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider="recording",
            provider_message_id="rec-1",
        )

    sent = (await session.execute(select(Message))).scalars().one()
    assert sent.state == STATE_SENT
    assert sent.sent_at is not None
    assert sent.provider_message_id == "rec-1"

    ledger = (await session.execute(select(Delivery))).scalars().all()
    assert [(d.attempt_no, d.outcome) for d in ledger] == [(1, "sent")]

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_SENT)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert "to_address" not in events[0].payload


async def test_a_transient_failure_returns_the_message_to_pending(session, org_id) -> None:
    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider="smtp",
            error="connection reset",
            permanent=False,
            max_attempts=6,
        )

    row = (await session.execute(select(Message))).scalars().one()
    assert row.state == STATE_PENDING
    assert row.attempts == 1
    assert row.last_error == "connection reset"


async def test_a_permanent_refusal_fails_immediately(session, org_id) -> None:
    """Retrying an address the provider will never accept only delays the moment somebody
    looks at it."""
    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider="whatsapp_cloud",
            error="not a WhatsApp user",
            permanent=True,
            max_attempts=6,
        )

    row = (await session.execute(select(Message))).scalars().one()
    assert row.state == STATE_FAILED


async def test_a_transient_failure_stops_retrying_once_attempts_are_spent(session, org_id) -> None:
    """Bounded rather than infinite: a queue that never drains hides the messages that could
    still be sent."""
    message = await _stage_one(session, org_id)
    for _ in range(2):
        async with session.begin():
            await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
        async with session.begin():
            await services.record_failure(
                session,
                organization_id=org_id,
                message_id=message.id,
                provider="smtp",
                error="timeout",
                permanent=False,
                max_attempts=2,
            )

    row = (await session.execute(select(Message))).scalars().one()
    assert row.state == STATE_FAILED
    assert row.attempts == 2

    ledger = (await session.execute(select(Delivery))).scalars().all()
    assert [d.attempt_no for d in ledger] == [1, 2]


async def test_the_delivery_ledger_is_append_only(session, org_id) -> None:
    """ "Why did this customer not get their invoice?" is answered by reading the attempts, and
    an answer that can be edited is not an answer."""
    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider="smtp",
            error="first",
            permanent=False,
            max_attempts=6,
        )
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider="smtp",
            provider_message_id=None,
        )

    ledger = (await session.execute(select(Delivery).order_by(Delivery.attempt_no))).scalars().all()
    # The failure is still there beside the success. Nothing was corrected in place.
    assert [(d.attempt_no, d.outcome) for d in ledger] == [(1, "failed"), (2, "sent")]
