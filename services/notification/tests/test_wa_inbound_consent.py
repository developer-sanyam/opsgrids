"""WhatsApp inbound and consent gaps, end to end over the real receive route.

What is proven here: the receive route verifies with the same app secret Embedded Signup uses
(either variable works); Meta's own marketing-opt-out signals (`user_preferences`, error 131050)
reach the consent ledger; a `template_category_update` changes what `consent.purpose_for_send`
says; archived and deleted templates stop being sendable; every inbound message type is parsed
(and media is recorded, not fetched); a "Stop promotions" quick reply opts out of marketing and
a "Cancel" does not opt out of anything; `account_update` invalidates the connection health for
the events that mean the business disconnected us.

Payload shapes follow Meta's webhook reference pages (see `app/webhooks.py` for which were read
and which are unverified).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from app import consent, domain, w1_selectors, w2_domain, webhooks
from app.models import (
    AuditLog,
    ConsentEvent,
    InboundMessage,
    Preference,
    ProviderAccount,
    ProviderTemplate,
)
from platform_core import pii
from platform_core.config import get_settings

pytestmark = pytest.mark.integration

SECRET = "ci-only-app-secret"
OTHER_SECRET = "ci-only-other-secret"
URL = "/api/v1/webhooks/whatsapp"
WABA = "waba-a"
PHONE_NUMBER_ID = "1001"
WA_ID = "16505551234"
PHONE = "+16505551234"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch: pytest.MonkeyPatch):
    """Both secret variables start unset; each test sets exactly the ones it means to."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch):
    """The usual case: the legacy variable holds the secret."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    get_settings.cache_clear()


def _headers(body: bytes, secret: str = SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


def _post(client, payload: dict[str, Any], secret: str = SECRET):
    body = json.dumps(payload).encode()
    return client.post(URL, content=body, headers=_headers(body, secret))


def _account_entry(field: str, value: dict[str, Any], time: int = 1_750_263_773) -> dict:
    return {"id": WABA, "time": time, "changes": [{"field": field, "value": value}]}


def _account_payload(field: str, value: dict[str, Any], time: int = 1_750_263_773) -> dict:
    return {"object": "whatsapp_business_account", "entry": [_account_entry(field, value, time)]}


def _messages_payload(
    *, messages: list[dict] | None = None, statuses: list[dict] | None = None
) -> dict:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": PHONE_NUMBER_ID},
    }
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": WABA, "changes": [{"value": value, "field": "messages"}]}],
    }


def _message(msg_type: str, wamid: str, **inner: Any) -> dict:
    return {
        "from": WA_ID,
        "id": wamid,
        "timestamp": str(int(datetime.now(UTC).timestamp())),
        "type": msg_type,
        msg_type: inner,
    }


async def _connect(admin_session, org_id: uuid.UUID) -> None:
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id=WABA,
                phone_number_id=PHONE_NUMBER_ID,
                display_number="+15550783881",
                status="connected",
            )
        )


async def _template(
    admin_session, org_id: uuid.UUID, *, category: str = "utility", status: str = "approved"
) -> None:
    async with admin_session.begin():
        admin_session.add(
            ProviderTemplate(
                organization_id=org_id,
                code="order_a",
                channel="whatsapp",
                provider_name="order_a",
                provider_template_id="555",
                language="en_US",
                category=category,
                status=status,
                variables=[],
                preview_text="Hi {{1}}",
                history=[{"status": status, "at": "2026-09-26T05:00:00+00:00", "source": "submit"}],
            )
        )


async def _template_row(admin_session, org_id: uuid.UUID) -> ProviderTemplate:
    async with admin_session.begin():
        return (
            await admin_session.execute(
                select(ProviderTemplate)
                .where(ProviderTemplate.organization_id == org_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


async def _opt_in_marketing(session, org_id: uuid.UUID) -> None:
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=(consent.MARKETING,),
            status=consent.OPTED_IN,
            source="checkout",
            evidence="test fixture",
        )


async def _decide(session, org_id: uuid.UUID, purpose: str) -> consent.ConsentDecision:
    async with session.begin():
        return await consent.decide(session, org_id, "whatsapp", purpose, PHONE)


async def _events(session, org_id: uuid.UUID) -> list[ConsentEvent]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(ConsentEvent)
                    .where(ConsentEvent.organization_id == org_id)
                    .order_by(ConsentEvent.created_at, ConsentEvent.id)
                )
            ).scalars()
        )


async def _preferences(session) -> list[Preference]:
    async with session.begin():
        return list((await session.execute(select(Preference))).scalars())


# ===================================================================== 1. the app secret


async def test_the_meta_app_secret_variable_alone_verifies_webhooks(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_APP_SECRET", SECRET)
    get_settings.cache_clear()
    response = await _post(client, {"object": "whatsapp_business_account", "entry": []})
    assert response.status_code == 200


async def test_the_legacy_whatsapp_app_secret_variable_alone_still_verifies(client, signed) -> None:
    response = await _post(client, {"object": "whatsapp_business_account", "entry": []})
    assert response.status_code == 200


async def test_meta_app_secret_wins_when_both_are_set(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same precedence `config.meta_app_secret` gives Embedded Signup: one secret, not two."""
    monkeypatch.setenv("META_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", OTHER_SECRET)
    get_settings.cache_clear()
    payload = {"object": "whatsapp_business_account", "entry": []}
    assert (await _post(client, payload, SECRET)).status_code == 200
    assert (await _post(client, payload, OTHER_SECRET)).status_code == 401


