"""The contact-resolution consumer — folding a CRM lookup onto a conversation, idempotently.

`fold_inbound_contact` runs on `services.EVT_INBOUND_RECEIVED`, the event the webhook fold stages
once a customer's message is durably recorded. The interesting behaviour is the same shape as
every other consumer in this service: it completes exactly once even redelivered, and — the
degrade path unique to this one — it completes even when the CRM cannot answer at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select

from app import consumers, services
from app.config import settings
from app.models import Conversation
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


async def _conversation(session, org_id: uuid.UUID) -> Conversation:
    enc, masked = seal("+16505551234", kind="phone", settings=settings())
    conversation = Conversation(
        organization_id=org_id,
        channel="whatsapp",
        wa_address_enc=enc,
        wa_address_masked=masked,
        wa_address_index="index-does-not-matter-here",
        status="open",
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


async def test_a_matched_contact_is_folded_onto_the_conversation(
    session, org_id, worker_engine
) -> None:
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> str | None:
        return CONTACT_ID

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), resolver)

    async with session.begin():
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation.id))
        ).scalar_one()
    assert str(row.contact_id) == CONTACT_ID


async def test_no_resolver_leaves_the_conversation_unknown(session, org_id, worker_engine) -> None:
    """Wired without a resolver at all (should never happen outside a test, but the same
    principle as `services._recipient_for`'s guard for `resolve_target`): fails safe rather than
    crashing the consumer."""
    conversation = await _conversation(session, org_id)

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), None)

    async with session.begin():
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation.id))
        ).scalar_one()
    assert row.contact_id is None


async def test_a_crm_that_cannot_answer_degrades_to_unknown_rather_than_failing(
    session, org_id, worker_engine
) -> None:
    """The behaviour `app/contacts.py` exists for: no exception, no redelivery, just an
    unresolved conversation a later run can still enrich."""
    conversation = await _conversation(session, org_id)

    async def resolver(organization_id: str, phone: str) -> str | None:
        return None  # what `contacts.resolve_contact_by_phone` returns on any failure

    await consumers.fold_inbound_contact(_envelope(org_id, conversation.id), resolver)

    async with session.begin():
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation.id))
        ).scalar_one()
    assert row.contact_id is None


async def test_a_conversation_that_no_longer_exists_is_skipped_not_an_error(
    session, org_id, worker_engine
) -> None:
    async def resolver(organization_id: str, phone: str) -> str | None:
        raise AssertionError("must not be called: there is no conversation to open a phone for")

    # Must not raise.
    await consumers.fold_inbound_contact(_envelope(org_id, uuid.uuid4()), resolver)


async def test_redelivery_resolves_the_contact_only_once(session, org_id, worker_engine) -> None:
    """Idempotent on the event, not just on the outcome: a second resolver call would be a
    second CRM lookup for work already done."""
    conversation = await _conversation(session, org_id)
    calls = 0

    async def resolver(organization_id: str, phone: str) -> str | None:
        nonlocal calls
        calls += 1
        return CONTACT_ID

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
        await services.resolve_conversation_contact(
            session,
            organization_id=org_id,
            conversation_id=conversation.id,
            contact_id=CONTACT_ID,
        )
    async with session.begin():
        await services.resolve_conversation_contact(
            session,
            organization_id=org_id,
            conversation_id=conversation.id,
            contact_id=CONTACT_ID,
        )
    async with session.begin():
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation.id))
        ).scalar_one()
    assert str(row.contact_id) == CONTACT_ID
