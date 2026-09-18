"""Where a message actually goes. One protocol, three implementations.

The port exists so the rest of the service never knows which provider is configured, and so the
tests never open a socket: `RecordingTransport` satisfies the same protocol and keeps what it was
given. Swapping SMTP for a hosted API is a config change and a new class, not a change to the
dispatcher.

Failures are **classified**, because the dispatcher's decision differs. A `TransportRefused` is
the provider saying "this will never work" — a malformed address, a number not on WhatsApp — and
retrying it is just a slower way to fail. A `TransportUnavailable` is "not now" — a timeout, a
5xx, a refused connection — and that is exactly what retries are for.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import httpx

from platform_core.logging import get_logger

log = get_logger("notification.transports")


class TransportError(Exception):
    """Base class for a send that did not happen."""


class TransportRefused(TransportError):
    """The provider will never accept this message. Do not retry."""


class TransportUnavailable(TransportError):
    """The provider could not take it now. Retrying is reasonable."""


@dataclass(frozen=True, slots=True)
class Outgoing:
    """What a transport is asked to send. Deliberately not the ORM row."""

    to_address: str
    subject: str | None
    body: str


@dataclass(frozen=True, slots=True)
class Receipt:
    """What came back. `provider_message_id` is null for transports that return none."""

    provider: str
    provider_message_id: str | None = None


class Transport(Protocol):
    """The port. Everything the dispatcher needs to know about a provider."""

    @property
    def name(self) -> str: ...

    async def send(self, message: Outgoing) -> Receipt: ...


class LogTransport:
    """Renders, records, sends nothing. **The default.**

    A service started without credentials must be inert rather than half-configured. This is
    also what local development uses when MailHog is not running, and what keeps a misconfigured
    staging deploy from mailing real customers.
    """

    name = "log"

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def send(self, message: Outgoing) -> Receipt:
        log.info(
            "notification.log_transport.send",
            channel=self._channel,
            to=message.to_address,
            subject=message.subject,
            body_chars=len(message.body),
        )
        return Receipt(provider=self.name)


@dataclass
class RecordingTransport:
    """Keeps what it was asked to send. For tests only — never registered by `build`."""

    name: str = "recording"
    sent: list[Outgoing] = field(default_factory=list)
    fail_with: Exception | None = None

    async def send(self, message: Outgoing) -> Receipt:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)
        return Receipt(provider=self.name, provider_message_id=f"rec-{len(self.sent)}")


class SmtpTransport:
    """Email over SMTP.

    `smtplib` runs in a worker thread rather than an async client, which keeps the dependency
    set unchanged — the loop is not blocked, and a send is short. The choice is reversible: this
    class is the only thing that would change.

    `SMTPRecipientsRefused` and `SMTPSenderRefused` are permanent by definition, so they refuse
    rather than retry; everything else from the socket is treated as "not now".
    """

    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        use_tls: bool,
        timeout: float,
        from_address: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout
        self._from = from_address

    async def send(self, message: Outgoing) -> Receipt:
        await asyncio.to_thread(self._send_blocking, message)
        return Receipt(provider=self.name)

    def _send_blocking(self, message: Outgoing) -> None:
        mail = EmailMessage()
        mail["From"] = self._from
        mail["To"] = message.to_address
        mail["Subject"] = message.subject or ""
        mail.set_content(message.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
                if self._use_tls:
                    client.starttls()
                if self._username and self._password:
                    client.login(self._username, self._password)
                client.send_message(mail)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise TransportRefused(str(exc)) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise TransportUnavailable(str(exc)) from exc


class WhatsAppCloudTransport:
    """WhatsApp via Meta's Cloud API.

    Sends a `text` message to an E.164 recipient. Note the constraint this does not pretend to
    solve: outside a 24-hour customer service window Meta accepts only pre-approved *template*
    messages, and a free-text send is refused. That refusal arrives as a 4xx and is classified
    permanent, which is the honest outcome — the fix is an approved template, not a retry.

    4xx is refused, 5xx and timeouts are unavailable. 429 is deliberately *unavailable*: rate
    limiting is the one 4xx that time does fix.
    """

    name = "whatsapp_cloud"

    def __init__(
        self,
        *,
        api_base: str,
        phone_number_id: str,
        access_token: str,
        timeout: float,
    ) -> None:
        self._url = f"{api_base.rstrip('/')}/{phone_number_id}/messages"
        self._token = access_token
        self._timeout = timeout

    async def send(self, message: Outgoing) -> Receipt:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.to_address,
            "type": "text",
            "text": {"body": message.body},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            raise TransportUnavailable(str(exc)) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise TransportUnavailable(f"{response.status_code}: {response.text[:200]}")
        if response.status_code >= 400:
            raise TransportRefused(f"{response.status_code}: {response.text[:200]}")

        body = response.json()
        messages = body.get("messages") or [{}]
        return Receipt(provider=self.name, provider_message_id=messages[0].get("id"))


def build(cfg: object) -> dict[str, Transport]:
    """The channel-to-transport map this process will use.

    Credentials missing while a real transport is named is a **startup** failure, not a silent
    downgrade to `log`. A service that quietly stops sending looks identical to one with nothing
    to send, and the difference is only discovered by a customer who did not hear from us.
    """
    email_kind = getattr(cfg, "email_transport", "log")
    whatsapp_kind = getattr(cfg, "whatsapp_transport", "log")

    transports: dict[str, Transport] = {}

    if email_kind == "smtp":
        password = getattr(cfg, "smtp_password", None)
        transports["email"] = SmtpTransport(
            host=cfg.smtp_host,  # type: ignore[attr-defined]
            port=cfg.smtp_port,  # type: ignore[attr-defined]
            username=getattr(cfg, "smtp_username", None),
            password=password.get_secret_value() if password is not None else None,
            use_tls=cfg.smtp_use_tls,  # type: ignore[attr-defined]
            timeout=cfg.smtp_timeout_seconds,  # type: ignore[attr-defined]
            from_address=cfg.email_from,  # type: ignore[attr-defined]
        )
    elif email_kind == "log":
        transports["email"] = LogTransport("email")
    else:
        raise ValueError(f"Unknown email transport {email_kind!r}")

    if whatsapp_kind == "whatsapp_cloud":
        token = getattr(cfg, "whatsapp_access_token", None)
        phone_number_id = getattr(cfg, "whatsapp_phone_number_id", None)
        if token is None or not phone_number_id:
            raise ValueError(
                "whatsapp_transport is 'whatsapp_cloud' but the phone number id or access "
                "token is unset — refusing to start half-configured"
            )
        transports["whatsapp"] = WhatsAppCloudTransport(
            api_base=cfg.whatsapp_api_base,  # type: ignore[attr-defined]
            phone_number_id=phone_number_id,
            access_token=token.get_secret_value(),
            timeout=cfg.whatsapp_timeout_seconds,  # type: ignore[attr-defined]
        )
    elif whatsapp_kind == "log":
        transports["whatsapp"] = LogTransport("whatsapp")
    else:
        raise ValueError(f"Unknown whatsapp transport {whatsapp_kind!r}")

    return transports