async def test_no_secret_configured_refuses_everything(client) -> None:
    response = await _post(client, {"object": "whatsapp_business_account", "entry": []})
    assert response.status_code == 401


async def test_the_get_handshake_needs_only_the_verify_token(client) -> None:
    """The GET handshake echoes a challenge for the verify token; it never used an app secret,
    so setting neither secret variable does not affect it."""
    response = await client.get(
        URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "ci-only-verify-token",
            "hub.challenge": "1234",
        },
    )
    assert response.status_code == 200
    assert response.text == "1234"


# ===================================================================== 2. marketing opt-out


def _user_preferences(value: str, *, category: str = "marketing_messages") -> dict:
    return {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": PHONE_NUMBER_ID},
        "contacts": [{"wa_id": WA_ID, "profile": {"name": "Sam"}}],
        "user_preferences": [
            {
                "wa_id": WA_ID,
                "detail": "User chose to stop marketing messages",
                "category": category,
                "value": value,
                "timestamp": 1_750_263_773,
            }
        ],
    }


async def test_a_user_preferences_stop_opts_marketing_out_and_is_audited(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    assert (await _decide(session, org_id, "marketing")).allowed is True

    response = await _post(client, _account_payload("user_preferences", _user_preferences("stop")))
    assert response.status_code == 200

    assert (await _decide(session, org_id, "marketing")).reason == "opted_out"
    # Only marketing: the customer's order updates are not part of what they turned off.
    assert (await _decide(session, org_id, "transactional")).allowed is True
    meta_events = [e for e in await _events(session, org_id) if e.source == "meta_user_preference"]
    assert [(e.purpose, e.status) for e in meta_events] == [("marketing", "opted_out")]
    assert all(
        PHONE not in (e.evidence or "") and WA_ID not in (e.evidence or "") for e in meta_events
    )

    (pref,) = await _preferences(session)
    assert (pref.category, pref.suppressed, pref.reason) == (
        "marketing",
        True,
        "meta_marketing_stop",
    )
    async with session.begin():
        audited = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.organization_id == org_id, AuditLog.action == "preference.set")
            )
        ).scalar_one()
    assert audited == 1


