"""Webhook authenticity and parsing — the one surface that accepts input from outside.

There is no token behind this endpoint; Meta cannot hold one. So the only thing separating a
genuine delivery receipt from a forged one is the signature, and these tests are mostly about
refusing things: a tampered body, a wrong secret, a missing header, a replayed event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from app import webhooks

# Named with the repository's `ci-only-` prefix, which the secret scanner's exclude
# pattern recognises. A pragma would silence the finding; the prefix makes the string
# self-evidently not a credential, which is the better answer to a scanner that is right
# to be suspicious of a variable called SECRET.
SECRET = "ci-only-app-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _status_payload(status: str = "delivered", wamid: str = "wamid.ABC") -> dict:
    """The shape Meta publishes for a status webhook."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "106540352242922",
                            },
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": status,
                                    "timestamp": "1750263773",
                                    "recipient_id": "16505551234",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


# --------------------------------------------------------------------- authenticity


def test_a_genuine_signature_verifies() -> None:
    body = json.dumps(_status_payload()).encode()
    assert webhooks.verify_signature(body, _sign(body), SECRET)


def test_a_tampered_body_is_refused() -> None:
    """The attack this exists to stop: a forged delivery receipt, or a forged inbound message
    attributed to a customer."""
    body = json.dumps(_status_payload()).encode()
    signature = _sign(body)
    tampered = json.dumps(_status_payload(status="read")).encode()
    assert not webhooks.verify_signature(tampered, signature, SECRET)


def test_a_signature_from_the_wrong_secret_is_refused() -> None:
    body = json.dumps(_status_payload()).encode()
    assert not webhooks.verify_signature(body, _sign(body, "ci-only-wrong-secret"), SECRET)


@pytest.mark.parametrize("header", [None, "", "sha256=", "deadbeef", "sha1=deadbeef"])
def test_a_missing_or_malformed_header_is_refused(header: str | None) -> None:
    body = b"{}"
    assert not webhooks.verify_signature(body, header, SECRET)


def test_re_serialising_the_body_would_break_verification() -> None:
    """Why the raw bytes must be read before any JSON parsing. This asserts the trap directly:
    a semantically identical body with different whitespace does not verify, so a handler that
    parsed first and re-serialised would reject every genuine webhook."""
    body = json.dumps(_status_payload(), separators=(",", ":")).encode()
    signature = _sign(body)
    reserialised = json.dumps(json.loads(body), indent=2).encode()
    assert body != reserialised
    assert not webhooks.verify_signature(reserialised, signature, SECRET)


# --------------------------------------------------------------------- the GET handshake


def test_the_challenge_is_echoed_when_the_token_matches() -> None:
    assert (
        webhooks.verification_challenge("subscribe", "tok", "1158201444", expected_token="tok")
        == "1158201444"
    )


def test_a_wrong_verify_token_is_refused() -> None:
    with pytest.raises(webhooks.WebhookError, match="token does not match"):
        webhooks.verification_challenge("subscribe", "guess", "123", expected_token="tok")


def test_an_unconfigured_verify_token_refuses_everything() -> None:
    """A service with no token configured must not accept an empty one and hand over the
    endpoint — that would let anybody subscribe to it."""
    with pytest.raises(webhooks.WebhookError):
        webhooks.verification_challenge("subscribe", "", "123", expected_token="")


def test_a_non_subscribe_mode_is_refused() -> None:
    with pytest.raises(webhooks.WebhookError, match="not a subscription"):
        webhooks.verification_challenge("unsubscribe", "tok", "123", expected_token="tok")


# --------------------------------------------------------------------- routing


def test_the_phone_number_id_identifies_the_tenant() -> None:
    assert webhooks.phone_number_id(_status_payload()) == "106540352242922"


@pytest.mark.parametrize("payload", [{}, {"entry": []}, {"entry": [{"changes": []}]}])
def test_a_payload_with_no_metadata_resolves_to_none(payload: dict) -> None:
    """Acknowledged rather than failed: Meta retries anything it does not get a 200 for, and
    retrying a payload we will never understand is a loop."""
    assert webhooks.phone_number_id(payload) is None


# --------------------------------------------------------------------- statuses


