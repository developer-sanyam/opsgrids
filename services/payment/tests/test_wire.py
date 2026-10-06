"""The provider-neutral wire (`app/providers/wire.py`) and the Razorpay HTTP wire built on it:
the three-way error taxonomy, the overall call deadline, the credentials latch, and the promises
that make a live wire safe to run — no secret in any repr or exception, no redirect followed, no
unbounded body. Everything runs against `httpx.MockTransport`: no network, no keys.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.providers.razorpay_wire import RazorpayHttpWire
from app.providers.wire import (
    CREDENTIALS_LATCH_SECONDS,
    MAX_DECLARED_CALL_BUDGET_SECONDS,
    WIRE_MAX_RESPONSE_BYTES,
    CredentialsLatch,
    Deadline,
    ProviderApiError,
    ProviderMisconfigured,
    ProviderRejected,
    ProviderTransient,
    call_deadline,
    classify_status,
    current_deadline,
    ensure_deadline,
)

KEY_ID = "rzp_test_WireTest0001"
KEY_SECRET = "dev-only-wire-key-secret"  # pragma: allowlist secret
DESCRIPTION = "this-description-must-never-be-kept-9f3a"


def _wire(handler: Any) -> RazorpayHttpWire:
    return RazorpayHttpWire(
        key_id=KEY_ID, key_secret=KEY_SECRET, transport=httpx.MockTransport(handler)
    )


def _answer(status: int, body: Any = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {})

    return handler


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestClassification:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ProviderMisconfigured),
            (403, ProviderMisconfigured),
            (400, ProviderRejected),
            (404, ProviderRejected),
            (409, ProviderRejected),
            (422, ProviderRejected),
            (408, ProviderTransient),
            (425, ProviderTransient),
            (429, ProviderTransient),
            (500, ProviderTransient),
            (502, ProviderTransient),
            (503, ProviderTransient),
        ],
    )
    def test_a_status_is_classified_by_its_class(
        self, status: int, expected: type[ProviderApiError]
    ) -> None:
        assert classify_status(status) is expected
        with pytest.raises(expected) as raised:
            _wire(_answer(status, {"error": {"code": "BAD_REQUEST_ERROR"}})).request(
                "GET", "/orders"
            )
        assert raised.value.status_code == status and raised.value.code == "BAD_REQUEST_ERROR"

    def test_the_invalid_key_answer_is_misconfigured_on_whatever_status_it_comes_with(self) -> None:
        """Razorpay's common-errors page does not say what status a bad key gets. The description is
        matched exactly (a boolean, then discarded), so a `400` carrying it is still the platform's
        bad key — never a rejection of the charge."""
        body = {
            "error": {"code": "BAD_REQUEST_ERROR", "description": "The api key provided is invalid"}
        }
        for status in (400, 401, 403):
            with pytest.raises(ProviderMisconfigured):
                _wire(_answer(status, body)).request("GET", "/orders")

    def test_another_400_description_is_a_plain_rejection(self) -> None:
        body = {
            "error": {"code": "BAD_REQUEST_ERROR", "description": "Order amount less than minimum"}
        }
        with pytest.raises(ProviderRejected):
            _wire(_answer(400, body)).request("POST", "/orders", json={"amount": 1})

    def test_the_description_is_never_kept_and_no_credential_is_in_the_error(self) -> None:
        body = {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": f"{DESCRIPTION} {KEY_ID} {KEY_SECRET}",
            }
        }
        with pytest.raises(ProviderRejected) as raised:
            _wire(_answer(400, body)).request("POST", "/orders", json={"amount": 1})
        text = str(raised.value) + repr(raised.value) + repr(raised.value.args)
        for secret in (DESCRIPTION, KEY_ID, KEY_SECRET):
            assert secret not in text

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (httpx.ConnectError("refused"), "connect_error"),
            (httpx.ReadTimeout("slow"), "timeout"),
            (httpx.ConnectTimeout("slow"), "timeout"),
            (httpx.RemoteProtocolError("garbled"), "transport_error"),
        ],
    )
    def test_a_transport_failure_is_transient_with_a_stable_code(
        self, error: Exception, code: str
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error

        with pytest.raises(ProviderTransient) as raised:
            _wire(handler).request("GET", "/orders")
        assert raised.value.code == code and raised.value.status_code == 0

    def test_a_success_that_cannot_be_read_is_transient_not_a_success(self) -> None:
        """Whatever the call was, it may have been done: an unreadable 200 is unknown."""
        with pytest.raises(ProviderTransient) as raised:
            _wire(lambda r: httpx.Response(200, content=b"<html>not json</html>")).request(
                "POST", "/orders", json={"amount": 100}
            )
        assert raised.value.code == "unreadable_response"

    def test_a_json_answer_that_is_not_an_object_is_transient_for_razorpay(self) -> None:
        with pytest.raises(ProviderTransient) as raised:
            _wire(_answer(200, [1, 2])).request("GET", "/orders")
        assert raised.value.code == "unexpected_response"

    def test_an_error_body_that_is_not_json_still_classifies_by_status(self) -> None:
        with pytest.raises(ProviderTransient) as raised:
            _wire(lambda r: httpx.Response(502, content=b"<html>bad gateway</html>")).request(
                "GET", "/orders"
            )
        assert raised.value.status_code == 502 and raised.value.code is None


class TestWhatTheWireNeverDoes:
    def test_no_credential_is_in_a_repr(self) -> None:
        wire = _wire(_answer(200))
        assert KEY_SECRET not in repr(wire) and KEY_ID not in repr(wire)

    def test_credentials_go_in_headers_only(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        _wire(handler).request("POST", "/orders", json={"amount": 100})
        (request,) = seen
        assert KEY_SECRET not in str(request.url) and KEY_SECRET.encode() not in request.content
        assert request.headers["Authorization"].startswith("Basic ")

    def test_a_redirect_is_never_followed(self) -> None:
        """Credentials must not ride along to wherever a `302` points."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

        with pytest.raises(ProviderTransient):
            _wire(handler).request("GET", "/orders")
        assert calls == ["https://api.razorpay.com/v1/orders"]

    def test_a_response_larger_than_the_cap_is_refused(self) -> None:
        big = b"x" * (WIRE_MAX_RESPONSE_BYTES + 1)
        with pytest.raises(ProviderTransient) as raised:
            _wire(lambda r: httpx.Response(200, content=big)).request("GET", "/orders")
        assert raised.value.code == "response_too_large"


