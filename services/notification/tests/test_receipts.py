"""Folding provider receipts onto the messages they concern — against real PostgreSQL.

The interesting behaviour is not the happy path. Receipts arrive out of order, more than once,
for messages we never sent, and for messages we deliberately did not send — and each of those
has a right answer that is not "apply it".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import domain, services
from app.models import Message, OutboxEvent, ProviderAccount

pytestmark = pytest.mark.integration

WAMID = "wamid.SENT1"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


async def _sent_message(session, org_id, *, state: str = domain.STATE_SENT) -> Message:
    """A message already handed to the provider, which is when receipts start arriving."""
    message = Message(
        organization_id=org_id,
        channel="whatsapp",
        category="operational",
        to_address="+917976603446",
        subject=None,
        body="Invoice INV-9 is due.",
        state=state,
        attempts=1,
        provider_message_id=WAMID,
        sent_at=NOW,
    )
    session.add(message)
    await session.flush()
    return message


# --------------------------------------------------------------------- progress


async def test_a_delivery_receipt_advances_the_message(session, org_id) -> None:
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )
    assert folded is not None
    assert folded.state == domain.STATE_DELIVERED
    assert folded.delivered_at == NOW


async def test_a_read_receipt_backfills_delivery(session, org_id) -> None:
    """Meta does not always send both. A message shown as read but never delivered is a gap an
    operator would have to explain."""
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_READ,
            occurred_at=NOW,
        )
    assert folded is not None
    assert folded.state == domain.STATE_READ
    assert folded.read_at == NOW
    assert folded.delivered_at == NOW


async def test_a_failure_records_metas_own_error(session, org_id) -> None:
    """ "Failed" is not an answer an operator can act on; Meta's code and title are."""
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_FAILED,
            occurred_at=NOW,
            error_code="131026",
            error_detail="Message undeliverable",
        )
    assert folded is not None
    assert folded.state == domain.STATE_FAILED
    assert folded.provider_error_code == "131026"
    assert folded.provider_error_detail == "Message undeliverable"


# --------------------------------------------------------------------- out of order


async def test_a_late_delivery_receipt_does_not_walk_the_state_back(session, org_id) -> None:
    """The one that matters. Receipts are reordered by batching and by the network, so a
    `delivered` arriving after a `read` must not report the message as less delivered than it
    is — the inbox would lie."""
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_READ,
            occurred_at=NOW,
        )
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW - timedelta(minutes=5),
        )

    assert folded is None
    row = (await session.execute(select(Message))).scalars().one()
    assert row.state == domain.STATE_READ
    assert row.read_at == NOW


async def test_a_receipt_for_a_failed_message_is_ignored(session, org_id) -> None:
    """`failed` is terminal. A later `delivered` means two messages have been confused, not
    that the failed one arrived."""
    async with session.begin():
        await _sent_message(session, org_id, state=domain.STATE_FAILED)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )
    assert folded is None


async def test_a_receipt_for_a_suppressed_message_is_ignored(session, org_id) -> None:
    """A suppressed message was never sent, so nothing can have delivered it."""
    async with session.begin():
        await _sent_message(session, org_id, state=domain.STATE_SUPPRESSED)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )
    assert folded is None


async def test_a_failure_still_applies_to_a_sent_message(session, org_id) -> None:
    """Failure is outside the ladder: Meta reports a message as sent and then failed, and the
    failure is worth recording whatever came before it."""
    async with session.begin():
        await _sent_message(session, org_id, state=domain.STATE_DELIVERED)
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_FAILED,
            occurred_at=NOW,
            error_code="131047",
        )
    assert folded is not None
    assert folded.state == domain.STATE_FAILED


# --------------------------------------------------------------------- nothing to apply


async def test_a_receipt_for_an_unknown_message_is_not_an_error(session, org_id) -> None:
    """A message sent before this system existed, or from another tool on the same number.
    Recording it against nothing would invent a message."""
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id="wamid.NEVER_SENT",
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )
    assert folded is None


async def test_another_tenants_message_is_untouchable(session, admin_session, org_id) -> None:
    """A receipt is attributed by `phone_number_id`, so a tenant boundary crossed here would
    let one organization's webhook rewrite another's delivery record."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Message(
                organization_id=other,
                channel="whatsapp",
                category="operational",
                to_address="+15550101234",
                subject=None,
                body="Theirs",
                state=domain.STATE_SENT,
                attempts=1,
                provider_message_id=WAMID,
            )
        )

    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )
    assert folded is None

    rows = (await admin_session.execute(select(Message))).scalars().all()
    theirs = [r for r in rows if r.organization_id == other]
    assert theirs and theirs[0].state == domain.STATE_SENT


# --------------------------------------------------------------------- downstream


async def test_progress_publishes_an_event(session, org_id) -> None:
    """So analytics and projections react without polling the messages table."""
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )

    rows = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_DELIVERED)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_the_published_event_never_carries_the_recipient(session, org_id) -> None:
    """A delivery receipt is not a reason to put a customer's phone number into an event
    payload that every consumer can read."""
    async with session.begin():
        await _sent_message(session, org_id)
    async with session.begin():
        await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_DELIVERED,
            occurred_at=NOW,
        )

    row = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_DELIVERED)
            )
        )
        .scalars()
        .one()
    )
    blob = str(row.payload)
    assert "+917976603446" not in blob
    assert "to_address" not in blob


# --------------------------------------------------------------------- routing table


async def test_a_phone_number_resolves_to_one_organization(session, org_id) -> None:
    from app import selectors

    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="102290129340398",
                phone_number_id="106540352242922",
                display_number="+15550783881",
                status="connected",
            )
        )

    found = await selectors.account_for_phone_number(session, "106540352242922")
    assert found is not None
    assert found.organization_id == org_id


async def test_two_tenants_cannot_claim_one_phone_number(session, admin_session, org_id) -> None:
    """Globally unique, deliberately: it is the webhook routing key, and a duplicate would make
    an inbound message unattributable."""
    from sqlalchemy.exc import IntegrityError

    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-a",
                phone_number_id="shared-number",
                display_number="+1",
                status="connected",
            )
        )

    with pytest.raises(IntegrityError):
        async with admin_session.begin():
            admin_session.add(
                ProviderAccount(
                    organization_id=uuid.uuid4(),
                    channel="whatsapp",
                    waba_id="waba-b",
                    phone_number_id="shared-number",
                    display_number="+2",
                    status="connected",
                )
            )
