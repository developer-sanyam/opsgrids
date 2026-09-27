"""Two hardening fixes on Meta's webhook: a delivery receipt that beats its own send's commit is not
lost, and a non-ASCII signature or verify token is refused rather than crashing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import domain, services, webhooks
from app.models import InboundDeadLetter, Message, ProcessedEvent, ProviderAccount
from platform_core.config import get_settings

pytestmark = pytest.mark.integration

SECRET = "ci-only-app-secret"
VERIFY_TOKEN = "ci-only-verify-token"
PHONE_NUMBER_ID = "106540352242922"
WAMID = "wamid.RACE1"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


def _status_body(*, timestamp: datetime, status: str = "delivered") -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "statuses": [
                                {
                                    "id": WAMID,
                                    "status": status,
                                    "timestamp": str(int(timestamp.timestamp())),
                                    "recipient_id": "16505551234",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


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


async def _commit_the_send(session, org_id: uuid.UUID) -> None:
    """What `record_sent` commits once Meta's response is back: the row, now carrying its wamid."""
    async with session.begin():
        session.add(
            Message(
                organization_id=org_id,
                channel="whatsapp",
                category="operational",
                to_address="+917976603446",
                subject=None,
                body="Invoice INV-9 is due.",
                state=domain.STATE_SENT,
                attempts=1,
                provider_message_id=WAMID,
                sent_at=datetime.now(UTC),
            )
        )


# --------------------------------------------------------------------- the receipt race


async def test_a_receipt_that_beats_the_wamid_commit_is_redelivered_not_dropped(
    client, session, org_id
) -> None:
    await _connect_account(session, org_id)
    body = _status_body(timestamp=datetime.now(UTC))

    # The receipt lands before `record_sent` has committed the wamid: Meta is told to retry, and
    # the receipt is not marked processed.
    early = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert early.status_code >= 500
    async with session.begin():
        processed = (
            await session.execute(
                select(func.count())
                .select_from(ProcessedEvent)
                .where(ProcessedEvent.event_type.like("whatsapp.status.%"))
            )
        ).scalar_one()
        dead = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()
    assert (processed, dead) == (0, 0)

    # The send commits; Meta's redelivery now finds the message and applies the receipt.
    await _commit_the_send(session, org_id)
    redelivered = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert redelivered.status_code == 200
    async with session.begin():
        message = (await session.execute(select(Message))).scalars().one()
    assert message.state == domain.STATE_DELIVERED
    assert message.delivered_at is not None


async def test_an_old_receipt_for_an_unknown_message_is_still_acknowledged_and_dropped(
    client, session, org_id
) -> None:
    """Another tool's message on the same number: not ours, and not worth a redelivery loop."""
    await _connect_account(session, org_id)
    body = _status_body(timestamp=datetime.now(UTC) - services.RECEIPT_RACE_WINDOW * 2)
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200
    async with session.begin():
        processed = (
            await session.execute(
                select(func.count())
                .select_from(ProcessedEvent)
                .where(ProcessedEvent.event_type.like("whatsapp.status.%"))
            )
        ).scalar_one()
    assert processed == 1


async def test_fold_status_raises_for_a_fresh_unknown_wamid_and_returns_none_for_an_old_one(
    session, org_id
) -> None:
    async with session.begin():
        with pytest.raises(services.ReceiptBeforeSend):
            await services.fold_status(
                session,
                organization_id=org_id,
                provider_message_id=WAMID,
                status=domain.STATE_SENT,
                occurred_at=datetime.now(UTC),
            )
    async with session.begin():
        folded = await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id=WAMID,
            status=domain.STATE_SENT,
            occurred_at=datetime.now(UTC) - timedelta(hours=1),
        )
    assert folded is None


async def test_the_race_error_is_transient_not_permanent() -> None:
    """The webhook parks permanent errors and lets transient ones raise into a 5xx."""
    assert not services.is_permanent_fold_error(services.ReceiptBeforeSend(WAMID))


# --------------------------------------------------------------------- non-ASCII secrets

NON_ASCII = "sha256=café☃"


def test_a_non_ascii_signature_is_not_a_signature() -> None:
    assert webhooks.verify_signature(b"{}", NON_ASCII, SECRET) is False
    assert webhooks.verify_signature(b"{}", "sha256=\ud800", SECRET) is False


def test_a_genuine_signature_still_verifies() -> None:
    body = b'{"x": 1}'
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert webhooks.verify_signature(body, f"sha256={digest}", SECRET) is True


def test_a_non_ascii_verify_token_is_refused_not_a_crash() -> None:
    with pytest.raises(webhooks.WebhookError):
        webhooks.verification_challenge(
            "subscribe", "töken", "challenge", expected_token=VERIFY_TOKEN
        )
    with pytest.raises(webhooks.WebhookError):
        webhooks.verification_challenge(
            "subscribe", VERIFY_TOKEN, "challenge", expected_token="töken"
        )
    assert (
        webhooks.verification_challenge("subscribe", "töken", "challenge", expected_token="töken")
        == "challenge"
    )


async def test_a_non_ascii_signature_header_answers_401_not_500(client, session, org_id) -> None:
    body = b'{"object": "whatsapp_business_account", "entry": []}'
    response = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers=[
            (b"content-type", b"application/json"),
            (b"x-hub-signature-256", NON_ASCII.encode("utf-8")),
        ],
    )
    assert response.status_code == 401


async def test_a_non_ascii_verify_token_query_answers_403_not_500(client) -> None:
    response = await client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "töken☃",
            "hub.challenge": "1234",
        },
    )
    assert response.status_code == 403
