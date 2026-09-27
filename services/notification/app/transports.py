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
import contextlib
import re
import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from platform_core import pii
from platform_core.logging import get_logger

log = get_logger("notification.transports")

#: Matches anything address-shaped, so a provider's own error text
#: (`550 5.1.1 <jane@doe.com>: Recipient address rejected`) never reaches a log line or an
#: operator screen with the recipient's address still in it (CLAUDE.md: never log secrets or
#: personal data). Deliberately permissive rather than a strict RFC 5322 grammar — over-matching
#: redacts a little more than an address occasionally; under-matching leaks one.
_ADDRESS_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+")


def scrub_addresses(text: str) -> str:
    """A provider's error message, with every address-shaped substring redacted.

    Applied at the transport boundary — inside `SmtpTransport`/`WhatsAppCloudTransport` before
    the error is wrapped in `TransportRefused`/`TransportUnavailable` — so every caller
    downstream (the dispatcher's log line, the campaign driver's, the recipient-facing message)
    is safe by construction rather than by remembering to scrub at each call site.
    """
    return _ADDRESS_RE.sub("<redacted>", text)


class TransportError(Exception):
    """Base class for a send that did not happen.

    `code` is the provider's own error code (Meta's `error.code`, as a string) when it gave one,
    and `reason` a short machine-readable word for what that code means (`per_user_marketing_
    limit`, `template_paused`). Both are optional: an SMTP failure, a network timeout and a body
    that was not JSON have neither. They travel with the exception so the dispatcher can decide
    on the code rather than on the sentence, and so `messages.provider_error_code` holds what
    Meta actually said.
    """

    def __init__(self, message: str = "", *, code: str | None = None, reason: str | None = None):
        super().__init__(message)
        self.code = code
        self.reason = reason

    def with_message(self, message: str) -> TransportError:
        """The same failure — same class, same code and reason — with different text. For a
        wrapper that scrubs a credential out of the text and must not lose what it classified."""
        return type(self)(message, code=self.code, reason=self.reason)


class TransportRefused(TransportError):
    """The provider will never accept this message. Do not retry."""


class TransportUnavailable(TransportError):
    """The provider could not take it now. Retrying is reasonable."""


class CredentialsRejected(TransportRefused):
    """The provider refused the *credential* (expired or revoked token, restricted account,
    missing permission) rather than this one message.

    Terminal for the message like any refusal, but it says something about every other message on
    the same connection too, so the dispatcher also marks that connection's health invalid and
    later sends fail fast instead of each learning it from Meta.
    """


@dataclass(frozen=True, slots=True)
class Outgoing:
    """What a transport is asked to send. Deliberately not the ORM row.

    `template` carries a provider-registered template payload, already built and validated by
    `domain.build_template_payload`. When it is set the transport sends the template and `body`
    is only a human-readable record of what the customer will read; when it is None the
    transport sends `body` as free text.

    A transport that cannot send templates ignores the field rather than failing: email has no
    such concept, and the alternative — every caller knowing which channel supports what — puts
    provider knowledge back in the rules engine.
    """

    to_address: str
    subject: str | None
    body: str
    template: dict[str, Any] | None = None
    #: An HTML alternative to `body`, for transports that carry one (email). `body` stays the
    #: plain-text part, so a client that cannot render HTML still reads the message.
    html: str | None = None
    #: Extra headers — `List-Unsubscribe` on campaign mail. Ignored by transports with none.
    headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    """What came back. `provider_message_id` is null for transports that return none."""

    provider: str
    provider_message_id: str | None = None
    #: The provider's own acceptance status when it sends one — Meta's `messages[0].
    #: message_status` (`accepted`, `held_for_quality_assessment`, ...). A held message has a
    #: wamid and is `sent` as far as we can tell, but it is not on its way yet.
    message_status: str | None = None


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
            # Masked, never the address itself — this transport is also what `make infra`
            # without MailHog and a misconfigured staging deploy run as, and its log line is
            # exactly the kind of place a real customer's address should never end up in the
            # clear (CLAUDE.md).
            to=pii.mask(message.to_address, kind="email" if self._channel == "email" else "phone"),
            subject=message.subject,
            body_chars=len(message.body),
        )
        return Receipt(provider=self.name)


