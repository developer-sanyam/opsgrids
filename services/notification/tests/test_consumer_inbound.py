"""The contact-resolution consumer — folding a CRM lookup onto a conversation, idempotently.

`fold_inbound_contact` runs on `services.EVT_INBOUND_RECEIVED`, the event the webhook fold stages
once a customer's message is durably recorded. The interesting behaviour is the same shape as
every other consumer in this service: it completes exactly once even redelivered — but only on a
**definite** answer. A CRM that could not be asked at all must leave the conversation exactly as
it found it and mark nothing processed, which is the bug this file used to let through: an
earlier version collapsed "no match" and "could not ask" into the same `None` and happily wrote
over a contact id a previous, successful run had already resolved.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select

from app import consumers, services
from app.config import settings
from app.contacts import ContactResolution
from app.models import Conversation, ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from platform_core.pii import seal
from tests.helpers import envelope_for

CONTACT_ID = str(uuid.UUID(int=0xC0FFEE))


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    """The engine `session_scope` uses, as the consumer process would have it. Depends on
    `session` so the tables are truncated before the consumer sees them."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _conversation(
    session, org_id: uuid.UUID, *, contact_id: uuid.UUID | None = None
) -> Conversation:
    enc, masked = seal("+16505551234", kind="phone", settings=settings())
    conversation = Conversation(
        organization_id=org_id,
        channel="whatsapp",
        wa_address_enc=enc,
        wa_address_masked=masked,
        wa_address_index="index-does-not-matter-here",
        status="open",
        contact_id=contact_id,
    )
    async with session.begin():
        session.add(conversation)
        await session.flush()
    return conversation


def _envelope(org_id: uuid.UUID, conversation_id: uuid.UUID, **kw: object) -> dict:
    return envelope_for(
        org_id,
        services.EVT_INBOUND_RECEIVED,
        {
            "organization_id": str(org_id),
            "conversation_id": str(conversation_id),
            "inbound_message_id": str(uuid.uuid4()),
        },
        **kw,
    )


async def _contact_id(session, conversation_id: uuid.UUID) -> uuid.UUID | None:
    async with session.begin():
        # The write under test lands through the consumer's own session (`session_scope`, a
        # separate connection) — not this test's `session`, which already holds the row in its
        # identity map from `_conversation()`. With `expire_on_commit=False`, a plain re-`select`
        # would happily hand back the same, now-stale Python object instead of what the consumer
        # just committed; `expire_all()` is what makes this actually re-read the row.
        session.expire_all()
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation_id))
        ).scalar_one()
        return row.contact_id


async def test_a_matched_contact_is_folded_onto_the_conversation(
    session, org_id, worker_engine
) -> None:
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        return ContactResolution(answered=True, contact_id=CONTACT_ID)

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), resolver)

    assert str(await _contact_id(session, conversation.id)) == CONTACT_ID


async def test_no_resolver_leaves_the_conversation_unknown(session, org_id, worker_engine) -> None:
    """Wired without a resolver at all (should never happen outside a test, but the same
    principle as `services._recipient_for`'s guard for `resolve_target`): fails safe rather than
    crashing the consumer."""
    conversation = await _conversation(session, org_id)

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), None)

    assert await _contact_id(session, conversation.id) is None


async def test_a_crm_that_cannot_answer_leaves_the_conversation_untouched(
    session, org_id, worker_engine
) -> None:
    """The behaviour `app/contacts.py` exists for: no exception, no redelivery loop, just an
    unresolved conversation a later run can still enrich."""
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        return ContactResolution(answered=False)

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), resolver)

    assert await _contact_id(session, conversation.id) is None


async def test_a_stranger_is_a_definite_no_match_and_is_written(
    session, org_id, worker_engine
) -> None:
    """The other half of the tri-state: `answered=True, contact_id=None` **is** a fact worth
    marking processed, unlike an unanswered lookup — asking again would waste a call on a
    question the CRM already settled."""
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        return ContactResolution(answered=True, contact_id=None)

    event_id = uuid.uuid4()
    fold_id = uuid.uuid5(event_id, "contact_resolve")
    await consumers.fold_inbound_contact(
        _envelope(org_id, conversation.id, event_id=event_id), resolver
    )

    async with session.begin():
        assert (await session.get(ProcessedEvent, fold_id)) is not None