async def test_a_redelivered_or_repeated_stop_writes_nothing_more(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    body = _account_payload("user_preferences", _user_preferences("stop"))
    await _post(client, body)
    before = len(await _events(session, org_id))

    await _post(client, body)  # exact redelivery: deduped by processed_events
    later = _account_payload("user_preferences", _user_preferences("stop"), time=1_750_999_999)
    await _post(client, later)  # a fresh delivery of the same state: idempotent by state
    assert len(await _events(session, org_id)) == before


async def test_a_user_preferences_resume_restores_consent_that_existed(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    await _post(client, _account_payload("user_preferences", _user_preferences("stop")))
    await _post(
        client,
        _account_payload("user_preferences", _user_preferences("resume"), time=1_750_999_999),
    )

    assert (await _decide(session, org_id, "marketing")).allowed is True
    (pref,) = await _preferences(session)
    assert pref.suppressed is False


async def test_a_resume_never_creates_marketing_consent(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _post(client, _account_payload("user_preferences", _user_preferences("stop")))
    await _post(
        client,
        _account_payload("user_preferences", _user_preferences("resume"), time=1_750_999_999),
    )
    assert (await _decide(session, org_id, "marketing")).allowed is False


async def test_a_user_preference_for_another_category_is_ignored(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _post(
        client,
        _account_payload("user_preferences", _user_preferences("stop", category="something_else")),
    )
    assert await _events(session, org_id) == []
    assert await _preferences(session) == []


def _failed_status(code: int, *, wamid: str = "wamid.OUT131050") -> dict:
    return {
        "id": wamid,
        "status": "failed",
        "timestamp": "1750263773",
        "recipient_id": WA_ID,
        "errors": [{"code": code, "title": "Unable to deliver the message."}],
    }


async def test_error_131050_opts_the_recipient_out_of_marketing(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    response = await _post(client, _messages_payload(statuses=[_failed_status(131050)]))
    assert response.status_code == 200

    assert (await _decide(session, org_id, "marketing")).reason == "opted_out"
    assert (await _decide(session, org_id, "transactional")).allowed is True
    meta_events = [e for e in await _events(session, org_id) if e.source == "meta_user_preference"]
    assert [(e.purpose, e.status) for e in meta_events] == [("marketing", "opted_out")]
    assert "error_131050" in (meta_events[0].evidence or "")

    # Redelivery changes nothing.
    await _post(client, _messages_payload(statuses=[_failed_status(131050)]))
    assert len(await _events(session, org_id)) == 2  # the opt-in fixture and the one opt-out


async def test_another_failure_code_does_not_touch_consent(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    await _post(client, _messages_payload(statuses=[_failed_status(131026)]))
    assert (await _decide(session, org_id, "marketing")).allowed is True
    assert await _preferences(session) == []


# ===================================================================== 3. template category


def _category_update(**fields: Any) -> dict:
    return {
        "message_template_id": 555,
        "message_template_name": "order_a",
        "message_template_language": "en_US",
        **fields,
    }


async def _send_purpose(session, org_id: uuid.UUID) -> str:
    async with session.begin():
        return await consent.purpose_for_send(
            session,
            org_id,
            channel="whatsapp",
            category="order_updates",
            provider_template_code="order_a",
        )


async def test_a_template_reclassified_to_marketing_stops_being_transactional(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id)
    assert await _send_purpose(session, org_id) == consent.TRANSACTIONAL

    response = await _post(
        client,
        _account_payload(
            "template_category_update",
            _category_update(previous_category="UTILITY", new_category="MARKETING"),
        ),
    )
    assert response.status_code == 200
    assert (await _template_row(admin_session, org_id)).category == "marketing"
    assert await _send_purpose(session, org_id) == consent.MARKETING


async def test_an_announced_move_to_marketing_is_applied_early(
    client, signed, session, admin_session, org_id
) -> None:
    """Impending shape: `new_category` is the *current* category, `correct_category` the one it
    will have in 24 hours. Marketing carries the opt-in obligation, so it is applied at once."""
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id)
    await _post(
        client,
        _account_payload(
            "template_category_update",
            _category_update(
                new_category="UTILITY",
                correct_category="MARKETING",
                category_update_timestamp=1_751_000_000,
            ),
        ),
    )
    assert await _send_purpose(session, org_id) == consent.MARKETING


async def test_an_announced_move_away_from_marketing_waits_for_the_completed_event(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id, category="marketing")
    await _post(
        client,
        _account_payload(
            "template_category_update",
            _category_update(
                new_category="MARKETING",
                correct_category="UTILITY",
                category_update_timestamp=1_751_000_000,
            ),
        ),
    )
    assert (await _template_row(admin_session, org_id)).category == "marketing"

    await _post(
        client,
        _account_payload(
            "template_category_update",
            _category_update(previous_category="MARKETING", new_category="UTILITY"),
            time=1_750_999_999,
        ),
    )
    assert (await _template_row(admin_session, org_id)).category == "utility"


def test_category_after_update_covers_both_shapes() -> None:
    assert w2_domain.category_after_update({"new_category": "MARKETING"}) == "marketing"
    assert w2_domain.category_after_update({"new_category": "bogus"}) is None
    assert w2_domain.category_after_update({"correct_category": "UTILITY"}) is None


# ===================================================================== 3b. template lifecycle


def _status_update(event: str, **extra: Any) -> dict:
    return {
        "event": event,
        "message_template_id": 555,
        "message_template_name": "order_a",
        "message_template_language": "en_US",
        "reason": "NONE",
        **extra,
    }


async def _status_event(client, event: str, time: int) -> None:
    response = await _post(
        client, _account_payload("message_template_status_update", _status_update(event), time)
    )
    assert response.status_code == 200


@pytest.mark.parametrize("event", ["ARCHIVED", "DELETED"])
async def test_an_archived_or_deleted_template_is_no_longer_sendable(
    client, signed, admin_session, org_id, event
) -> None:
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id)
    await _status_event(client, event, 1_750_263_773)
    row = await _template_row(admin_session, org_id)
    assert row.status == "disabled"
    assert row.status_reason == event
    assert not domain.template_is_sendable(row.status)


async def test_unarchiving_restores_the_status_held_before(
    client, signed, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id)
    await _status_event(client, "ARCHIVED", 1_750_263_773)
    await _status_event(client, "UNARCHIVED", 1_750_263_999)
    row = await _template_row(admin_session, org_id)
    assert row.status == "approved"
    assert domain.template_is_sendable(row.status)


@pytest.mark.parametrize("event", ["LOCKED", "LIMIT_EXCEEDED"])
async def test_locked_and_limit_exceeded_change_no_status(
    client, signed, admin_session, org_id, event
) -> None:
    await _connect(admin_session, org_id)
    await _template(admin_session, org_id)
    await _status_event(client, event, 1_750_263_773)
    assert (await _template_row(admin_session, org_id)).status == "approved"


# ===================================================================== 4. inbound parsing


def _parse(message: dict) -> webhooks.InboundMessage:
    parsed = webhooks.inbound_from_raw(message)
    assert parsed is not None
    return parsed


def test_a_button_reply_is_parsed_into_label_and_payload() -> None:
    parsed = _parse(_message("button", "wamid.B1", payload="order_ack", text="Got it"))
    assert (parsed.message_type, parsed.body, parsed.reply_id) == ("button", "Got it", "order_ack")
    assert parsed.media is None


def test_an_interactive_button_reply_is_parsed() -> None:
    raw = _message("interactive", "wamid.I1", type="button_reply")
    raw["interactive"]["button_reply"] = {"id": "opt_1", "title": "Yes"}
    parsed = _parse(raw)
    assert (parsed.message_type, parsed.body, parsed.reply_id) == ("interactive", "Yes", "opt_1")


def test_an_interactive_list_reply_is_parsed() -> None:
    raw = _message("interactive", "wamid.I2", type="list_reply")
    raw["interactive"]["list_reply"] = {"id": "row_7", "title": "Tuesday", "description": "9am"}
    parsed = _parse(raw)
    assert (parsed.body, parsed.reply_id) == ("Tuesday", "row_7")


@pytest.mark.parametrize("kind", ["image", "audio", "video", "document", "sticker"])
def test_media_is_recorded_as_a_reference_that_has_not_been_fetched(kind: str) -> None:
    parsed = _parse(_message(kind, f"wamid.M.{kind}", id="MEDIA1", mime_type="x/y", sha256="abc"))
    assert parsed.message_type == kind
    assert parsed.media == {
        "kind": kind,
        "id": "MEDIA1",
        "mime_type": "x/y",
        "sha256": "abc",
        "fetch_state": "not_fetched",
    }
    assert parsed.body is None


def test_an_image_caption_becomes_the_body_and_a_document_keeps_its_filename() -> None:
    image = _parse(_message("image", "wamid.M1", id="I", mime_type="image/jpeg", caption="Look"))
    assert image.body == "Look"
    doc = _parse(_message("document", "wamid.M2", id="D", filename="po.pdf", caption="PO"))
    assert doc.media is not None and doc.media["filename"] == "po.pdf"


def test_a_malformed_shape_parses_to_nothing_rather_than_raising() -> None:
    raw = _message("button", "wamid.X1")
    raw["button"] = "not-an-object"
    parsed = _parse(raw)
    assert (parsed.body, parsed.reply_id, parsed.media) == (None, None, None)
    unknown = _parse(_message("order", "wamid.X2", catalog_id="1"))
    assert (unknown.body, unknown.reply_id, unknown.media) == (None, None, None)


async def test_parsed_button_and_media_details_are_stored_sealed(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    payload = _messages_payload(
        messages=[
            _message("button", "wamid.B1", payload="order_ack", text="Got it"),
            _message("image", "wamid.M1", id="MEDIA1", mime_type="image/jpeg", sha256="h"),
        ]
    )
    assert (await _post(client, payload)).status_code == 200

    async with session.begin():
        rows = {
            r.provider_message_id: r
            for r in (await session.execute(select(InboundMessage))).scalars()
        }
    button = rows["wamid.B1"]
    assert button.type == "button"
    assert pii.open_sealed(button.body_enc, settings=_settings()) == "Got it"
    stored = json.loads(pii.open_sealed(button.raw_enc, settings=_settings()))
    assert stored["_opsgrids"] == {"reply_id": "order_ack"}
    assert stored["button"]["payload"] == "order_ack"  # the provider's own object is kept whole

    image = rows["wamid.M1"]
    assert image.type == "image"
    assert image.body_enc is None
    media = json.loads(pii.open_sealed(image.raw_enc, settings=_settings()))["_opsgrids"]["media"]
    assert (media["id"], media["mime_type"], media["fetch_state"]) == (
        "MEDIA1",
        "image/jpeg",
        "not_fetched",
    )


def _settings():
    from app.config import settings

    return settings()


# ===================================================================== 5. STOP handling


async def _assert_marketing_only_opt_out(session, org_id: uuid.UUID) -> None:
    assert (await _decide(session, org_id, "marketing")).allowed is False
    assert (await _decide(session, org_id, "transactional")).allowed is True
    (pref,) = await _preferences(session)
    assert (pref.category, pref.suppressed, pref.reason) == ("marketing", True, "customer_stop")


async def test_a_stop_promotions_quick_reply_payload_opts_out_of_marketing(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    payload = _messages_payload(
        messages=[_message("button", "wamid.B1", payload="stop_promotions", text="Stop promotions")]
    )
    assert (await _post(client, payload)).status_code == 200
    await _assert_marketing_only_opt_out(session, org_id)


async def test_a_button_whose_label_says_stop_opts_out_even_with_an_opaque_payload(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    payload = _messages_payload(
        messages=[_message("button", "wamid.B2", payload="btn-3f9", text="Stop promotions")]
    )
    await _post(client, payload)
    (pref,) = await _preferences(session)
    assert (pref.category, pref.suppressed) == ("marketing", True)


async def test_an_interactive_reply_id_of_stop_opts_out(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    raw = _message("interactive", "wamid.I1", type="button_reply")
    raw["interactive"]["button_reply"] = {"id": "STOP", "title": "No more offers"}
    await _post(client, _messages_payload(messages=[raw]))
    (pref,) = await _preferences(session)
    assert (pref.category, pref.suppressed) == ("marketing", True)


@pytest.mark.parametrize("word", ["Cancel", "END", "quit"])
async def test_cancel_end_and_quit_do_not_opt_anyone_out_of_anything(
    client, signed, session, admin_session, org_id, word
) -> None:
    """A customer answering an order reminder with "Cancel" means the order."""
    await _connect(admin_session, org_id)
    await _opt_in_marketing(session, org_id)
    payload = _messages_payload(messages=[_message("text", "wamid.T1", body=word)])
    assert (await _post(client, payload)).status_code == 200

    assert await _preferences(session) == []
    assert (await _decide(session, org_id, "marketing")).allowed is True
    assert (await _decide(session, org_id, "transactional")).allowed is True
    assert len(await _events(session, org_id)) == 1  # only the fixture's opt-in


async def test_a_cancel_button_does_not_opt_out_either(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    payload = _messages_payload(
        messages=[_message("button", "wamid.B3", payload="cancel_order", text="Cancel")]
    )
    await _post(client, payload)
    assert await _preferences(session) == []
    assert await _events(session, org_id) == []


async def test_a_media_caption_saying_stop_is_not_a_command(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    raw = _message("image", "wamid.M9", id="I", mime_type="image/png", caption="STOP")
    await _post(client, _messages_payload(messages=[raw]))
    assert await _preferences(session) == []


# ===================================================================== 6. account_update


async def _health_state(session, org_id: uuid.UUID) -> str | None:
    async with session.begin():
        health = await w1_selectors.get_health(session, org_id)
        return health.token_state if health is not None else None


@pytest.mark.parametrize("event", ["PARTNER_APP_UNINSTALLED", "PARTNER_REMOVED"])
async def test_a_disconnecting_account_update_invalidates_the_connection_health(
    client, signed, session, admin_session, org_id, event
) -> None:
    await _connect(admin_session, org_id)
    assert await _health_state(session, org_id) is None
    response = await _post(client, _account_payload("account_update", {"event": event}))
    assert response.status_code == 200
    assert await _health_state(session, org_id) == "invalid"


async def test_other_account_updates_only_log(
    client, signed, session, admin_session, org_id
) -> None:
    await _connect(admin_session, org_id)
    response = await _post(
        client, _account_payload("account_update", {"event": "PARTNER_APP_INSTALLED"})
    )
    assert response.status_code == 200
    assert await _health_state(session, org_id) is None


async def test_an_account_update_for_an_unknown_waba_touches_nothing(
    client, signed, session, org_id
) -> None:
    response = await _post(
        client, _account_payload("account_update", {"event": "PARTNER_APP_UNINSTALLED"})
    )
    assert response.status_code == 200
    assert await _health_state(session, org_id) is None