@dataclass
class InAppTransport:
    """The in-app feed's "provider". There is no network on the other end of it.

    A `Message` staged on the `in_app` channel is already the whole of the delivery — its row
    *is* what `GET /notifications/me` (`app/inbox.py`) reads. `send` therefore does no I/O and
    cannot fail: there is no address to reject and no socket to time out, so it never raises
    `TransportRefused` or `TransportUnavailable`. The dispatcher's claim/send/record cycle still
    runs unchanged, which is the point — reusing it is what "no new event infrastructure" means
    for this channel.
    """

    name = "in_app"

    async def send(self, message: Outgoing) -> Receipt:
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


#: A campaign batch's SMTP sends run here, never on the process's default executor — that pool is
#: what `asyncio.to_thread` (and hence every other blocking call in this process, `pii.py`'s
#: crypto included) shares, and it is also what the dispatcher's own single, one-off sends use
#: for a password reset or a trial-ending notice. A campaign of a few hundred recipients holds a
#: thread for as long as its batch takes to send; two campaigns running at once on a busy day
#: must never be able to occupy every thread the default pool has and leave a password reset
#: waiting behind them. Two threads, not one: a batch that is mid-send must not block a *second*
#: organization's campaign from making any progress at all, and not more than two: this is a
#: concurrency cap on how many SMTP connections this process opens at once, not a throughput
#: knob — a campaign is not latency-sensitive the way a password reset is.
CAMPAIGN_SMTP_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="campaign-smtp")


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
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout
        self._from = from_address
        #: `None` means "the process's default executor" — `loop.run_in_executor(None, ...)`
        #: is exactly what `asyncio.to_thread` does internally, so the platform's own shared
        #: transport (built once in `app/worker.py`, used by the dispatcher for a single
        #: password-reset or notification send) is unaffected by this parameter existing at
        #: all. Only `app/services.py:campaign_transport` passes `CAMPAIGN_SMTP_EXECUTOR`.
        self._executor = executor

    async def send(self, message: Outgoing) -> Receipt:
        (receipt,) = await self.send_many([message])
        if isinstance(receipt, TransportError):
            raise receipt
        return receipt

    async def send_many(self, messages: list[Outgoing]) -> list[Receipt | TransportError]:
        """Send a batch over **one** SMTP session rather than one connection per message.

        The campaign driver (`app/cron.py`) is the caller this exists for: a batch of a few
        hundred recipients that opened a fresh TLS handshake and logged in per message would
        spend most of its time on setup, not delivery. A connection-level failure (cannot
        connect, `login` refused, the socket drops mid-batch) fails every message from that
        point on as `TransportUnavailable` — retrying the batch is reasonable, and there is no
        connection left to keep trying individual messages on. A single recipient's own refusal
        (`SMTPRecipientsRefused`) does not: the connection is still good, so the rest of the
        batch is still attempted on it.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._send_many_blocking, messages)

    def _build(self, message: Outgoing) -> EmailMessage:
        # Building the message — including the caller-supplied headers — happens inside the
        # same try a socket error would raise from, so a message that cannot be built (a header
        # value `email.message` itself refuses) is this one recipient's `TransportRefused`
        # rather than an unclassified exception that would otherwise escape `send`/`send_many`
        # entirely and never reach the campaign driver's per-recipient handling.
        mail = EmailMessage()
        mail["From"] = self._from
        mail["To"] = message.to_address
        mail["Subject"] = message.subject or ""
        for key, value in (message.headers or {}).items():
            mail[key] = value
        mail.set_content(message.body)
        if message.html:
            mail.add_alternative(message.html, subtype="html")
        return mail

    def _send_many_blocking(self, messages: list[Outgoing]) -> list[Receipt | TransportError]:
        results: list[Receipt | TransportError] = []
        try:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        except (smtplib.SMTPException, OSError) as exc:
            unavailable = TransportUnavailable(scrub_addresses(str(exc)))
            return [unavailable] * len(messages)
        try:
            try:
                if self._use_tls:
                    client.starttls()
                if self._username and self._password:
                    client.login(self._username, self._password)
            except (smtplib.SMTPException, OSError) as exc:
                unavailable = TransportUnavailable(scrub_addresses(str(exc)))
                return [unavailable] * len(messages)

            connection_dead = False
            for message in messages:
                if connection_dead:
                    results.append(TransportUnavailable("SMTP connection dropped mid-batch"))
                    continue
                try:
                    mail = self._build(message)
                    client.send_message(mail)
                    results.append(Receipt(provider=self.name))
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
                    results.append(TransportRefused(scrub_addresses(str(exc))))
                except (smtplib.SMTPException, OSError) as exc:
                    connection_dead = True
                    results.append(TransportUnavailable(scrub_addresses(str(exc))))
                except Exception as exc:
                    # Anything else building or sending this one message — a header value the
                    # email package itself refuses, for instance — is this recipient's problem,
                    # not the connection's, so the batch continues.
                    results.append(TransportRefused(scrub_addresses(str(exc))))
        finally:
            with contextlib.suppress(Exception):
                client.quit()
        return results


class WhatsAppCloudTransport:
    """WhatsApp via Meta's Cloud API.

    Sends either a pre-approved **template** message or free **text** to an E.164 recipient,
    depending on whether the caller supplied a built template payload.

    That choice is the 24-hour customer service window, and it is not cosmetic: Meta accepts free
    text only inside the window, and only a template outside it. A proactive notification — an
    invoice reminder, an order update, anything the customer did not just ask for — is by
    definition outside, so it must be a template. A free-text send there is refused with a 4xx,
    classified permanent below, which is honest but still undelivered.

    Failures are classified by Meta's `error.code` (`classify_meta_error`), not by HTTP status:
    throttles and Meta-side outages and network timeouts are unavailable; a per-message refusal is
    refused; a rejected token or restricted account is `CredentialsRejected`. Only a response with
    no usable code falls back to the status.
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
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.to_address,
        }
        if message.template is not None:
            # The only shape that reaches a user outside the 24-hour customer service window.
            payload["type"] = "template"
            payload["template"] = message.template
        else:
            # Free text, valid only inside the window. Outside it Meta refuses with a 4xx,
            # which is classified permanent below — the fix is an approved template, not a retry.
            payload["type"] = "text"
            payload["text"] = {"body": message.body}
        # **No idempotency key.** Meta's messages endpoint takes none, so a request that reached
        # Meta but whose response we never saw (a read timeout, a dropped connection) is
        # indistinguishable from one that never arrived. It is retried as transient below —
        # the alternative is silently dropping messages — which means a timeout after Meta
        # accepted the message can produce a duplicate. The window is small, and neither the
        # receipt webhook nor a lookup can close it (we never learn the wamid). Do not "fix"
        # this by inventing a key: Meta would ignore it.
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            raise TransportUnavailable(str(exc), reason="network") from exc

        body = _json_or_none(response)

        if response.status_code >= 400:
            raise classify_meta_error(response.status_code, body, response.text)

        messages = body.get("messages") if isinstance(body, dict) else None
        first = messages[0] if isinstance(messages, list) and messages else {}
        if not isinstance(first, dict):
            first = {}
        wamid = first.get("id")
        status = first.get("message_status")
        return Receipt(
            provider=self.name,
            provider_message_id=wamid if isinstance(wamid, str) else None,
            message_status=status if isinstance(status, str) else None,
        )


