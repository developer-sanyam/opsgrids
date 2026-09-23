"""The inbound half of the WhatsApp webhook, driven over real HTTP.

`test_receipts.py` covers the status-fold logic directly; this covers what only shows up going
through the actual route: signature enforcement on a body carrying `messages`, a batch mixing
statuses and messages, redelivery, cross-tenant routing, and STOP reaching a reply already
possible a moment later.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import services
from app.models import Conversation, InboundDeadLetter, InboundMessage, Preference, ProviderAccount
from platform_core.auth import Scope
from platform_core.config import get_settings
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

# Named with the repository's `ci-only-` prefix, which the secret scanner's exclude pattern
# recognises — see tests/test_webhooks.py for the same convention.
SECRET = "ci-only-app-secret"
PHONE_NUMBER_ID = "106540352242922"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    """Settings are cached by class (`platform_core.config.get_settings`); the cache has to be
    cleared on both sides of the test or a secret set here would leak into the next module, and
    one set by an earlier module would make this one silently pass with the wrong key."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _headers(body: bytes) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}


async def _connect_account(
    session, org_id: uuid.UUID, *, phone_number_id: str = PHONE_NUMBER_ID, waba_id: str = "waba-1"
) -> None:
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                display_number="+15550783881",
                status="connected",
            )
        )


def _text_message(
    *,
    wamid: str = "wamid.IN1",
    sender: str = "16505551234",
    body: str = "Hi there",
    timestamp: str | None = None,
) -> dict:
    # A fresh timestamp by default rather than a fixed one: the conversation's service window is
    # computed from this against `datetime.now(UTC)`, and a fixed constant would drift out of the
    # window as the calendar moves past it — which is exactly what happened the first time this
    # test suite was written against 2026-09-19 and run afterwards.
    when = timestamp or str(int(datetime.now(UTC).timestamp()))
    return {"from": sender, "id": wamid, "timestamp": when, "type": "text", "text": {"body": body}}


def _status(*, wamid: str = "wamid.OUT1", status: str = "delivered") -> dict:
    return {
        "id": wamid,
        "status": status,
        "timestamp": "1750263773",
        "recipient_id": "16505551234",
    }


def _payload(
    *,
    statuses: list[dict] | None = None,
    messages: list[dict] | None = None,
    phone_number_id: str = PHONE_NUMBER_ID,
) -> dict:
    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": phone_number_id},
    }
    if statuses is not None:
        value["statuses"] = statuses
    if messages is not None:
        value["messages"] = messages
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-1", "changes": [{"value": value, "field": "messages"}]}],
    }


def _batched_payload(*bodies: dict) -> dict:
    """Several organizations' changes, in the one body Meta's own batching produces — each
    `_payload(...)`'s single entry, concatenated into one `entry` list."""
    entries = [entry for body in bodies for entry in body["entry"]]
    return {"object": "whatsapp_business_account", "entry": entries}


# --------------------------------------------------------------------- authenticity


