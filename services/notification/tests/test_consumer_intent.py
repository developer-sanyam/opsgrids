"""`consumers.classify_inbound_intent` -- a free-text opt-out suppresses marketing, once, and only
when it should. The classifier is a stub: what is proven here is what the consumer does with a
verdict, not what the model says (`tests/test_intent.py` and the experiment behind the thresholds
cover that)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import consent, consumers, intent, services
from app.config import settings
from app.models import ConsentEvent, Conversation, InboundMessage, Preference
from platform_core.db import dispose_engine, init_engine
from platform_core.pii import blind_index, seal
from tests.helpers import envelope_for

PHONE = "+16505551234"


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


async def _message(
    session, org_id: uuid.UUID, body: str | None, *, type_: str = "text", phone: str = PHONE
) -> InboundMessage:
    cfg = settings()
    enc, masked = seal(phone, kind="phone", settings=cfg)
    conversation = Conversation(
        organization_id=org_id,
        channel="whatsapp",
        wa_address_enc=enc,
        wa_address_masked=masked,
        wa_address_index=blind_index(phone, kind="phone", settings=cfg),
        status="open",
    )
    async with session.begin():
        session.add(conversation)
        await session.flush()
        body_enc = seal(body, kind="generic", settings=cfg)[0] if body else None
        raw_enc, _ = seal("{}", kind="generic", settings=cfg)
        row = InboundMessage(
            organization_id=org_id,
            conversation_id=conversation.id,
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
            type=type_,
            body_enc=body_enc,
            body_masked="" if body is None else "••••",
            raw_enc=raw_enc,
            occurred_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
    return row


def _envelope(org_id: uuid.UUID, message: InboundMessage, **kw: object) -> dict:
    return envelope_for(
        org_id,
        services.EVT_INBOUND_RECEIVED,
        {
            "organization_id": str(org_id),
            "conversation_id": str(message.conversation_id),
            "inbound_message_id": str(message.id),
        },
        **kw,
    )


def _stub(label: str, p_opt_out: float, calls: list[str] | None = None):
    async def classify(text: str) -> intent.Verdict:
        if calls is not None:
            calls.append(text)
        return intent.Verdict(label=label, p_opt_out=p_opt_out, confidence=0.99)

    return classify


async def _state(session, org_id: uuid.UUID, message_id: uuid.UUID):
    async with session.begin():
        session.expire_all()
        row = (
            await session.execute(select(InboundMessage).where(InboundMessage.id == message_id))
        ).scalar_one()
        events = list(
            (
                await session.execute(
                    select(ConsentEvent).where(ConsentEvent.organization_id == org_id)
                )
            ).scalars()
        )
        prefs = list((await session.execute(select(Preference))).scalars())
        return row, events, prefs


async def test_a_confident_free_text_opt_out_suppresses_marketing(
    session, org_id, worker_engine
) -> None:
    message = await _message(session, org_id, "please stop texting me")
    calls: list[str] = []

    result = await consumers.classify_inbound_intent(
        _envelope(org_id, message), _stub(intent.OPT_OUT, 0.99, calls)
    )

    row, events, prefs = await _state(session, org_id, message.id)
    assert result == intent.ACTION_SUPPRESS
    assert calls == ["please stop texting me"]
    assert (row.intent, row.intent_action) == (intent.OPT_OUT, intent.ACTION_SUPPRESS)
    assert row.intent_p_opt_out == pytest.approx(0.99)
    assert row.intent_classified_at is not None
    # Marketing only, sourced as inferred, and the evidence holds no customer words.
    assert [(e.purpose, e.status, e.source) for e in events] == [
        (consent.MARKETING, consent.OPTED_OUT, consent.SOURCE_INBOUND_INTENT)
    ]
    assert "stop texting" not in (events[0].evidence or "")
    assert [p.suppressed for p in prefs] == [True]


async def test_a_middling_probability_flags_for_review_and_changes_nothing(
    session, org_id, worker_engine
) -> None:
    message = await _message(session, org_id, "leave me alone")

    result = await consumers.classify_inbound_intent(
        _envelope(org_id, message), _stub(intent.OPT_OUT, 0.81)
    )

    row, events, prefs = await _state(session, org_id, message.id)
    assert result == intent.ACTION_REVIEW
    assert row.intent_action == intent.ACTION_REVIEW
    assert events == [] and prefs == []


async def test_an_order_cancel_or_a_question_never_suppresses(
    session, org_id, worker_engine
) -> None:
    message = await _message(session, org_id, "how do I stop my subscription")

    result = await consumers.classify_inbound_intent(
        _envelope(org_id, message), _stub(intent.QUESTION, 0.02)
    )

    row, events, _ = await _state(session, org_id, message.id)
    assert result == intent.ACTION_NONE
    assert row.intent == intent.QUESTION and events == []


async def test_an_opt_in_label_is_recorded_but_never_restores_consent(
    session, org_id, worker_engine
) -> None:
    message = await _message(session, org_id, "yes keep sending me the offers")

    await consumers.classify_inbound_intent(_envelope(org_id, message), _stub(intent.OPT_IN, 0.0))

    row, events, prefs = await _state(session, org_id, message.id)
    assert row.intent == intent.OPT_IN
    assert events == [] and prefs == []


@pytest.mark.parametrize("body", ["STOP", "stop.", "START", "Unsubscribe!"])
async def test_keyword_messages_are_left_to_the_keyword_rules(
    session, org_id, worker_engine, body: str
) -> None:
    message = await _message(session, org_id, body)
    calls: list[str] = []

    result = await consumers.classify_inbound_intent(
        _envelope(org_id, message), _stub(intent.OPT_OUT, 1.0, calls)
    )

    row, events, _ = await _state(session, org_id, message.id)
    assert result == "skipped" and calls == []
    assert row.intent is None and events == []


async def test_media_and_empty_messages_are_never_sent_anywhere(
    session, org_id, worker_engine
) -> None:
    calls: list[str] = []
    image = await _message(session, org_id, "a caption", type_="image")
    empty = await _message(session, org_id, None, phone="+16505550000")

    for message in (image, empty):
        result = await consumers.classify_inbound_intent(
            _envelope(org_id, message), _stub(intent.OPT_OUT, 1.0, calls)
        )
        assert result == "skipped"
    assert calls == []


async def test_with_no_classifier_configured_nothing_happens(
    session, org_id, worker_engine
) -> None:
    message = await _message(session, org_id, "please stop texting me")

    result = await consumers.classify_inbound_intent(_envelope(org_id, message), None)

    row, events, _ = await _state(session, org_id, message.id)
    assert result == "disabled" and row.intent is None and events == []


async def test_a_redelivered_event_classifies_once(session, org_id, worker_engine) -> None:
    message = await _message(session, org_id, "remove me from your list")
    envelope = _envelope(org_id, message)
    calls: list[str] = []
    classify = _stub(intent.OPT_OUT, 0.99, calls)

    first = await consumers.classify_inbound_intent(envelope, classify)
    second = await consumers.classify_inbound_intent(envelope, classify)

    _, events, _ = await _state(session, org_id, message.id)
    assert (first, second) == (intent.ACTION_SUPPRESS, "redelivered")
    assert len(calls) == 1 and len(events) == 1


async def test_an_unreachable_classifier_raises_and_writes_nothing(
    session, org_id, worker_engine
) -> None:
    """Raising is what gets the event a real second attempt; marking it processed would turn a
    TypeSafe outage into a permanently unclassified message."""
    message = await _message(session, org_id, "no more promos")

    async def down(text: str) -> intent.Verdict:
        raise intent.IntentUnavailable("status 503")

    with pytest.raises(intent.IntentUnavailable):
        await consumers.classify_inbound_intent(_envelope(org_id, message), down)

    row, events, _ = await _state(session, org_id, message.id)
    assert row.intent is None and events == []
    # ...and the retry, once TypeSafe is back, does the work.
    retried = await consumers.classify_inbound_intent(
        _envelope(org_id, message), _stub(intent.OPT_OUT, 0.99)
    )
    assert retried == intent.ACTION_SUPPRESS


async def test_another_tenants_message_id_is_not_found(session, org_id, worker_engine) -> None:
    """Negative authz: an envelope for org B naming org A's message classifies nothing -- the
    tenant binding is the envelope's, and the lookup filters on it."""
    message = await _message(session, org_id, "please stop texting me")
    other_org = uuid.uuid4()
    calls: list[str] = []

    result = await consumers.classify_inbound_intent(
        _envelope(other_org, message), _stub(intent.OPT_OUT, 1.0, calls)
    )

    row, events, _ = await _state(session, org_id, message.id)
    assert result == "skipped" and calls == []
    assert row.intent is None and events == []