def _json_or_none(response: httpx.Response) -> Any:
    """The response body as JSON, or None when it is not — a proxy's HTML error page, an empty
    2xx. Never raises: a body we cannot parse is a fact about the response, not a crash."""
    try:
        return response.json()
    except ValueError:
        return None


# Meta's `error.code` is the contract; the HTTP status is not (a 400 can be a throttle and a 500
# can be a permanent refusal). Classified against
# https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes, read 2026-09-27.
# Where a code below departs from that page's "retryable" column the departure is noted.

#: Retry with backoff: throughput/rate limits and Meta-side outages. 1 and 2 are Meta's generic
#: "invalid request or server error" and "temporary downtime"; 4 is the app-level rate limit;
#: 131057 is an account in maintenance.
_RETRYABLE_CODES: frozenset[int] = frozenset(
    {130429, 80007, 131056, 131000, 131016, 133004, 2, 1, 4, 131057}
)

#: The credential or the account is the problem, not the message. Terminal for the message, and
#: the connection is marked invalid. 0/190 token invalid or expired; 463/467 are token
#: `error_subcode`s that can also arrive as `code`; 3, 10 and 200-299 permissions; 368 account
#: restricted for policy violations.
_CREDENTIAL_CODES: frozenset[int] = frozenset({0, 3, 10, 190, 368, 463, 467, *range(200, 300)})
_CREDENTIAL_SUBCODES: frozenset[int] = frozenset({458, 459, 460, 463, 467})