async def test_an_unsigned_body_carrying_a_message_is_refused(client, session, org_id) -> None:
    await _connect_account(session, org_id)
    body = json.dumps(_payload(messages=[_text_message()])).encode()
    response = await client.post(
        "/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401

    async with session.begin():
        count = (
            await session.execute(select(func.count()).select_from(InboundMessage))
        ).scalar_one()
    assert count == 0


# --------------------------------------------------------------------- persistence


async def test_a_signed_message_creates_a_conversation_and_a_message(
    client, session, org_id
) -> None:
    await _connect_account(session, org_id)
    body = json.dumps(_payload(messages=[_text_message()])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        conversation = (await session.execute(select(Conversation))).scalars().one()
        inbound = (await session.execute(select(InboundMessage))).scalars().one()

    assert conversation.channel == "whatsapp"
    assert conversation.status == "open"
    assert conversation.last_customer_message_at is not None
    # Masked, never the number in full.
    assert "16505551234" not in conversation.wa_address_masked
    assert inbound.conversation_id == conversation.id
    assert inbound.type == "text"
    assert inbound.body_enc is not None
    assert "Hi there" not in inbound.body_masked


async def test_a_mixed_batch_of_statuses_and_messages_is_folded(client, session, org_id) -> None:
    await _connect_account(session, org_id)
    body = json.dumps(_payload(statuses=[_status()], messages=[_text_message()])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        messages = (await session.execute(select(InboundMessage))).scalars().all()
    assert len(messages) == 1


async def test_a_second_inbound_message_advances_the_same_conversation(
    client, session, org_id
) -> None:
    """Two messages from one number fold onto one conversation, not two."""
    await _connect_account(session, org_id)
    first = json.dumps(_payload(messages=[_text_message(wamid="wamid.IN1")])).encode()
    second = json.dumps(_payload(messages=[_text_message(wamid="wamid.IN2")])).encode()
    await client.post("/webhooks/whatsapp", content=first, headers=_headers(first))
    await client.post("/webhooks/whatsapp", content=second, headers=_headers(second))

    async with session.begin():
        conversations = (await session.execute(select(Conversation))).scalars().all()
        count = (
            await session.execute(select(func.count()).select_from(InboundMessage))
        ).scalar_one()
    assert len(conversations) == 1
    assert count == 2


# --------------------------------------------------------------------- redelivery


async def test_redelivery_of_the_same_body_is_a_no_op(client, session, org_id) -> None:
    await _connect_account(session, org_id)
    body = json.dumps(_payload(messages=[_text_message()])).encode()
    headers = _headers(body)

    first = await client.post("/webhooks/whatsapp", content=body, headers=headers)
    second = await client.post("/webhooks/whatsapp", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200

    async with session.begin():
        count = (
            await session.execute(select(func.count()).select_from(InboundMessage))
        ).scalar_one()
    assert count == 1


# --------------------------------------------------------------------- cross-tenant


async def test_a_webhook_for_one_orgs_number_never_touches_another_org(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    await _connect_account(session, org_id)

    body = json.dumps(_payload(messages=[_text_message()])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with admin_session.begin():
        theirs = (
            (
                await admin_session.execute(
                    select(Conversation).where(Conversation.organization_id == other)
                )
            )
            .scalars()
            .all()
        )
        ours = (
            (
                await admin_session.execute(
                    select(Conversation).where(Conversation.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    assert theirs == []
    assert len(ours) == 1


# --------------------------------------------------------------------- STOP


async def test_stop_suppresses_and_the_next_reply_is_refused(client, session, org_id) -> None:
    await _connect_account(session, org_id)
    body = json.dumps(_payload(messages=[_text_message(body="STOP")])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        pref = (
            (await session.execute(select(Preference).where(Preference.channel == "whatsapp")))
            .scalars()
            .one()
        )
        assert pref.suppressed is True
        assert pref.reason == "customer_stop"

        conversation = (await session.execute(select(Conversation))).scalars().one()
        ctx = ctx_for(org_id, "engage.inbox.message.reply", Scope.ORG)
        message = await services.reply_to_conversation(
            session,
            ctx,
            conversation_id=conversation.id,
            body="We got your message",
            idempotency_key="k1",
        )
    # Staged, not refused outright — "we deliberately did not tell this customer" is a fact kept
    # on the message row, the same way a rule-driven send records a suppression.
    assert message.state == "suppressed"


# --------------------------------------------------------------------- cross-organization batching


async def test_a_batch_naming_two_organizations_numbers_is_never_misrouted(
    client, session, admin_session, org_id
) -> None:
    """The blocker this file exists to close: Meta batching two WABAs' changes into one `POST`
    (one platform App Secret signs all of them) must not let organization B's status or message
    land against organization A's rows just because A's number happened to be resolved first."""
    other_org = uuid.uuid4()
    other_phone = "999999999999999"

    await _connect_account(session, org_id, phone_number_id=PHONE_NUMBER_ID)
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=other_org,
                channel="whatsapp",
                waba_id="waba-other",
                phone_number_id=other_phone,
                display_number="+19995550000",
                status="connected",
            )
        )

    ours = _payload(
        statuses=[_status(wamid="wamid.OURS", status="delivered")],
        messages=[_text_message(wamid="wamid.IN.OURS", sender="16505551234", body="ours")],
        phone_number_id=PHONE_NUMBER_ID,
    )
    theirs = _payload(
        statuses=[_status(wamid="wamid.THEIRS", status="delivered")],
        messages=[_text_message(wamid="wamid.IN.THEIRS", sender="19995551111", body="theirs")],
        phone_number_id=other_phone,
    )
    body = json.dumps(_batched_payload(ours, theirs)).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        our_messages = (await session.execute(select(InboundMessage))).scalars().all()
    assert {m.provider_message_id for m in our_messages} == {"wamid.IN.OURS"}

    async with admin_session.begin():
        their_messages = (
            (
                await admin_session.execute(
                    select(InboundMessage).where(InboundMessage.organization_id == other_org)
                )
            )
            .scalars()
            .all()
        )
        our_messages_via_admin = (
            (
                await admin_session.execute(
                    select(InboundMessage).where(InboundMessage.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    assert {m.provider_message_id for m in their_messages} == {"wamid.IN.THEIRS"}
    assert {m.provider_message_id for m in our_messages_via_admin} == {"wamid.IN.OURS"}


# --------------------------------------------------------------------- dead-lettering


async def test_one_bad_item_is_dead_lettered_and_the_rest_of_the_batch_still_folds(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single malformed or oversized item must not fail the whole request, and must not stop
    the other items in the same batch from folding."""
    await _connect_account(session, org_id)

    real_fold = services.fold_inbound_message

    async def _flaky_fold(session, *, organization_id, inbound):
        if inbound.provider_message_id == "wamid.BAD":
            raise ValueError("boom")
        return await real_fold(session, organization_id=organization_id, inbound=inbound)

    monkeypatch.setattr(services, "fold_inbound_message", _flaky_fold)

    payload = _payload(
        messages=[
            _text_message(wamid="wamid.BAD", body="this one blows up"),
            _text_message(wamid="wamid.GOOD", body="this one is fine"),
        ]
    )
    body = json.dumps(payload).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        good = (
            (
                await session.execute(
                    select(InboundMessage).where(InboundMessage.provider_message_id == "wamid.GOOD")
                )
            )
            .scalars()
            .all()
        )
        dead_letters = (await session.execute(select(InboundDeadLetter))).scalars().all()

    assert len(good) == 1
    assert len(dead_letters) == 1
    assert dead_letters[0].provider_message_id == "wamid.BAD"
    assert dead_letters[0].kind == "inbound"
    # No PII in the parked row: the offending body text must never appear in the error column.
    assert "blows up" not in dead_letters[0].error


# --------------------------------------------------------------------- long bodies


async def test_a_body_longer_than_the_old_mask_column_does_not_crash_the_fold(
    client, session, org_id
) -> None:
    """`body_masked` used to be a function of the real body's length (`_mask_generic`'s bullets
    plus the last two characters), so a message long enough overflowed the column and the whole
    request 500'd — taking every other item in the batch down with it. The mask is now a fixed
    placeholder, so this is just an ordinary message."""
    await _connect_account(session, org_id)
    long_body = "x" * 4000

    payload = _payload(
        messages=[
            _text_message(wamid="wamid.LONG", body=long_body),
            _text_message(wamid="wamid.AFTER", body="still arrives"),
        ]
    )
    body = json.dumps(payload).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        rows = (await session.execute(select(InboundMessage))).scalars().all()
        dead_letters = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()

    assert {r.provider_message_id for r in rows} == {"wamid.LONG", "wamid.AFTER"}
    assert dead_letters == 0
    long_row = next(r for r in rows if r.provider_message_id == "wamid.LONG")
    assert len(long_row.body_masked) <= 40
    assert "x" * 10 not in long_row.body_masked


# --------------------------------------------------------------------- no plaintext PII at rest


async def test_the_sender_and_body_never_land_in_a_clear_column(client, session, org_id) -> None:
    """`raw_enc` is sealed and there is no plaintext `raw` column at all — the fold's whole
    envelope, including `from`, must never be readable without opening a sealed column."""
    await _connect_account(session, org_id)
    payload = _payload(
        messages=[_text_message(sender="16505551234", body="my card number is 4111111111111111")]
    )
    body = json.dumps(payload).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        row = (await session.execute(select(InboundMessage))).scalars().one()

    assert not hasattr(row, "raw")
    assert row.raw_enc is not None
    assert "4111111111111111" not in row.body_masked
    assert row.profile_name_masked is None or "4111111111111111" not in row.profile_name_masked