def test_a_status_is_parsed() -> None:
    update = webhooks.parse_statuses(_status_payload())[0]
    assert update.provider_message_id == "wamid.ABC"
    assert update.status == "delivered"
    assert update.recipient == "16505551234"
    assert update.occurred_at == datetime.fromtimestamp(1750263773, tz=UTC)


@pytest.mark.parametrize("status", ["sent", "delivered", "read", "failed"])
def test_every_documented_status_parses(status: str) -> None:
    assert webhooks.parse_statuses(_status_payload(status=status))[0].status == status


def test_an_unfamiliar_status_is_skipped_not_rejected() -> None:
    """Meta adds statuses. A build that rejected a webhook for carrying one would start
    failing on their release schedule rather than ours."""
    assert webhooks.parse_statuses(_status_payload(status="warp-speed")) == []


def test_several_statuses_in_one_body_are_all_parsed() -> None:
    """Meta batches freely, and it will not resend the ones a handler dropped."""
    payload = _status_payload()
    payload["entry"][0]["changes"][0]["value"]["statuses"].append(
        {
            "id": "wamid.DEF",
            "status": "read",
            "timestamp": "1750263800",
            "recipient_id": "16505551234",
        }
    )
    assert [u.provider_message_id for u in webhooks.parse_statuses(payload)] == [
        "wamid.ABC",
        "wamid.DEF",
    ]


def test_a_failure_carries_metas_error_code() -> None:
    payload = _status_payload(status="failed")
    payload["entry"][0]["changes"][0]["value"]["statuses"][0]["errors"] = [
        {"code": 131026, "title": "Message undeliverable"}
    ]
    update = webhooks.parse_statuses(payload)[0]
    assert update.error_code == "131026"
    assert update.error_detail == "Message undeliverable"


def test_a_malformed_timestamp_does_not_lose_the_status() -> None:
    """The status is the fact worth keeping; losing a delivery receipt over a bad clock would
    be the wrong trade."""
    payload = _status_payload()
    payload["entry"][0]["changes"][0]["value"]["statuses"][0]["timestamp"] = "not-a-time"
    assert webhooks.parse_statuses(payload)[0].status == "delivered"


def test_a_status_missing_its_message_id_is_skipped() -> None:
    payload = _status_payload()
    del payload["entry"][0]["changes"][0]["value"]["statuses"][0]["id"]
    assert webhooks.parse_statuses(payload) == []


# --------------------------------------------------------------------- de-duplication


def test_the_same_event_derives_the_same_id() -> None:
    """Meta redelivers. A stable derived id lets the existing `processed_events` ledger do this
    job rather than a second table, and two ledgers eventually disagree."""
    a = webhooks.parse_statuses(_status_payload())[0]
    b = webhooks.parse_statuses(_status_payload())[0]
    assert a.dedupe_id == b.dedupe_id


def test_different_statuses_on_one_message_are_different_events() -> None:
    """A message legitimately produces sent, then delivered, then read — so the identity is the
    pair, not the message. Keying on the message alone would drop every later status."""
    delivered = webhooks.parse_statuses(_status_payload(status="delivered"))[0]
    read = webhooks.parse_statuses(_status_payload(status="read"))[0]
    assert delivered.dedupe_id != read.dedupe_id


def test_the_same_status_on_different_messages_are_different_events() -> None:
    a = webhooks.parse_statuses(_status_payload(wamid="wamid.A"))[0]
    b = webhooks.parse_statuses(_status_payload(wamid="wamid.B"))[0]
    assert a.dedupe_id != b.dedupe_id


# --------------------------------------------------------------------- inbound messages


def _message_payload(
    *,
    body: str | None = "Hi, is my order ready?",
    message_type: str = "text",
    sender: str = "16505551234",
    wamid: str = "wamid.IN1",
    profile_name: str | None = "Jane Customer",
) -> dict:
    """The shape Meta publishes for an inbound-message webhook."""
    message: dict = {
        "from": sender,
        "id": wamid,
        "timestamp": "1750263773",
        "type": message_type,
    }
    if message_type == "text":
        message["text"] = {"body": body}
    else:
        message["image"] = {"id": "media-1", "mime_type": "image/jpeg"}

    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "15550783881",
            "phone_number_id": "106540352242922",
        },
        "messages": [message],
    }
    if profile_name is not None:
        value["contacts"] = [{"wa_id": sender, "profile": {"name": profile_name}}]

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "102290129340398", "changes": [{"value": value, "field": "messages"}]}],
    }


