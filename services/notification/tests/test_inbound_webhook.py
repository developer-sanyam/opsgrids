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
from app.models import Conversation, InboundMessage, Preference, ProviderAccount
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


async def _connect_account(session, org_id: uuid.UUID) -> None:
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-1",
                phone_number_id=PHONE_NUMBER_ID,
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


def _payload(*, statuses: list[dict] | None = None, messages: list[dict] | None = None) -> dict:
    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": PHONE_NUMBER_ID},
    }
    if statuses is not None:
        value["statuses"] = statuses
    if messages is not None:
        value["messages"] = messages
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-1", "changes": [{"value": value, "field": "messages"}]}],
    }


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
