"""The inbound half of the WhatsApp webhook, driven over real HTTP.

`test_receipts.py` covers the status-fold logic directly; this covers what only shows up going
through the actual route: signature enforcement on a body carrying `messages`, a batch mixing
statuses and messages, redelivery, cross-tenant routing, and STOP reaching a reply already
possible a moment later.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import replay_dead_letters, services
from app.models import Conversation, InboundDeadLetter, InboundMessage, Preference, ProviderAccount
from platform_core.auth import Scope
from platform_core.config import get_settings
from platform_core.pii import open_sealed
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
    waba_id: str = "waba-1",
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
        "entry": [{"id": waba_id, "changes": [{"value": value, "field": "messages"}]}],
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


async def test_concurrent_duplicate_inbound_deliveries_produce_one_message_and_no_dead_letters(
    client, session, org_id
) -> None:
    """`processed_events` cannot catch every duplicate: two concurrent deliveries of the same
    `wamid` both read "not yet processed" before either commits, so the loser reaches the
    database's own dedupe constraint (`uq_inbound_messages_org_provider_id`) instead. That is
    still success, not a permanent failure to park — the message the loser was trying to store
    already exists, courtesy of the winner."""
    await _connect_account(session, org_id)
    body = json.dumps(_payload(messages=[_text_message(wamid="wamid.RACE")])).encode()
    headers = _headers(body)

    responses = await asyncio.gather(
        client.post("/webhooks/whatsapp", content=body, headers=headers),
        client.post("/webhooks/whatsapp", content=body, headers=headers),
    )
    assert all(r.status_code == 200 for r in responses)

    async with session.begin():
        messages = (await session.execute(select(InboundMessage))).scalars().all()
        dead_letters = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()
    assert len(messages) == 1
    assert dead_letters == 0


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


async def test_stop_suppresses_marketing_only_and_a_service_reply_still_goes(
    client, session, org_id
) -> None:
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
        assert pref.category == "marketing"

        conversation = (await session.execute(select(Conversation))).scalars().one()
        ctx = ctx_for(org_id, "engage.inbox.message.reply", Scope.ORG)
        message = await services.reply_to_conversation(
            session,
            ctx,
            conversation_id=conversation.id,
            body="We got your message",
            idempotency_key="k1",
        )
    # STOP is a request to stop marketing, so a human answering the customer's own message inside
    # the service window is not refused (it was, when STOP wrote a `*` row).
    assert message.state != "suppressed"


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
        waba_id="waba-other",
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


async def test_a_wamid_longer_than_the_column_is_dead_lettered_exactly_once(
    client, session, org_id
) -> None:
    """No mocking here — a `wamid` this long genuinely overflows `inbound_messages.
    provider_message_id` (`String(200)`) and Postgres itself raises a real data exception at the
    insert. SQLAlchemy's asyncpg dialect never raises its own `DataError` for that — it comes back
    as a generic `DBAPIError` carrying SQLSTATE `22001` — so `is_permanent_fold_error` has to read
    the SQLSTATE rather than the Python exception type, or this would be misclassified transient
    and 503 forever instead of being parked once."""
    await _connect_account(session, org_id)
    long_wamid = "wamid." + "9" * 250
    assert len(long_wamid) == 256

    body = json.dumps(_payload(messages=[_text_message(wamid=long_wamid)])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        dead_letters = (await session.execute(select(InboundDeadLetter))).scalars().all()

    assert len(dead_letters) == 1
    # Truncated to fit the dead-letter table's own column; the full id survives inside raw_enc.
    assert dead_letters[0].provider_message_id == long_wamid[:200]
    sealed = json.loads(open_sealed(dead_letters[0].raw_enc, settings=services.settings()))
    assert sealed["id"] == long_wamid


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


# --------------------------------------------------------------------- transient vs permanent


async def test_a_transient_fold_error_answers_5xx_and_redelivery_folds_exactly_once(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection or a deadlock is not the same failure a duplicate key or a bad value
    is: parking it the way a permanent error is parked would lose the message. The whole request
    answers with a 5xx instead so Meta redelivers the body — safe because `processed_events`
    dedupes whatever already folded, so the good message in the same batch is not re-folded and
    only the one that actually failed does any work on the second attempt."""
    await _connect_account(session, org_id)
    real_fold = services.fold_inbound_message

    async def _flaky_fold(session, *, organization_id, inbound):
        if inbound.provider_message_id == "wamid.TRANSIENT":
            raise OperationalError("SELECT 1", {}, Exception("connection reset by peer"))
        return await real_fold(session, organization_id=organization_id, inbound=inbound)

    monkeypatch.setattr(services, "fold_inbound_message", _flaky_fold)

    payload = _payload(
        messages=[
            _text_message(wamid="wamid.TRANSIENT", body="please retry me"),
            _text_message(wamid="wamid.GOOD", body="this one is fine"),
        ]
    )
    body = json.dumps(payload).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code >= 500

    async with session.begin():
        rows = (await session.execute(select(InboundMessage))).scalars().all()
        dead_letters = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()
    # The good item in the same batch was not held hostage by the bad one.
    assert {r.provider_message_id for r in rows} == {"wamid.GOOD"}
    # The transient item was not parked — parking it would have lost it for good.
    assert dead_letters == 0

    monkeypatch.setattr(services, "fold_inbound_message", real_fold)
    redelivered = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert redelivered.status_code == 200

    async with session.begin():
        rows = (await session.execute(select(InboundMessage))).scalars().all()
    # The good message did not fold a second time; the transient one folds for the first time.
    assert sorted(r.provider_message_id for r in rows) == ["wamid.GOOD", "wamid.TRANSIENT"]


