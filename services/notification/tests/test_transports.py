"""Transport classification, and the refusal to start half-configured.

The distinction these tests protect is the one the dispatcher acts on: a refusal must not be
retried and an outage must be. Getting it backwards either drops messages silently or retries a
bad address forever.

Nothing here opens a socket. The WhatsApp cases drive the real client against a stubbed
transport, so the status-code mapping is tested without a network.
"""

from __future__ import annotations

import httpx
import pytest

from app import transports
from app.config import Settings

# --------------------------------------------------------------------- the default


async def test_log_transport_sends_nothing_and_reports_a_receipt() -> None:
    receipt = await transports.LogTransport("email").send(
        transports.Outgoing(to_address="a@b.com", subject="Hi", body="Body")
    )
    assert receipt.provider == "log"


async def test_recording_transport_keeps_what_it_was_given() -> None:
    t = transports.RecordingTransport()
    await t.send(transports.Outgoing(to_address="a@b.com", subject="S", body="B"))
    assert [m.to_address for m in t.sent] == ["a@b.com"]


# --------------------------------------------------------------------- build()


def test_build_defaults_to_the_log_transport() -> None:
    """A service started without credentials must be inert rather than half-configured."""
    built = transports.build(Settings())
    assert built["email"].name == "log"
    assert built["whatsapp"].name == "log"


def test_build_refuses_whatsapp_without_credentials() -> None:
    """A service that quietly stops sending looks exactly like one with nothing to send, and
    only a customer finds the difference. So this is a startup failure, not a downgrade."""
    cfg = Settings(whatsapp_transport="whatsapp_cloud")
    with pytest.raises(ValueError, match="refusing to start half-configured"):
        transports.build(cfg)


def test_build_refuses_an_unknown_transport() -> None:
    with pytest.raises(ValueError, match="Unknown email transport"):
        transports.build(Settings(email_transport="carrier_pigeon"))


def test_build_wires_smtp_when_asked() -> None:
    built = transports.build(Settings(email_transport="smtp"))
    assert built["email"].name == "smtp"


# --------------------------------------------------------------------- WhatsApp mapping


def _whatsapp(handler: object) -> transports.WhatsAppCloudTransport:
    t = transports.WhatsAppCloudTransport(
        api_base="https://graph.example.com/v21.0",
        phone_number_id="1234",
        access_token="token",
        timeout=1.0,
    )
    return t


async def _send_against(
    handler,
    monkeypatch: pytest.MonkeyPatch,
) -> transports.Receipt:
    transport = _whatsapp(handler)
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await transport.send(
        transports.Outgoing(to_address="+917976603446", subject=None, body="Hello")
    )


async def test_whatsapp_success_returns_the_provider_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    receipt = await _send_against(handler, monkeypatch)
    assert receipt.provider_message_id == "wamid.X"


async def test_whatsapp_4xx_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside the 24-hour window a free-text send is refused. An approved template is the fix;
    a retry is not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="outside the customer service window")

    with pytest.raises(transports.TransportRefused):
        await _send_against(handler, monkeypatch)


async def test_whatsapp_5xx_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="try later")

    with pytest.raises(transports.TransportUnavailable):
        await _send_against(handler, monkeypatch)


async def test_whatsapp_429_is_transient_not_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limiting is the one 4xx that time does fix, so it must not be classified permanent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with pytest.raises(transports.TransportUnavailable):
        await _send_against(handler, monkeypatch)


async def test_whatsapp_network_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route")

    with pytest.raises(transports.TransportUnavailable):
        await _send_against(handler, monkeypatch)
