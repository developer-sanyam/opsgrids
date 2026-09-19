"""Webhook authenticity and parsing — the one surface that accepts input from outside.

There is no token behind this endpoint; Meta cannot hold one. So the only thing separating a
genuine delivery receipt from a forged one is the signature, and these tests are mostly about
refusing things: a tampered body, a wrong secret, a missing header, a replayed event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from app import webhooks

SECRET = "an-app-secret"


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
    assert not webhooks.verify_signature(body, _sign(body, "someone-elses-secret"), SECRET)


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
