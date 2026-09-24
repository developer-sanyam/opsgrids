"""Transport classification, and the refusal to start half-configured.

The distinction these tests protect is the one the dispatcher acts on: a refusal must not be
retried and an outage must be. Getting it backwards either drops messages silently or retries a
bad address forever.

Nothing here opens a socket. The WhatsApp cases drive the real client against a stubbed
transport, so the status-code mapping is tested without a network.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
from typing import ClassVar

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


async def test_whatsapp_sends_a_template_when_one_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape that reaches a user outside the 24-hour window. Asserted on the wire, because
    the difference between `type: text` and `type: template` is the difference between a
    delivered notification and a 4xx."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.T"}]})

    transport = transports.WhatsAppCloudTransport(
        api_base="https://graph.example.com/v25.0",
        phone_number_id="1234",
        access_token="token",
        timeout=1.0,
    )
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    built = {
        "name": "invoice_due_reminder",
        "language": {"code": "en_US"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": "INV-9"}]}],
    }
    receipt = await transport.send(
        transports.Outgoing(
            to_address="+917976603446",
            subject=None,
            body="preview only",
            template=built,
        )
    )

    assert receipt.provider_message_id == "wamid.T"
    assert seen["type"] == "template"
    assert seen["template"] == built
    # The preview text must not be sent alongside — the provider holds the wording.
    assert "text" not in seen


async def test_whatsapp_sends_text_when_no_template_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    transport = transports.WhatsAppCloudTransport(
        api_base="https://graph.example.com/v25.0",
        phone_number_id="1234",
        access_token="token",
        timeout=1.0,
    )
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    await transport.send(
        transports.Outgoing(to_address="+917976603446", subject=None, body="Hello")
    )
    assert seen["type"] == "text"
    assert seen["text"] == {"body": "Hello"}
    assert "template" not in seen


async def test_email_transport_ignores_a_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport without templates ignores the field rather than failing — the alternative
    puts provider knowledge back in the rules engine."""
    receipt = await transports.LogTransport("email").send(
        transports.Outgoing(to_address="a@b.com", subject="S", body="B", template={"name": "x"})
    )
    assert receipt.provider == "log"


# --------------------------------------------------------------------- masked logging


class _CapturingLog:
    """A drop-in for `transports.log` that just remembers what it was called with, so a test
    can assert on the field values a structlog call would otherwise only print."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, kwargs))


async def test_log_transport_masks_an_email_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md: never log secrets or personal data. `LogTransport` is what a misconfigured
    deploy and local development without MailHog run as, so its log line is exactly the place a
    real customer's address must never appear in the clear."""
    captured = _CapturingLog()
    monkeypatch.setattr(transports, "log", captured)
    await transports.LogTransport("email").send(
        transports.Outgoing(to_address="jane@example.com", subject="Hi", body="Body")
    )
    (event, fields) = captured.calls[0]
    assert event == "notification.log_transport.send"
    assert fields["to"] != "jane@example.com"
    assert "jane@example.com" not in str(fields["to"])
    assert fields["to"] == "j•••@e•••"


async def test_log_transport_masks_a_phone_number_on_the_whatsapp_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _CapturingLog()
    monkeypatch.setattr(transports, "log", captured)
    await transports.LogTransport("whatsapp").send(
        transports.Outgoing(to_address="+919876543210", subject=None, body="Hi")
    )
    (_, fields) = captured.calls[0]
    assert "9876543210" not in str(fields["to"])
    assert str(fields["to"]).endswith("3210")


def test_scrub_addresses_redacts_email_like_substrings() -> None:
    text = "550 5.1.1 <jane@doe.com>: Recipient address rejected"
    scrubbed = transports.scrub_addresses(text)
    assert "jane@doe.com" not in scrubbed
    assert "<redacted>" in scrubbed
    assert "Recipient address rejected" in scrubbed


# --------------------------------------------------------------------- SMTP batching