class TestTheOverallDeadline:
    def test_a_request_inherits_what_remains_of_the_call(self) -> None:
        clock = Clock()
        timeouts: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timeouts.append(dict(request.extensions["timeout"]))
            return httpx.Response(200, json={"items": []})

        wire = _wire(handler)
        with call_deadline(3.0, clock=clock):
            wire.request("GET", "/orders")
        assert timeouts[0]["connect"] == pytest.approx(2.0)
        assert timeouts[0]["read"] == pytest.approx(3.0)  # 5s per request, clamped to the 3s left
        clock.now += 0  # (no time passed: the clamp is what is asserted)

    def test_without_a_deadline_the_per_request_timeouts_apply(self) -> None:
        timeouts: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timeouts.append(dict(request.extensions["timeout"]))
            return httpx.Response(200, json={})

        _wire(handler).request("GET", "/orders")
        assert timeouts[0]["connect"] == 2.0 and timeouts[0]["read"] == 5.0

    def test_a_post_refuses_to_start_with_less_than_one_full_timeout_left(self) -> None:
        """The create that begins and is then abandoned by the caller is the second payment the
        lookup-before-create design exists to prevent. Nothing is sent."""
        clock = Clock()
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            return httpx.Response(200, json={"id": "order_1"})

        wire = _wire(handler)
        with call_deadline(6.9, clock=clock), pytest.raises(ProviderTransient) as raised:
            wire.request("POST", "/orders", json={"amount": 100})
        assert raised.value.code == "deadline_exceeded" and calls == []
        with call_deadline(7.0, clock=clock):
            wire.request("POST", "/orders", json={"amount": 100})
        assert calls == ["POST"]

    def test_a_get_may_start_with_less_than_a_full_timeout_left_but_not_with_none(self) -> None:
        clock = Clock()
        wire = _wire(_answer(200, {"items": []}))
        with call_deadline(1.0, clock=clock):
            wire.request("GET", "/orders")
        with call_deadline(1.0, clock=clock):
            clock.now += 1.0
            with pytest.raises(ProviderTransient) as raised:
                wire.request("GET", "/orders")
        assert raised.value.code == "deadline_exceeded"

    def test_a_slow_trickle_cannot_outlast_the_deadline(self) -> None:
        """An HTTP read timeout restarts whenever a byte arrives; the deadline does not."""
        clock = Clock()

        class Trickle(httpx.SyncByteStream):
            def __iter__(self) -> Iterator[bytes]:
                for chunk in (b'{"it', b'ems"', b": []}"):
                    clock.now += 2.0  # each chunk arrives inside its own read timeout
                    yield chunk

        wire = _wire(lambda r: httpx.Response(200, stream=Trickle()))
        with call_deadline(5.0, clock=clock), pytest.raises(ProviderTransient) as raised:
            wire.request("GET", "/orders")
        assert raised.value.code == "deadline_exceeded"

    def test_the_deadline_is_a_context_that_nests_and_restores(self) -> None:
        assert current_deadline() is None
        with call_deadline(10.0) as outer:
            assert current_deadline() is outer
            with ensure_deadline(99.0) as inner:
                assert inner is outer  # a running call is not given a second, later deadline
            assert current_deadline() is outer
        assert current_deadline() is None
        with ensure_deadline(5.0) as created:
            assert created is not None and current_deadline() is created

    def test_the_deadline_follows_a_call_onto_a_worker_thread(self) -> None:
        import asyncio

        async def run() -> float | None:
            with call_deadline(30.0):
                found = await asyncio.to_thread(current_deadline)
            return found.remaining() if found is not None else None

        remaining = asyncio.run(run())
        assert remaining is not None and 0 < remaining <= 30.0

    def test_the_settings_budget_covers_the_worst_declared_wire(self) -> None:
        from app.config import settings

        assert settings().provider_call_timeout_seconds >= MAX_DECLARED_CALL_BUDGET_SECONDS
        assert Deadline(4.0, clock=Clock()).remaining() == 4.0


