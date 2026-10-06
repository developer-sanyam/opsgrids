"""Cashfree's wire format, pinned with **independent known answers** (ADR-0032): the money
conversion that must never touch a float, the webhook signature, the status maps and the redaction.

Every expected value below was written down (or computed with `openssl`, for the signature) before
the code that must reproduce it — none is derived by calling the function under test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.providers import cashfree_codec as codec
from app.providers.wire import ProviderRejected

#: `openssl dgst -sha256 -hmac "dev-only-cashfree-secret-key" -binary <ts+body>` then base64.
SECRET = "dev-only-cashfree-secret-key"  # pragma: allowlist secret
TIMESTAMP = "1697191215000"
BODY = (
    b'{"data":{"order":{"order_id":"o-1","order_amount":499.00}},"type":"PAYMENT_SUCCESS_WEBHOOK"}'
)
SIGNATURE = "oBgunN7Wi7UYUwtOkZb2dGfPPZe4sxagr9FnzWMGg/U="  # pragma: allowlist secret


class TestMoneyOutbound:
    @pytest.mark.parametrize(
        ("minor", "text"),
        [
            (0, "0.00"),
            (1, "0.01"),
            (10, "0.10"),
            (99, "0.99"),
            (100, "1.00"),
            (1999, "19.99"),
            (10005, "100.05"),
            (49900, "499.00"),
            (1_500_000, "15000.00"),
        ],
    )
    def test_minor_units_become_the_exact_rupee_decimal(self, minor: int, text: str) -> None:
        assert codec.decimal_text(minor, "INR") == text
        assert codec.minor_to_amount(minor, "INR") == Decimal(text)

    @pytest.mark.parametrize(
        ("amount", "wire"),
        [
            (Decimal("19.99"), b'{"order_amount":19.99}'),
            (Decimal("0.10"), b'{"order_amount":0.10}'),
            (Decimal("499.00"), b'{"order_amount":499.00}'),
        ],
    )
    def test_a_decimal_is_written_as_a_bare_json_number(self, amount: Decimal, wire: bytes) -> None:
        assert codec.dumps({"order_amount": amount}) == wire

    def test_a_float_amount_is_refused_outright(self) -> None:
        """`19.99` as a float is 19.989999999999998: how an amount silently loses a paisa."""
        with pytest.raises(TypeError):
            codec.dumps({"order_amount": 19.99})

    def test_strings_ints_bools_nulls_and_nesting_encode_as_json(self) -> None:
        body = {
            "a": 'x"y',
            "b": 3,
            "c": True,
            "d": None,
            "e": [1, "z"],
            "f": {"g": Decimal("1.50")},
        }
        assert json.loads(codec.dumps(body), parse_float=Decimal) == {
            "a": 'x"y',
            "b": 3,
            "c": True,
            "d": None,
            "e": [1, "z"],
            "f": {"g": Decimal("1.50")},
        }

    @pytest.mark.parametrize("currency", ["USD", "EUR", "JPY", "usd", ""])
    def test_any_currency_but_inr_is_refused_before_any_call(self, currency: str) -> None:
        with pytest.raises(ProviderRejected) as raised:
            codec.minor_to_amount(100, currency)
        assert raised.value.code == "unsupported_currency"

    def test_the_conversion_round_trips_through_the_wire_for_a_swathe_of_amounts(self) -> None:
        for minor in [*range(0, 20_000, 7), 1_999_999, 123_456_789, 9_999_999_999]:
            wire = codec.dumps({"order_amount": codec.minor_to_amount(minor, "INR")})
            parsed = codec.loads(wire)["order_amount"]
            assert codec.amount_to_minor(parsed, "INR") == minor, minor


class TestMoneyInbound:
    @pytest.mark.parametrize(
        ("value", "minor"),
        [
            (Decimal("19.99"), 1999),
            (Decimal("0.10"), 10),
            (Decimal("0.1"), 10),
            (Decimal("1E+2"), 10_000),  # the JSON number 1e2 is a hundred rupees
            (Decimal("100.5"), 10_050),
            (499, 49_900),
            ("499.00", 49_900),
            (" 12.34 ", 1234),
            (Decimal("0"), 0),
        ],
    )
    def test_an_exact_amount_is_integer_minor_units(self, value: Any, minor: int) -> None:
        assert codec.amount_to_minor(value, "INR") == minor

    @pytest.mark.parametrize(
        "value",
        [
            Decimal("0.105"),  # three decimal places of rupees is a defect, not an amount
            Decimal("19.999"),
            "abc",
            "",
            None,
            True,
            False,
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-1"),
            "-0.01",
            Decimal("1E+13"),  # beyond any real amount: a body must not ask for a huge integer
            [1],
            {"a": 1},
        ],
    )
    def test_anything_else_is_none_and_never_a_guess(self, value: Any) -> None:
        assert codec.amount_to_minor(value, "INR") is None

    def test_an_amount_in_another_currency_is_none(self) -> None:
        assert codec.amount_to_minor(Decimal("1.00"), "USD") is None

    def test_a_body_is_parsed_with_decimals_not_floats(self) -> None:
        parsed = codec.loads('{"a": 1e2, "b": 19.99, "c": 3, "d": "4.50"}')
        assert parsed["a"] == Decimal("1E+2") and isinstance(parsed["a"], Decimal)
        assert parsed["b"] == Decimal("19.99") and isinstance(parsed["b"], Decimal)
        assert parsed["c"] == 3 and isinstance(parsed["c"], int)
        assert parsed["d"] == "4.50"

    def test_19_99_never_becomes_1998_paise(self) -> None:
        """The classic: `int(19.99 * 100)` is 1998."""
        assert int(19.99 * 100) == 1998  # the trap, demonstrated
        assert codec.amount_to_minor(codec.loads("19.99"), "INR") == 1999


class TestTheSignature:
    def test_the_known_answer(self) -> None:
        assert codec.sign(SECRET, TIMESTAMP, BODY) == SIGNATURE
        assert codec.verify_signature(SECRET, SIGNATURE, TIMESTAMP, BODY) is True

    def test_a_tampered_body_fails(self) -> None:
        assert codec.verify_signature(SECRET, SIGNATURE, TIMESTAMP, BODY + b" ") is False
        assert (
            codec.verify_signature(SECRET, SIGNATURE, TIMESTAMP, BODY.replace(b"499", b"1"))
            is False
        )

    def test_a_tampered_timestamp_fails(self) -> None:
        assert codec.verify_signature(SECRET, SIGNATURE, "1697191215001", BODY) is False

    def test_the_timestamp_and_body_are_concatenated_with_no_separator(self) -> None:
        assert codec.verify_signature(SECRET, SIGNATURE, TIMESTAMP + ".", BODY) is False

    def test_a_sandbox_secret_does_not_verify_against_a_production_secret(self) -> None:
        production = "dev-only-cashfree-production-key"  # pragma: allowlist secret
        assert codec.verify_signature(production, SIGNATURE, TIMESTAMP, BODY) is False

    @pytest.mark.parametrize(
        ("secret", "signature", "timestamp"),
        [
            (None, SIGNATURE, TIMESTAMP),  # an unset secret verifies nothing at all
            ("", SIGNATURE, TIMESTAMP),
            (SECRET, None, TIMESTAMP),
            (SECRET, "", TIMESTAMP),
            (SECRET, SIGNATURE, None),
            (SECRET, SIGNATURE, ""),
            (SECRET, "☃" * 20, TIMESTAMP),  # non-ASCII must be False, not a TypeError
            (SECRET, SIGNATURE, "☃"),
            (SECRET, SIGNATURE + " ", TIMESTAMP),
            (SECRET, "0" * 44, TIMESTAMP),
        ],
    )
    def test_it_fails_closed_and_never_raises(
        self, secret: Any, signature: Any, timestamp: Any
    ) -> None:
        assert codec.verify_signature(secret, signature, timestamp, BODY) is False


class TestStatusesAndEvents:
    @pytest.mark.parametrize(
        ("status", "state"),
        [("SUCCESS", "captured"), ("FAILED", "failed"), ("PENDING", "created")],
    )
    def test_a_payment_status_maps_into_the_neutral_vocabulary(
        self, status: str, state: str
    ) -> None:
        assert codec.PAYMENT_STATUS_STATES[status] == state

    @pytest.mark.parametrize("status", ["USER_DROPPED", "NOT_ATTEMPTED", "VOID", "CANCELLED", "X"])
    def test_an_abandoned_or_unknown_attempt_is_no_observation_at_all(self, status: str) -> None:
        """A customer closing the page is not a payment failing: it must never become `failed`."""
        item = _payment(status=status)
        assert codec.snapshot_from_payment(item) is None

    @pytest.mark.parametrize(
        ("raw", "name"),
        [
            ("PAYMENT_SUCCESS_WEBHOOK", "PAYMENT_SUCCESS"),
            ("payment_success", "PAYMENT_SUCCESS"),
            ("PAYMENT_FAILED_WEBHOOK", "PAYMENT_FAILED"),
            (None, ""),
            (7, ""),
        ],
    )
    def test_the_event_type_loses_its_suffix_and_its_case(self, raw: Any, name: str) -> None:
        assert codec.event_type({"type": raw}) == name

    def test_only_a_payment_success_asserts_an_outcome(self) -> None:
        assert codec.EVENT_OUTCOMES == {"PAYMENT_SUCCESS": "captured"}

    @pytest.mark.parametrize(
        ("order", "paid", "expected"),
        [
            (49900, 49900, 49900),  # the usual case
            (49900, 50900, 49900),  # a surcharge: accepted at the Order's amount
            (49900, 40000, 40000),  # short: reported as what it is, so the reconciler sees it
            (None, 49900, 49900),  # no Order amount in the body: the payment's stands
            (49900, None, None),  # money that cannot be seen to have moved is not a capture
            (None, None, None),
        ],
    )
    def test_the_amount_a_payment_is_reconciled_at(
        self, order: int | None, paid: int | None, expected: int | None
    ) -> None:
        assert codec.event_amount(order, paid) == expected

    def test_a_snapshot_keeps_ids_status_money_and_the_error_code_only(self) -> None:
        item = _payment(
            status="FAILED",
            extra={
                "error_details": {"error_code": "card_declined", "error_description": "free text"}
            },
        )
        snapshot = codec.snapshot_from_payment(item)
        assert snapshot is not None
        assert (snapshot.provider_payment_id, snapshot.status) == ("3001", "failed")
        assert (snapshot.amount_minor, snapshot.currency) == (49900, "INR")
        assert snapshot.error_code == "card_declined" and snapshot.error_description is None
        assert "customer@example.com" not in repr(snapshot) and "upi" not in repr(snapshot)

    def test_a_snapshot_with_an_unreadable_amount_is_none(self) -> None:
        assert codec.snapshot_from_payment(_payment(amount=Decimal("0.105"))) is None
        assert codec.snapshot_from_payment(_payment(amount=None)) is None

    def test_a_surcharged_snapshot_is_reported_at_the_orders_amount(self) -> None:
        snapshot = codec.snapshot_from_payment(_payment(amount=Decimal("509.00")))
        assert snapshot is not None and snapshot.amount_minor == 49900

    def test_a_short_snapshot_is_reported_as_short(self) -> None:
        snapshot = codec.snapshot_from_payment(_payment(amount=Decimal("400.00")))
        assert snapshot is not None and snapshot.amount_minor == 40000

    def test_epoch_seconds_from_an_iso_timestamp(self) -> None:
        expected = int(datetime(2021, 10, 13, 4, 45, 15, tzinfo=UTC).timestamp())
        assert codec.epoch_of("2021-10-13T10:15:15+05:30") == expected
        assert codec.epoch_of("2021-10-13T04:45:15Z") == expected
        assert codec.epoch_of("2021-10-13T04:45:15") == expected  # naive is read as UTC
        for bad in ("", "yesterday", None, 12, "2021-13-45"):
            assert codec.epoch_of(bad) is None


class TestRedaction:
    def test_only_the_allow_list_survives_and_no_decimal_remains(self) -> None:
        stored = codec.redact_webhook_body(_webhook())
        assert stored == {
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "event_time": "2021-10-13T10:15:15+05:30",
            "data": {
                "order": {
                    "order_id": "ord-1",
                    "order_currency": "INR",
                    "order_tags": {"organization_id": "org-1", "charge_id": "ch-1"},
                    "order_amount": "499.00",
                },
                "payment": {
                    "cf_payment_id": "3001",
                    "payment_status": "SUCCESS",
                    "payment_currency": "INR",
                    "payment_time": "2021-10-13T10:15:10+05:30",
                    "payment_amount": "499.00",
                },
            },
        }
        json.dumps(stored)  # JSONB-safe: no Decimal anywhere

    def test_no_customer_or_payment_method_detail_is_stored(self) -> None:
        text = json.dumps(codec.redact_webhook_body(_webhook()))
        for pii in (
            "Asha Customer",
            "asha@example.com",
            "9876543210",
            "asha@okbank",
            "4111",
            "HDFC",
            "bank_reference",
            "payment_message",
        ):
            assert pii not in text

    def test_a_field_cashfree_adds_tomorrow_is_dropped_too(self) -> None:
        body = _webhook()
        body["data"]["payment"]["brand_new_field"] = "someone@example.com"
        body["data"]["customer_details"]["new_pii"] = "x"
        assert "brand_new_field" not in json.dumps(codec.redact_webhook_body(body))

    def test_redaction_is_idempotent(self) -> None:
        once = codec.redact_webhook_body(_webhook())
        assert codec.redact_webhook_body(once) == once

    def test_an_unreadable_amount_is_dropped_not_guessed(self) -> None:
        body = _webhook()
        body["data"]["order"]["order_amount"] = Decimal("0.105")
        assert "order_amount" not in codec.redact_webhook_body(body)["data"]["order"]

    def test_a_non_object_body_reduces_to_an_empty_envelope(self) -> None:
        assert codec.redact_webhook_body({"data": "nope"}) == {
            "data": {"order": {"order_tags": {}}, "payment": {}}
        }


def _payment(
    *,
    status: str = "SUCCESS",
    amount: Decimal | str | None = "default",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "cf_payment_id": 3001,
        "payment_status": status,
        "payment_currency": "INR",
        "order_amount": Decimal("499.00"),
        "payment_time": "2021-10-13T10:15:10+05:30",
        "payment_method": {"upi": {"upi_id": "asha@okbank"}},
        "customer_email": "customer@example.com",
    }
    if amount == "default":
        item["payment_amount"] = Decimal("499.00")
    elif amount is not None:
        item["payment_amount"] = amount
    item.update(extra or {})
    return item


def _webhook() -> dict[str, Any]:
    return {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "event_time": "2021-10-13T10:15:15+05:30",
        "data": {
            "order": {
                "order_id": "ord-1",
                "order_amount": Decimal("499.00"),
                "order_currency": "INR",
                "order_tags": {"organization_id": "org-1", "charge_id": "ch-1"},
            },
            "payment": {
                "cf_payment_id": 3001,
                "payment_status": "SUCCESS",
                "payment_amount": Decimal("499.00"),
                "payment_currency": "INR",
                "payment_message": "Transaction successful",
                "payment_time": "2021-10-13T10:15:10+05:30",
                "bank_reference": "HDFC-4111-REF",
                "payment_method": {"upi": {"upi_id": "asha@okbank"}},
            },
            "customer_details": {
                "customer_name": "Asha Customer",
                "customer_email": "asha@example.com",
                "customer_phone": "9876543210",
            },
        },
    }