class _FakeSmtp:
    """Stands in for `smtplib.SMTP`: no socket, and it remembers what it was asked to send."""

    instances: ClassVar[list[_FakeSmtp]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.sent: list[str] = []
        self.quit_called = False
        self.connect_error: Exception | None = None
        self.refuse_recipients: set[str] = set()
        self.die_after: int | None = None
        _FakeSmtp.instances.append(self)

    def starttls(self) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        pass

    def send_message(self, mail) -> None:
        to = mail["To"]
        if to in self.refuse_recipients:
            raise smtplib.SMTPRecipientsRefused({to: (550, b"no such user")})
        if self.die_after is not None and len(self.sent) >= self.die_after:
            raise smtplib.SMTPServerDisconnected("connection lost")
        self.sent.append(to)

    def quit(self) -> None:
        self.quit_called = True

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.quit()


def _smtp_transport() -> transports.SmtpTransport:
    return transports.SmtpTransport(
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        use_tls=True,
        timeout=1.0,
        from_address="from@example.com",
    )


def _message(to: str) -> transports.Outgoing:
    return transports.Outgoing(to_address=to, subject="Hi", body="Body")


async def test_send_many_reuses_one_connection_for_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    transport = _smtp_transport()
    results = await transport.send_many([_message("a@b.com"), _message("c@d.com")])
    assert all(isinstance(r, transports.Receipt) for r in results)
    assert len(_FakeSmtp.instances) == 1, "one connection for the whole batch, not one per message"
    assert _FakeSmtp.instances[0].sent == ["a@b.com", "c@d.com"]
    assert _FakeSmtp.instances[0].quit_called


async def test_send_many_refuses_one_recipient_without_aborting_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single recipient's own refusal (a bad address) leaves the connection good — the rest of
    the batch is still attempted on it."""
    _FakeSmtp.instances.clear()

    def factory(host: str, port: int, timeout: float | None = None) -> _FakeSmtp:
        fake = _FakeSmtp(host, port, timeout)
        fake.refuse_recipients = {"bad@b.com"}
        return fake

    monkeypatch.setattr(smtplib, "SMTP", factory)
    transport = _smtp_transport()
    results = await transport.send_many(
        [_message("a@b.com"), _message("bad@b.com"), _message("c@d.com")]
    )
    assert isinstance(results[0], transports.Receipt)
    assert isinstance(results[1], transports.TransportRefused)
    assert isinstance(results[2], transports.Receipt)
    assert "bad@b.com" not in str(results[1])


async def test_send_many_treats_a_connect_failure_as_unavailable_for_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(host: str, port: int, timeout: float | None = None) -> _FakeSmtp:
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", factory)
    transport = _smtp_transport()
    results = await transport.send_many([_message("a@b.com"), _message("c@d.com")])
    assert all(isinstance(r, transports.TransportUnavailable) for r in results)


async def test_send_many_marks_the_rest_unavailable_once_the_connection_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket error mid-batch (not a per-recipient refusal) means the connection itself is
    gone — the rest of the batch is not worth attempting on it."""
    _FakeSmtp.instances.clear()

    def factory(host: str, port: int, timeout: float | None = None) -> _FakeSmtp:
        fake = _FakeSmtp(host, port, timeout)
        fake.die_after = 1
        return fake

    monkeypatch.setattr(smtplib, "SMTP", factory)
    transport = _smtp_transport()
    results = await transport.send_many(
        [_message("a@b.com"), _message("b@b.com"), _message("c@b.com")]
    )
    assert isinstance(results[0], transports.Receipt)
    assert isinstance(results[1], transports.TransportUnavailable)
    assert isinstance(results[2], transports.TransportUnavailable)


async def test_send_delegates_to_send_many_for_a_single_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    receipt = await _smtp_transport().send(_message("a@b.com"))
    assert receipt.provider == "smtp"


# --------------------------------------------------------------------- send_batch()


async def test_send_batch_uses_send_many_when_the_transport_has_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    transport = _smtp_transport()
    results = await transports.send_batch(transport, [_message("a@b.com"), _message("b@b.com")])
    assert len(results) == 2
    assert len(_FakeSmtp.instances[-1].sent) == 2


async def test_send_batch_falls_back_to_one_send_per_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`RecordingTransport` (and `LogTransport`) have no `send_many` — `send_batch` still
    returns one result per message, never raising out of the batch."""
    recording = transports.RecordingTransport()
    results = await transports.send_batch(recording, [_message("a@b.com"), _message("b@b.com")])
    assert all(isinstance(r, transports.Receipt) for r in results)
    assert [m.to_address for m in recording.sent] == ["a@b.com", "b@b.com"]


async def test_send_batch_records_a_refusal_without_raising() -> None:
    failing = transports.RecordingTransport(fail_with=transports.TransportRefused("550 no"))
    results = await transports.send_batch(failing, [_message("a@b.com")])
    assert isinstance(results[0], transports.TransportRefused)


# ------------------------------------------------------- SmtpTransport's executor (M7)


async def test_send_many_uses_the_process_default_executor_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform's own shared transport (built once in `app/worker.py`, used by the
    dispatcher for a single password-reset or notification send) must land on the default
    pool — passing no executor at all is what makes that true."""
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    transport = _smtp_transport()
    captured: dict[str, object] = {}

    class _RecordingLoop:
        async def run_in_executor(self, executor: object, func: object, *args: object) -> object:
            captured["executor"] = executor
            return func(*args)  # type: ignore[operator]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _RecordingLoop())
    await transport.send_many([_message("a@b.com")])
    assert captured["executor"] is None


async def test_send_many_uses_the_executor_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """A campaign's transport (`app/services.py:campaign_transport`) is built with
    `CAMPAIGN_SMTP_EXECUTOR` — this is the seam that proves the value actually reaches the
    blocking call, not just the constructor."""
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    sentinel_executor = object()
    transport = transports.SmtpTransport(
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        use_tls=True,
        timeout=1.0,
        from_address="from@example.com",
        executor=sentinel_executor,  # type: ignore[arg-type]
    )
    captured: dict[str, object] = {}

    class _RecordingLoop:
        async def run_in_executor(self, executor: object, func: object, *args: object) -> object:
            captured["executor"] = executor
            return func(*args)  # type: ignore[operator]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _RecordingLoop())
    await transport.send_many([_message("a@b.com")])
    assert captured["executor"] is sentinel_executor


def test_campaign_smtp_executor_is_a_small_dedicated_pool() -> None:
    """A big campaign must never be able to hold every thread the process's default pool has —
    see `transports.CAMPAIGN_SMTP_EXECUTOR`'s own docstring for why 2, specifically."""
    assert transports.CAMPAIGN_SMTP_EXECUTOR._max_workers == 2


def test_campaign_transport_uses_the_dedicated_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """`services.campaign_transport` — the campaign driver's own path — must pass
    `CAMPAIGN_SMTP_EXECUTOR`; `services.send_test`'s interactive path must not, so a running
    campaign can never make an operator's "send a test" button hang behind it."""
    import inspect

    from app import services

    source = inspect.getsource(services.campaign_transport)
    assert "CAMPAIGN_SMTP_EXECUTOR" in source
