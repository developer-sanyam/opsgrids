"""Webhook identity and permanent-condition verdicts — the pure halves, no database.

The empty string as a dedupe key is the defect these guard (S14): Razorpay's body carries no
top-level `id`, the adapter used to default the missing one to `''`, and `(organization_id, '')` is
one key for every event an organization ever receives — the first is recorded, the rest are dropped
as "duplicates" and answered 200, so the provider stops retrying and the capture is never
reconciled. `resolve_event_id` may return an id or None. It may never return `''`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app import domain
from app.models import INBOX_CONFLICT, INBOX_IGNORED
from app.providers.base import MalformedWebhook, WebhookEvent
from app.providers.razorpay import RazorpayProvider
from tests.helpers import razorpay_webhook_body


def _event(**overrides: Any) -> WebhookEvent:
    """A parsed webhook with nothing identifying it unless the test says so."""
    fields: dict[str, Any] = {
        "provider": "razorpay",
        "provider_event_id": "",
        "event_name": "",
        "outcome_state": "unknown",
        "organization_id": None,
        "charge_id": None,
        "provider_payment_id": None,
    }
    fields.update(overrides)
    return WebhookEvent(**fields)


def _identifiable(**overrides: Any) -> WebhookEvent:
    fields: dict[str, Any] = {
        "event_name": "payment.captured",
        "entity_id": "pay_abc123",
        "created_at": 1_700_000_000,
    }
    fields.update(overrides)
    return _event(**fields)


def _provider() -> RazorpayProvider:
    return RazorpayProvider(key_id=None, key_secret=None, webhook_secret="whsec-test")


class TestTheEmptyStringIsNeverAKey:
    @pytest.mark.parametrize("header", ["", "   ", "\t", None])
    def test_a_blank_header_and_a_blank_body_id_with_nothing_to_derive_from_yield_no_id(
        self, header: str | None
    ) -> None:
        assert domain.resolve_event_id(header=header, event=_event()) is None

    @pytest.mark.parametrize("header", ["", "   ", "\t", None])
    def test_a_blank_header_falls_through_to_the_derived_id_never_to_blank(
        self, header: str | None
    ) -> None:
        resolved = domain.resolve_event_id(header=header, event=_identifiable())
        assert resolved is not None and resolved != ""
        assert resolved.startswith(domain.DERIVED_EVENT_ID_PREFIX)

    @pytest.mark.parametrize("value", ["", " ", "\n", "\x7f", "with space", "é" * 3, "x" * 121])
    def test_an_unusable_id_is_none_not_a_truncation_or_a_blank(self, value: str) -> None:
        assert domain.usable_event_id(value) is None

    def test_a_real_razorpay_body_reports_no_id_rather_than_inventing_one(self) -> None:
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_abc123",
            organization_id=uuid.uuid4(),
            charge_id=uuid.uuid4(),
            body_id=False,
        )
        assert "id" not in json.loads(body), "the fixture must be the real (id-less) shape"
        parsed = _provider().parse_webhook(body=body)
        assert parsed.provider_event_id == ""
        # ...and the service therefore never gets '' as a key: with no header it derives one.
        resolved = domain.resolve_event_id(header=None, event=parsed)
        assert resolved is not None and resolved.startswith(domain.DERIVED_EVENT_ID_PREFIX)


class TestTheResolutionOrder:
    def test_the_header_wins_over_the_body_and_the_digest(self) -> None:
        event = _identifiable(provider_event_id="evt_from_body")
        assert domain.resolve_event_id(header="evt_from_header", event=event) == "evt_from_header"

    def test_the_body_id_is_used_when_there_is_no_header(self) -> None:
        event = _identifiable(provider_event_id="evt_from_body")
        assert domain.resolve_event_id(header=None, event=event) == "evt_from_body"

    def test_the_digest_is_the_last_resort(self) -> None:
        resolved = domain.resolve_event_id(header=None, event=_identifiable())
        assert resolved is not None and resolved.startswith("derived:")
        # A sha256 hexdigest after the prefix, and short enough for the column.
        assert len(resolved) == len("derived:") + 64 <= domain.EVENT_ID_MAX_LENGTH

    def test_a_header_cannot_pose_as_a_derived_key(self) -> None:
        """The two namespaces are disjoint: a delivery header that *looks* derived is not trusted
        as one, so it cannot collide with (and suppress) a real derived id."""
        forged = "derived:" + "0" * 64
        resolved = domain.resolve_event_id(header=forged, event=_identifiable())
        assert resolved != forged
        assert resolved is not None and resolved.startswith("derived:")


class TestTheDerivedId:
    def test_it_is_deterministic(self) -> None:
        assert domain.derive_event_id(_identifiable()) == domain.derive_event_id(_identifiable())

    @pytest.mark.parametrize(
        "change",
        [
            {"event_name": "payment.failed"},
            {"entity_id": "pay_other"},
            {"created_at": 1_700_000_001},
            {"provider": "stripe"},
        ],
    )
    def test_any_identifying_field_changes_it(self, change: dict[str, Any]) -> None:
        assert domain.derive_event_id(_identifiable(**change)) != domain.derive_event_id(
            _identifiable()
        )

    @pytest.mark.parametrize(
        "change", [{"event_name": ""}, {"entity_id": None}, {"entity_id": ""}, {"created_at": None}]
    )
    def test_it_refuses_to_derive_from_less_than_all_three(self, change: dict[str, Any]) -> None:
        """An id derived from fewer fields folds distinct events together — the very failure."""
        assert domain.derive_event_id(_identifiable(**change)) is None

    def test_zero_is_a_timestamp(self) -> None:
        assert domain.derive_event_id(_identifiable(created_at=0)) is not None


class TestTheAdapterReadsARealBodyDefensively:
    def test_it_reports_what_the_event_is_about_and_what_it_was_for(self) -> None:
        body = json.dumps(
            {
                "entity": "event",
                "account_id": "acc_1",
                "event": "payment.captured",
                "created_at": 1_700_000_123,
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_1",
                            "amount": 4900,
                            "currency": "INR",
                            "order_id": "order_1",
                            "notes": {"organization_id": "o", "charge_id": "c"},
                        }
                    }
                },
            }
        ).encode()
        event = _provider().parse_webhook(body=body)
        assert event.entity_id == "pay_1"
        assert event.created_at == 1_700_000_123
        assert event.amount_minor == 4900 and event.currency == "INR"
        assert event.provider_event_id == ""

    def test_a_refund_event_identifies_itself_by_the_refund_not_the_payment(self) -> None:
        body = json.dumps(
            {
                "event": "refund.processed",
                "created_at": 5,
                "payload": {
                    "payment": {"entity": {"id": "pay_1"}},
                    "refund": {"entity": {"id": "rfnd_1"}},
                },
            }
        ).encode()
        assert _provider().parse_webhook(body=body).entity_id == "rfnd_1"

    def test_a_numeric_note_is_kept_as_text_so_a_malformed_organization_is_seen(self) -> None:
        """A number in `organization_id` is *present and wrong* — a platform-side bug worth an
        error log — not absent; a boolean or null is absent."""
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "notes": {"organization_id": 12345, "charge_id": 7.5, "other": True}
                        }
                    }
                },
            }
        ).encode()
        event = _provider().parse_webhook(body=body)
        assert event.organization_id == "12345" and event.charge_id == "7.5"

        for junk in (True, None, [], {}, ""):
            body = json.dumps(
                {"payload": {"payment": {"entity": {"notes": {"organization_id": junk}}}}}
            ).encode()
            assert _provider().parse_webhook(body=body).organization_id is None

    def test_an_empty_notes_list_is_no_notes(self) -> None:
        """Razorpay returns `notes: []` (a list) for a payment with none."""
        body = json.dumps(
            {"event": "payment.captured", "payload": {"payment": {"entity": {"notes": []}}}}
        ).encode()
        event = _provider().parse_webhook(body=body)
        assert event.organization_id is None and event.charge_id is None

    @pytest.mark.parametrize(
        "payload", [None, [], "x", 7, {"payment": None}, {"payment": {"entity": []}}]
    )
    def test_a_strangely_shaped_payload_is_not_a_crash(self, payload: Any) -> None:
        body = json.dumps({"event": "payment.captured", "payload": payload}).encode()
        event = _provider().parse_webhook(body=body)
        assert event.entity_id is None and event.organization_id is None

    def test_a_boolean_is_not_an_amount_or_a_timestamp(self) -> None:
        body = json.dumps(
            {
                "event": "payment.captured",
                "created_at": True,
                "payload": {"payment": {"entity": {"amount": True}}},
            }
        ).encode()
        event = _provider().parse_webhook(body=body)
        assert event.amount_minor is None and event.created_at is None

    def test_an_over_long_event_name_fits_the_column(self) -> None:
        body = json.dumps({"event": "e" * 500}).encode()
        assert len(_provider().parse_webhook(body=body).event_name) == 80

    @pytest.mark.parametrize("body", [b"not json", b"", b"[1, 2]", b'"str"', b"null", b"\xff\xfe"])
    def test_a_body_that_is_not_an_object_is_malformed_not_a_crash(self, body: bytes) -> None:
        with pytest.raises(MalformedWebhook):
            _provider().parse_webhook(body=body)


class TestTheVerdictsOnAPaymentThatIsNotTheCharge:
    def test_agreement_is_no_verdict(self) -> None:
        assert (
            domain.contradiction(
                charge_amount_minor=1000,
                charge_currency="USD",
                event_amount_minor=1000,
                event_currency="usd",
            )
            is None
        )

    def test_another_amount_is_a_conflict_with_both_amounts_named(self) -> None:
        verdict = domain.contradiction(
            charge_amount_minor=1000,
            charge_currency="USD",
            event_amount_minor=999,
            event_currency="USD",
        )
        assert verdict is not None
        assert verdict.disposition == INBOX_CONFLICT
        assert verdict.reason == domain.REASON_AMOUNT_MISMATCH
        assert verdict.facts == {"charge_amount_minor": 1000, "asserted_amount_minor": 999}

    def test_another_currency_is_a_conflict_and_outranks_the_amount(self) -> None:
        verdict = domain.contradiction(
            charge_amount_minor=1000,
            charge_currency="USD",
            event_amount_minor=1,
            event_currency="EUR",
        )
        assert verdict is not None and verdict.reason == domain.REASON_CURRENCY_MISMATCH

    @pytest.mark.parametrize(("amount", "currency"), [(None, "USD"), (1000, None), (None, None)])
    def test_what_the_body_does_not_say_is_not_a_mismatch(
        self, amount: int | None, currency: str | None
    ) -> None:
        assert (
            domain.contradiction(
                charge_amount_minor=1000,
                charge_currency="USD",
                event_amount_minor=amount,
                event_currency=currency,
            )
            is None
        )

    def test_a_charge_that_is_not_ours_is_ignored_not_a_conflict(self) -> None:
        verdict = domain.unknown_charge()
        assert verdict.disposition == INBOX_IGNORED
        assert verdict.reason == domain.REASON_UNKNOWN_CHARGE

    def test_an_illegal_move_names_both_states_and_nothing_else(self) -> None:
        verdict = domain.illegal_transition("captured", "failed")
        assert verdict.disposition == INBOX_CONFLICT
        assert verdict.facts == {"charge_state": "captured", "asserted_state": "failed"}

    def test_a_verdict_is_never_an_http_error(self) -> None:
        """The whole point: a signed webhook's permanent condition must not be able to reach the
        HTTP layer as a 4xx the provider would retry."""
        from platform_core.errors import ProblemError

        assert not issubclass(domain.WebhookNotApplied, ProblemError)