async def test_a_crm_that_cannot_answer_does_not_erase_an_earlier_match(
    session, org_id, worker_engine
) -> None:
    """The bug this tri-state exists to close: a transient CRM outage on a *later* redelivery
    must never overwrite a contact id an earlier, successful run already wrote."""
    conversation = await _conversation(session, org_id, contact_id=uuid.UUID(CONTACT_ID))

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        return ContactResolution(answered=False)

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), resolver)

    assert str(await _contact_id(session, conversation.id)) == CONTACT_ID


async def test_an_unanswered_lookup_is_not_marked_processed(session, org_id, worker_engine) -> None:
    """So a later redelivery, or a manual replay, gets a real second attempt rather than a
    permanent "already handled" for a question that was never actually answered."""
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        return ContactResolution(answered=False)

    event_id = uuid.uuid4()
    fold_id = uuid.uuid5(event_id, "contact_resolve")
    await consumers.fold_inbound_contact(
        _envelope(org_id, conversation.id, event_id=event_id), resolver
    )

    async with session.begin():
        assert (await session.get(ProcessedEvent, fold_id)) is None


async def test_a_conversation_that_no_longer_exists_is_skipped_not_an_error(
    session, org_id, worker_engine
) -> None:
    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        raise AssertionError("must not be called: there is no conversation to open a phone for")

    # Must not raise.
    await consumers.fold_inbound_contact(_envelope(org_id, uuid.uuid4()), resolver)


async def test_an_envelope_payload_organization_mismatch_is_refused(
    session, org_id, worker_engine
) -> None:
    """The envelope is the trusted tenant binding; a payload claiming a different organization is
    a sign something upstream is broken, refused loudly rather than silently trusted."""
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        raise AssertionError("must not be called: the envelope and payload orgs disagree")

    envelope = _envelope(org_id, conversation.id)
    envelope["payload"]["organization_id"] = str(uuid.uuid4())

    await consumers.fold_inbound_contact(envelope, resolver)

    assert await _contact_id(session, conversation.id) is None


async def test_redelivery_resolves_the_contact_only_once(session, org_id, worker_engine) -> None:
    """Idempotent on the event, not just on the outcome: a second resolver call would be a
    second CRM lookup for work already done."""
    conversation = await _conversation(session, org_id)
    calls = 0

    async def resolver(organization_id: str, phone: str) -> ContactResolution:
        nonlocal calls
        calls += 1
        return ContactResolution(answered=True, contact_id=CONTACT_ID)

    event_id = uuid.uuid4()
    envelope = _envelope(org_id, conversation.id, event_id=event_id)

    await consumers.fold_inbound_contact(envelope, resolver)
    await consumers.fold_inbound_contact(envelope, resolver)

    assert calls == 1


async def test_setting_the_same_contact_twice_is_a_no_op(session, org_id) -> None:
    """`services.resolve_conversation_contact` alone, without the consumer's idempotency ledger
    around it — the second call must still change nothing, because a redelivery that somehow got
    past `processed_events` must not be distinguishable from one that did not happen twice."""
    conversation = await _conversation(session, org_id)
    async with session.begin():
        wrote_first = await services.resolve_conversation_contact(
            session,
            organization_id=org_id,
            conversation_id=conversation.id,
            contact_id=CONTACT_ID,
        )
    async with session.begin():
        wrote_second = await services.resolve_conversation_contact(
            session,
            organization_id=org_id,
            conversation_id=conversation.id,
            contact_id=CONTACT_ID,
        )
    assert wrote_first is True
    assert wrote_second is False
    assert str(await _contact_id(session, conversation.id)) == CONTACT_ID


async def test_an_unparseable_contact_id_is_refused_not_crashed(session, org_id) -> None:
    """customer-service answering with something that is not a UUID is its own bug, not this
    conversation's — treated as no usable answer rather than crashing the fold or writing
    garbage into a UUID column."""
    conversation = await _conversation(session, org_id, contact_id=uuid.UUID(CONTACT_ID))
    async with session.begin():
        wrote = await services.resolve_conversation_contact(
            session,
            organization_id=org_id,
            conversation_id=conversation.id,
            contact_id="not-a-uuid",
        )
    assert wrote is False
    assert str(await _contact_id(session, conversation.id)) == CONTACT_ID