class TestTheCredentialsLatch:
    def test_the_first_trip_pages_and_the_rest_do_not(self) -> None:
        clock = Clock()
        latch = CredentialsLatch(clock=clock)
        assert latch.tripped("razorpay") is False
        assert latch.trip("razorpay") is True
        assert latch.tripped("razorpay") is True and latch.tripped("cashfree") is False
        assert latch.trip("razorpay") is False

    def test_it_lapses_and_the_next_call_is_a_probe(self) -> None:
        clock = Clock()
        latch = CredentialsLatch(clock=clock)
        latch.trip("razorpay")
        clock.now += CREDENTIALS_LATCH_SECONDS + 1
        assert latch.tripped("razorpay") is False
        assert latch.trip("razorpay") is True

    def test_reset_clears_one_or_all(self) -> None:
        latch = CredentialsLatch(clock=Clock())
        latch.trip("razorpay")
        latch.trip("cashfree")
        latch.reset("razorpay")
        assert latch.tripped("razorpay") is False and latch.tripped("cashfree") is True
        latch.reset()
        assert latch.tripped("cashfree") is False


def test_error_messages_are_a_provider_a_status_and_a_code_and_nothing_else() -> None:
    error = ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR")
    assert str(error) == "razorpay answered 400 (BAD_REQUEST_ERROR)"
    assert json.dumps(str(ProviderTransient("cashfree", 0, "timeout"))) == (
        '"cashfree answered 0 (timeout)"'
    )