async def test_a_stale_transient_failure_is_parked_instead_of_503ing_forever(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection blip six hours old is not "about to clear on the next redelivery" — Meta's own
    retry schedule has long since tapered off by then. Left to raise a 503 forever, this item
    would never be redelivered again; parked instead, it is at least recoverable through
    `app/replay_dead_letters.py`."""
    await _connect_account(session, org_id)
    real_fold = services.fold_inbound_message

    async def _flaky_fold(session, *, organization_id, inbound):
        if inbound.provider_message_id == "wamid.STALE":
            raise OperationalError("SELECT 1", {}, Exception("connection reset by peer"))
        return await real_fold(session, organization_id=organization_id, inbound=inbound)

    monkeypatch.setattr(services, "fold_inbound_message", _flaky_fold)

    stale_timestamp = str(int((datetime.now(UTC) - timedelta(hours=7)).timestamp()))
    payload = _payload(
        messages=[
            _text_message(wamid="wamid.STALE", body="stuck for hours", timestamp=stale_timestamp),
            _text_message(wamid="wamid.GOOD", body="this one is fine"),
        ]
    )
    body = json.dumps(payload).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        rows = (await session.execute(select(InboundMessage))).scalars().all()
        dead_letters = (await session.execute(select(InboundDeadLetter))).scalars().all()

    assert {r.provider_message_id for r in rows} == {"wamid.GOOD"}
    assert len(dead_letters) == 1
    assert dead_letters[0].provider_message_id == "wamid.STALE"


async def test_a_permanent_fold_error_is_parked_sealed_and_replayable(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent error is parked, not discarded: `raw_enc` seals the item's own payload so
    `app/replay_dead_letters.py` can retry the fold once whatever made it permanent is fixed —
    every dead letter is recoverable, not just visible."""
    await _connect_account(session, org_id)
    real_fold = services.fold_inbound_message

    async def _flaky_fold(session, *, organization_id, inbound):
        if inbound.provider_message_id == "wamid.PERMANENT":
            raise ValueError("this shape will never parse, no matter how many times it is tried")
        return await real_fold(session, organization_id=organization_id, inbound=inbound)

    monkeypatch.setattr(services, "fold_inbound_message", _flaky_fold)

    body = json.dumps(
        _payload(messages=[_text_message(wamid="wamid.PERMANENT", body="parked, not lost")])
    ).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        dead_letter = (await session.execute(select(InboundDeadLetter))).scalars().one()
    assert dead_letter.provider_message_id == "wamid.PERMANENT"
    sealed = json.loads(open_sealed(dead_letter.raw_enc, settings=services.settings()))
    assert sealed["id"] == "wamid.PERMANENT"
    assert sealed["text"]["body"] == "parked, not lost"
    # The error column never carries the exception's own message.
    assert "will never parse" not in dead_letter.error

    monkeypatch.setattr(services, "fold_inbound_message", real_fold)
    replayed = await replay_dead_letters.replay(
        organization_id=org_id, operator="ops@opsgrids.example"
    )
    assert replayed == 1

    async with session.begin():
        rows = (await session.execute(select(InboundMessage))).scalars().all()
        remaining = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()
    assert {r.provider_message_id for r in rows} == {"wamid.PERMANENT"}
    assert remaining == 0


# --------------------------------------------------------------------- preferences


async def test_concurrent_stop_requests_do_not_crash_and_leave_one_suppressed_row(
    client, session, org_id
) -> None:
    """The bug the read-then-write shape had: two STOPs from the same number racing (two
    messages, two transactions, both reading "no preference yet" before either commits) used to
    both attempt an insert and the loser raised `IntegrityError` on `uq_preferences_org_
    recipient` — an ordinary customer action crashing the fold it triggered. The upsert makes
    the write itself race-free at the database, real concurrency and all."""
    await _connect_account(session, org_id)
    first = json.dumps(
        _payload(messages=[_text_message(wamid="wamid.STOP1", body="STOP")])
    ).encode()
    second = json.dumps(
        _payload(messages=[_text_message(wamid="wamid.STOP2", body="Stop")])
    ).encode()

    responses = await asyncio.gather(
        client.post("/webhooks/whatsapp", content=first, headers=_headers(first)),
        client.post("/webhooks/whatsapp", content=second, headers=_headers(second)),
    )
    assert all(r.status_code == 200 for r in responses)

    async with session.begin():
        prefs = (
            (await session.execute(select(Preference).where(Preference.channel == "whatsapp")))
            .scalars()
            .all()
        )
    assert len(prefs) == 1
    assert prefs[0].suppressed is True
    assert prefs[0].reason == "customer_stop"


async def test_start_only_lifts_a_customer_stop_never_an_operator_suppression(
    client, session, org_id
) -> None:
    """An operator who suppressed a number for their own reason (a complaint, a legal hold) did
    not ask a customer's stray "START" to undo it — only a row this same convention put there,
    `reason == "customer_stop"`, is one START is entitled to reverse."""
    await _connect_account(session, org_id)
    ctx = ctx_for(org_id, "engage.setup.rules.write", Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session,
            ctx,
            recipient="+16505551234",
            channel="whatsapp",
            category="*",
            suppressed=True,
            reason="legal_hold",
        )

    body = json.dumps(_payload(messages=[_text_message(body="START")])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        pref = (
            (await session.execute(select(Preference).where(Preference.channel == "whatsapp")))
            .scalars()
            .one()
        )
    assert pref.suppressed is True
    assert pref.reason == "legal_hold"


async def test_dead_letter_error_never_carries_a_parameter_value(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`error` is the exception's type, SQLSTATE and constraint name only — a database error's
    own text can echo a parameter straight out of the statement that failed, and this row is not
    sealed the way `raw_enc` is."""
    await _connect_account(session, org_id)
    real_fold = services.fold_inbound_message
    needle = "a-very-distinctive-parameter-value-12345"

    async def _flaky_fold(session, *, organization_id, inbound):
        if inbound.provider_message_id == "wamid.DATAERROR":
            from sqlalchemy.exc import DataError

            # A stand-in for a real driver error's own text quoting a parameter back — never an
            # actual query this test runs. `sqlstate` on the driver-shaped `orig` mirrors what
            # SQLAlchemy's asyncpg dialect actually attaches (see `is_permanent_fold_error`'s
            # docstring): classification reads that, never the exception's own Python type or its
            # message, which is the one place `needle` could otherwise leak from.
            class _FakeDriverError(Exception):
                sqlstate = "22001"  # string_data_right_truncation — the value did not fit

            statement = "INSERT INTO x (y) VALUES (%(y)s)"
            raise DataError(statement, {"y": needle}, _FakeDriverError(needle))
        return await real_fold(session, organization_id=organization_id, inbound=inbound)

    monkeypatch.setattr(services, "fold_inbound_message", _flaky_fold)

    body = json.dumps(_payload(messages=[_text_message(wamid="wamid.DATAERROR")])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        dead_letter = (await session.execute(select(InboundDeadLetter))).scalars().one()
    assert needle not in dead_letter.error
    assert "DataError" in dead_letter.error


# --------------------------------------------------------------------- WABA / entry mismatch


async def test_a_change_whose_entry_id_does_not_match_the_accounts_waba_is_dropped(
    client, session, org_id
) -> None:
    """The signature proves the platform's own App Secret signed the body, not which WABA a
    number belongs to. A change claiming a WABA that does not match this number's registered
    `ProviderAccount.waba_id` is dropped rather than trusted — the same reasoning a stale routing
    table anywhere else on the platform would get."""
    await _connect_account(session, org_id, waba_id="waba-registered")
    body = json.dumps(
        _payload(messages=[_text_message(wamid="wamid.MISMATCH")], waba_id="waba-different")
    ).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        count = (
            await session.execute(select(func.count()).select_from(InboundMessage))
        ).scalar_one()
    assert count == 0