def test_a_text_message_is_parsed() -> None:
    inbound = webhooks.parse_inbound(_message_payload())[0]
    assert inbound.provider_message_id == "wamid.IN1"
    assert inbound.from_address == "16505551234"
    assert inbound.message_type == "text"
    assert inbound.body == "Hi, is my order ready?"
    assert inbound.profile_name == "Jane Customer"
    assert inbound.occurred_at == datetime.fromtimestamp(1750263773, tz=UTC)


def test_a_non_text_message_is_stored_with_no_body() -> None:
    """Media and interactive shapes are not published on any page consulted, so they are kept
    whole in `raw` and never parsed on a guess."""
    inbound = webhooks.parse_inbound(_message_payload(message_type="image"))[0]
    assert inbound.message_type == "image"
    assert inbound.body is None
    assert inbound.raw["image"]["id"] == "media-1"


def test_an_unknown_type_keeps_its_own_name() -> None:
    inbound = webhooks.parse_inbound(_message_payload(message_type="sticker"))[0]
    assert inbound.message_type == "sticker"


def test_a_message_missing_its_id_or_sender_is_skipped() -> None:
    payload = _message_payload()
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    assert webhooks.parse_inbound(payload) == []


def test_several_messages_in_one_body_are_all_parsed() -> None:
    payload = _message_payload()
    payload["entry"][0]["changes"][0]["value"]["messages"].append(
        {
            "from": "16505551234",
            "id": "wamid.IN2",
            "timestamp": "1750263800",
            "type": "text",
            "text": {"body": "Still there?"},
        }
    )
    assert [m.provider_message_id for m in webhooks.parse_inbound(payload)] == [
        "wamid.IN1",
        "wamid.IN2",
    ]


def test_no_profile_name_leaves_it_none() -> None:
    inbound = webhooks.parse_inbound(_message_payload(profile_name=None))[0]
    assert inbound.profile_name is None


def test_inbound_dedupe_id_is_stable_and_distinct_from_a_status() -> None:
    a = webhooks.parse_inbound(_message_payload())[0]
    b = webhooks.parse_inbound(_message_payload())[0]
    assert a.dedupe_id == b.dedupe_id

    status = webhooks.parse_statuses(_status_payload(wamid="wamid.IN1"))[0]
    assert a.dedupe_id != status.dedupe_id


# --------------------------------------------------------------------- sender canonicalisation


def test_a_bare_meta_sender_becomes_e164() -> None:
    assert webhooks.sender_e164("16505551234") == "+16505551234"


def test_an_implausible_sender_does_not_parse() -> None:
    assert webhooks.sender_e164("not-a-number") is None


def test_an_empty_sender_does_not_parse() -> None:
    assert webhooks.sender_e164("") is None


# --------------------------------------------------------------------- STOP keywords


@pytest.mark.parametrize(
    "body",
    [
        "STOP",
        "stop",
        "  Stop  ",
        "Unsubscribe",
        "Stop promotions",
        "stop_promotions",
        "STOP-MARKETING",
    ],
)
def test_a_stop_keyword_is_recognised_case_and_whitespace_insensitively(body: str) -> None:
    assert webhooks.is_stop_keyword(body)


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "please stop sending me things",
        "STOPPED",
        "cancelled my order",
        # A reply to an order reminder is about the order, not a request to stop all messages.
        "cancel",
        "Cancel",
        "END",
        "QUIT",
    ],
)
def test_a_sentence_using_the_word_is_not_a_stop_request(body: str | None) -> None:
    """A substring match would silently unsubscribe someone for using the word in a sentence."""
    assert not webhooks.is_stop_keyword(body)


# --------------------------------------------------------------------- the service window


def test_a_conversation_with_no_customer_message_has_no_open_window() -> None:
    assert not webhooks.within_service_window(None)


def test_the_window_is_open_just_before_24_hours() -> None:
    last = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    now = last + timedelta(hours=23, minutes=59, seconds=59)
    assert webhooks.within_service_window(last, now=now)


def test_the_window_is_closed_at_exactly_24_hours() -> None:
    last = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    now = last + timedelta(hours=24)
    assert not webhooks.within_service_window(last, now=now)