#: Terminal, with a reason a UI can map to wording. 131049 (per-user marketing limit) is terminal
#: because retrying inside the window Meta names is futile. 131048 (spam rate limit) and 132015
#: (template paused) are marked retryable by Meta's page, but neither clears in minutes — a
#: sender-quality restriction and a template that stays paused until edited — so they are
#: terminal here, by the owner's instruction.
_TERMINAL_REASONS: dict[int, str] = {
    131049: "per_user_marketing_limit",
    131048: "spam_rate_limit",
    131026: "undeliverable",
    131047: "outside_service_window",
    131051: "unsupported_message_type",
    131050: "user_stopped_marketing",
    131042: "payment_issue",
    131031: "account_restricted",
    132000: "template_parameter_count",
    132001: "template_not_found_or_not_approved",
    132005: "template_text_too_long",
    132007: "template_policy_violation",
    132012: "template_parameter_format",
    132015: "template_paused",
    132016: "template_disabled",
    131005: "permission_not_granted",
    131008: "missing_parameter",
    131009: "invalid_parameter",
    131021: "same_sender_and_recipient",
    100: "invalid_parameter",
}

_MESSAGE_LIMIT = 300


def _code_of(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def classify_meta_error(status_code: int, body: Any, raw_text: str = "") -> TransportError:
    """The exception for one non-2xx Cloud API response, decided on `error.code`.

    The text kept is the code and Meta's own message (plus `error_data.details` when present),
    truncated — not the HTTP status and the first 200 bytes of whatever came back. A response
    with no usable `error.code` (a proxy page, a bare 502) falls back to the status: 429 and 5xx
    are transient, 401 is a rejected credential, anything else is a refusal.
    """
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    code = _code_of(error.get("code"))
    subcode = _code_of(error.get("error_subcode"))
    text = error.get("message")
    data = error.get("error_data")
    details = data.get("details") if isinstance(data, dict) else None
    parts = [p for p in (text, details) if isinstance(p, str) and p]
    message = (
        " - ".join(parts)[:_MESSAGE_LIMIT] or raw_text[:_MESSAGE_LIMIT] or f"HTTP {status_code}"
    )

    if code is None:
        label = f"http_{status_code}: {message}"
        if status_code == 429 or status_code >= 500:
            return TransportUnavailable(label, reason="http_status")
        if status_code == 401:
            return CredentialsRejected(label, reason="credentials_rejected")
        return TransportRefused(label, reason="http_status")

    label = f"{code}: {message}"
    code_s = str(code)
    if code in _CREDENTIAL_CODES or (subcode is not None and subcode in _CREDENTIAL_SUBCODES):
        return CredentialsRejected(label, code=code_s, reason="credentials_rejected")
    if code in _RETRYABLE_CODES:
        return TransportUnavailable(label, code=code_s, reason="retryable")
    reason = _TERMINAL_REASONS.get(code)
    if reason is not None:
        return TransportRefused(label, code=code_s, reason=reason)
    # An unlisted code falls back to the status: an unclassified outage (429/5xx) is cheap to
    # retry, while one that reads as permanent silently drops messages.
    if status_code == 429 or status_code >= 500:
        return TransportUnavailable(label, code=code_s, reason="unlisted_code")
    return TransportRefused(label, code=code_s, reason="unlisted_code")


async def send_batch(
    transport: Transport, messages: list[Outgoing]
) -> list[Receipt | TransportError]:
    """Send a batch, one call whether or not `transport` knows how to.

    `SmtpTransport.send_many` reuses one session; everything else (the recording transport in
    tests, `LogTransport`, a transport nobody has taught this yet) falls back to one `send` per
    message. Either way the campaign driver gets the same shape back: a `Receipt` or a
    `TransportError` per message, in order, and never an exception out of this function — a
    per-recipient failure is data for the caller to record, not a reason to abort the batch.
    """
    many = getattr(transport, "send_many", None)
    if many is not None:
        return await many(messages)  # type: ignore[no-any-return]
    results: list[Receipt | TransportError] = []
    for message in messages:
        try:
            results.append(await transport.send(message))
        except TransportError as exc:
            results.append(exc)
        except Exception as exc:
            results.append(TransportUnavailable(scrub_addresses(str(exc))))
    return results


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

    # No `kind` setting to branch on: unlike email and whatsapp, `in_app` has exactly one
    # implementation and no credentials to be half-configured without.
    transports["in_app"] = InAppTransport()

    return transports
