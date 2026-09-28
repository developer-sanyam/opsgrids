"""Proving a WhatsApp connection against Meta's Graph API — without a network.

`verify_ownership` is what stands between "an operator typed a phone number" and "an operator
proved they control it". Every failure mode here — a token that cannot read the number, one that
can but the number is not listed under the named WABA, an unreachable Graph API — must refuse the
connection; only both checks succeeding proves ownership.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.whatsapp_provider import WhatsAppOwnershipError, verify_ownership

WABA_ID = "102290129340398"
PHONE_NUMBER_ID = "106540352242922"
TOKEN = "ci-only-access-token"  # pragma: allowlist secret


def _settings() -> Settings:
    return Settings(whatsapp_api_base="https://graph.invalid/v25.0")


async def _verify(handler, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the real check against a stubbed transport — no socket is opened."""
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    await verify_ownership(
        _settings(), waba_id=WABA_ID, phone_number_id=PHONE_NUMBER_ID, access_token=TOKEN
    )


def _number_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": PHONE_NUMBER_ID})


def _waba_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": PHONE_NUMBER_ID}]})


def _handler(number=_number_ok, waba=_waba_ok):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/{PHONE_NUMBER_ID}"):
            return number(request)
        if request.url.path.endswith("/phone_numbers"):
            return waba(request)
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


async def test_a_token_that_controls_the_number_and_the_waba_is_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _verify(_handler(), monkeypatch)  # does not raise


async def test_the_token_is_sent_as_a_bearer_header_never_a_query_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        assert TOKEN not in str(request.url)
        if request.url.path.endswith(f"/{PHONE_NUMBER_ID}"):
            return _number_ok(request)
        return _waba_ok(request)

    await _verify(handler, monkeypatch)
    assert seen == [f"Bearer {TOKEN}", f"Bearer {TOKEN}"]


async def test_a_token_that_cannot_read_the_number_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def number_denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid OAuth access token"}})

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(_handler(number=number_denied), monkeypatch)


async def test_meta_s_own_reason_is_added_to_the_refusal_so_the_person_knows_what_to_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal used to read identically whatever Meta actually said. `error_user_msg` is
    the one field Meta documents as safe to show a person — never `message`, which is for a
    developer reading logs."""

    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Error validating access token: Session has expired",
                    "error_user_msg": "Your access token has expired. Generate a new one.",
                }
            },
        )

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(_handler(number=expired), monkeypatch)
    assert "Your access token has expired. Generate a new one." in str(excinfo.value)
    assert "Session has expired" not in str(excinfo.value)


async def test_the_waba_call_s_own_reason_is_added_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_permission(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "(#200) Requires whatsapp_business_management permission",
                    "error_user_msg": "This app does not have permission for this action.",
                }
            },
        )

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(_handler(waba=missing_permission), monkeypatch)
    assert "This app does not have permission for this action." in str(excinfo.value)


async def test_a_developer_only_message_is_not_shown_as_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`message` is for a developer reading logs, not a person connecting an account: with no
    `error_user_msg`, the refusal still reads as one plain sentence."""

    def developer_message_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid OAuth access token"}})

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(_handler(number=developer_message_only), monkeypatch)
    assert str(excinfo.value) == "this access token could not read that phone number from Meta"


async def test_a_blank_reason_falls_back_to_the_plain_sentence_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blank_user_msg(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"error_user_msg": "   "}})

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(_handler(waba=blank_user_msg), monkeypatch)
    assert str(excinfo.value) == (
        "this access token could not list that WhatsApp Business Account's numbers"
    )


async def test_a_mismatched_number_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Meta answered, but not about the number that was asked for — never trusted on faith."""

    def wrong_id(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "someone-elses-number"})

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(_handler(number=wrong_id), monkeypatch)


async def test_a_number_not_listed_under_the_waba_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token can read the number directly but the WABA it is claimed to belong to does not
    list it — exactly the shape of a number borrowed from one WABA and claimed under another."""

    def other_numbers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "a-different-number"}]})

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(_handler(waba=other_numbers), monkeypatch)


async def test_the_waba_call_failing_is_refused_even_though_the_number_call_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def waba_denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "refused"}})

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(_handler(waba=waba_denied), monkeypatch)


async def test_an_unrecognisable_body_is_refused_not_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(_handler(number=not_json), monkeypatch)


async def test_an_unreachable_graph_api_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(WhatsAppOwnershipError):
        await _verify(handler, monkeypatch)


async def test_an_unreachable_graph_api_never_echoes_the_underlying_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old message interpolated the `httpx` exception's own text — which for a connection
    failure can carry a hostname, a proxy's own error page, or other internal detail that has no
    business reaching whoever is connecting a WhatsApp number. The message is now fixed."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-internal-proxy.example.corp refused the connection")

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(handler, monkeypatch)
    assert str(excinfo.value) == "could not reach Meta to verify ownership"
    assert "secret-internal-proxy" not in str(excinfo.value)


async def test_an_invalid_url_is_refused_with_the_same_generic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`httpx.InvalidURL` is raised while *constructing* the request, before any socket opens,
    and is not a subclass of `httpx.HTTPError` — an unguarded call would let it escape this
    function entirely rather than being reported as the ownership failure it is."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("malformed URL")

    with pytest.raises(WhatsAppOwnershipError) as excinfo:
        await _verify(handler, monkeypatch)
    assert str(excinfo.value) == "could not reach Meta to verify ownership"