def test_the_window_is_closed_just_after_24_hours() -> None:
    last = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    now = last + timedelta(hours=24, seconds=1)
    assert not webhooks.within_service_window(last, now=now)


# --------------------------------------------------------------------- START keywords


@pytest.mark.parametrize("body", ["START", "start", "  Start  ", "Unstop", "subscribe", "START."])
def test_a_start_keyword_is_recognised_case_and_whitespace_insensitively(body: str) -> None:
    assert webhooks.is_start_keyword(body)


@pytest.mark.parametrize("body", [None, "", "let's start the meeting", "STARTED"])
def test_a_sentence_using_start_is_not_a_resubscribe_request(body: str | None) -> None:
    assert not webhooks.is_start_keyword(body)


def test_stop_and_start_are_never_both_true() -> None:
    for word in ("STOP", "START", "hello", ""):
        assert not (webhooks.is_stop_keyword(word) and webhooks.is_start_keyword(word))


@pytest.mark.parametrize("body", ["STOP.", "Stop!", "stop,", "STOP;", "stop:"])
def test_stop_survives_trailing_punctuation(body: str) -> None:
    assert webhooks.is_stop_keyword(body)


# --------------------------------------------------------------------- cross-tenant batching


def _batched_payload(entries: list[tuple[str, dict]]) -> dict:
    """Two (or more) WABAs' changes in one body — exactly the shape Meta's own batching produces
    and the shape a payload-wide `phone_number_id` lookup used to misroute."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": f"waba-{i}",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": phone_number_id,
                                "phone_number_id": phone_number_id,
                            },
                            **value,
                        },
                        "field": "messages",
                    }
                ],
            }
            for i, (phone_number_id, value) in enumerate(entries)
        ],
    }


def test_statuses_are_grouped_by_their_own_changes_number_not_the_bodys_first() -> None:
    payload = _batched_payload(
        [
            ("111", {"statuses": [{"id": "wamid.A", "status": "sent", "timestamp": "1"}]}),
            ("222", {"statuses": [{"id": "wamid.B", "status": "delivered", "timestamp": "1"}]}),
        ]
    )
    grouped = webhooks.group_statuses_by_phone(payload)
    assert {p.provider_message_id for p in grouped["111"]} == {"wamid.A"}
    assert {p.provider_message_id for p in grouped["222"]} == {"wamid.B"}
    # The old, unsafe reading: everything flattened under one number.
    assert webhooks.phone_number_id(payload) == "111"


def test_inbound_messages_are_grouped_by_their_own_changes_number() -> None:
    payload = _batched_payload(
        [
            (
                "111",
                {
                    "messages": [
                        {
                            "from": "1",
                            "id": "wamid.IN.A",
                            "timestamp": "1",
                            "type": "text",
                            "text": {"body": "hi"},
                        }
                    ]
                },
            ),
            (
                "222",
                {
                    "messages": [
                        {
                            "from": "2",
                            "id": "wamid.IN.B",
                            "timestamp": "1",
                            "type": "text",
                            "text": {"body": "hi"},
                        }
                    ]
                },
            ),
        ]
    )
    grouped = webhooks.group_inbound_by_phone(payload)
    assert {m.provider_message_id for m in grouped["111"]} == {"wamid.IN.A"}
    assert {m.provider_message_id for m in grouped["222"]} == {"wamid.IN.B"}


def test_a_change_with_no_phone_number_id_is_dropped_not_misrouted() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-0",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "x"},
                            "statuses": [
                                {"id": "wamid.ORPHAN", "status": "sent", "timestamp": "1"}
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    assert webhooks.group_statuses_by_phone(payload) == {}


def test_parse_statuses_still_flattens_across_numbers_for_single_org_callers() -> None:
    """The convenience function tests use — never the webhook route itself — still sees
    everything, which is the point of keeping it around."""
    payload = _batched_payload(
        [
            ("111", {"statuses": [{"id": "wamid.A", "status": "sent", "timestamp": "1"}]}),
            ("222", {"statuses": [{"id": "wamid.B", "status": "sent", "timestamp": "1"}]}),
        ]
    )
    assert {u.provider_message_id for u in webhooks.parse_statuses(payload)} == {
        "wamid.A",
        "wamid.B",
    }
