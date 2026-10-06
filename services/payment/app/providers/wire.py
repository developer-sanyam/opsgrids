"""Provider-neutral wire machinery: the error classes, the timeouts, the call deadline and the one
HTTP client both live adapters are built on (ADR-0032).

Everything a provider's HTTP wire has in common lives here so neither adapter module imports the
other, and so the three outcomes a failed call can have are named once, at the seam, instead of
being rediscovered per provider:

* **`ProviderTransient`** — the call may or may not have landed: a timeout, a connect failure, a
  `429`, a `5xx`, a response that could not be read. Nothing is concluded; the charge stays
  `created` and the lookup-before-create (or the sweep's final lookup) resolves it.
* **`ProviderRejected`** — the provider answered, and refused: any other `4xx`. On a create that is
  provably nothing created, so the charge fails with the provider's stable code as its reason.
* **`ProviderMisconfigured`** — the credentials are wrong or not allowed (`401`/`403`, or the
  provider's own "invalid key" answer). Not the customer's fault and not the charge's: it never
  consumes an attempt, never fails the charge, never emits `commerce.payment.failed` — it trips the
  credentials latch and pages an operator, because dunning a paying tenant for the platform's own
  bad key is the failure this class exists to prevent.

All three are `ProviderCallError`s whose message carries the provider name, an HTTP status and a
stable code and **nothing else** — never a body, a description, a header or a key — so they are
safe to log and to put in a problem response.

**The overall deadline.** A per-request timeout is not a bound on a call: an HTTP client's read
timeout restarts whenever a byte arrives, and a call makes several requests. `call_deadline` puts a
monotonic deadline in a context variable (it follows the call onto the worker thread), and
`HttpWire` clamps every request's timeouts to what remains, reads the body in chunks and checks the
clock between them, and **refuses to start a `POST`** (every create here is one) with less than one
full per-request timeout left — a create that begins and is then abandoned by the caller is the
second payment the whole lookup-before-create design exists to prevent.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import httpx

from app.providers.base import ProviderCallError

#: The connect and read timeouts the live wires set on **every** request, and how many requests one
#: full call can make at worst. Their product is what must stay inside
#: `Settings.provider_call_timeout_seconds` (`RazorpayProvider.request_timeouts_seconds`,
#: `ProviderContract`), declared here — not in either adapter — so the settings validator can hold
#: the budget to the worst adapter without importing one.
WIRE_CONNECT_TIMEOUT_SECONDS = 2.0
WIRE_READ_TIMEOUT_SECONDS = 5.0
#: Razorpay: the Order lookup, the Order create, the re-search after a `400` from the create, the
#: payments lookup, the payment create. (A checkout whose Order is recorded makes one request, the
#: `GET /orders/{id}`; Razorpay does not refuse a duplicate receipt, so the re-search is a recovery,
#: not a duplicate check.)
RAZORPAY_REQUESTS_PER_CHARGE = 5
#: Cashfree: the lookup, the create, and the lookup again after a `409` on the create.
CASHFREE_REQUESTS_PER_CHARGE = 3
WIRE_REQUEST_TIMEOUT_SECONDS = WIRE_CONNECT_TIMEOUT_SECONDS + WIRE_READ_TIMEOUT_SECONDS
#: The worst declared sum over every adapter, which `provider_call_timeout_seconds` must cover.
MAX_DECLARED_CALL_BUDGET_SECONDS = WIRE_REQUEST_TIMEOUT_SECONDS * max(
    RAZORPAY_REQUESTS_PER_CHARGE, CASHFREE_REQUESTS_PER_CHARGE
)
#: The most response bytes a wire will buffer. Provider answers here are a few kilobytes.
WIRE_MAX_RESPONSE_BYTES = 1024 * 1024


class ProviderApiError(ProviderCallError):
    """A provider answered (or failed to) and the wire classified it. `status_code` is the HTTP
    status (0 for a transport failure) and `code` a stable machine code — the provider's own
    `error.code`, or one of this module's (`timeout`, `connect_error`, `transport_error`,
    `deadline_exceeded`, `unreadable_response`, `response_too_large`). Never a description."""

    def __init__(self, provider: str, status_code: int, code: str | None) -> None:
        super().__init__(f"{provider} answered {status_code} ({code or 'no error code'})")
        self.provider = provider
        self.status_code = status_code
        self.code = code


class ProviderTransient(ProviderApiError):
    """The outcome is unknown. See the module docstring."""


class ProviderRejected(ProviderApiError):
    """The provider refused; nothing was created. See the module docstring."""


class ProviderMisconfigured(ProviderApiError):
    """The platform's own credentials are wrong or not allowed. See the module docstring."""


def classify_status(
    status_code: int, *, invalid_credentials: bool = False
) -> type[ProviderApiError]:
    """The class of an HTTP error answer. `invalid_credentials` is the adapter's own boolean "this
    body is the provider's invalid-key answer" — computed from the body and then discarded, so the
    description it matched is never kept (a provider may answer a bad key with a `400`)."""
    if invalid_credentials or status_code in (401, 403):
        return ProviderMisconfigured
    if status_code in (408, 425, 429) or status_code >= 500:
        return ProviderTransient
    return ProviderRejected


# --------------------------------------------------------------------------- the call deadline


class Deadline:
    """A monotonic point in time a whole provider call must be over by."""

    __slots__ = ("_clock", "_end")

    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def clamp(self, timeout: httpx.Timeout) -> httpx.Timeout:
        """`timeout` with every phase capped at what remains of the call."""
        left = self.remaining()

        def cap(value: float | None) -> float:
            return left if value is None else min(value, left)

        return httpx.Timeout(
            cap(timeout.read),
            connect=cap(timeout.connect),
            write=cap(timeout.write),
            pool=cap(timeout.pool),
        )


_CURRENT: contextvars.ContextVar[Deadline | None] = contextvars.ContextVar(
    "provider_call_deadline", default=None
)


def current_deadline() -> Deadline | None:
    """The deadline of the provider call this code is running inside, if any."""
    return _CURRENT.get()


@contextmanager
def call_deadline(
    seconds: float | None, *, clock: Callable[[], float] = time.monotonic
) -> Iterator[Deadline | None]:
    """Run a provider call under an overall deadline of `seconds` (None: no deadline). Set on the
    thread the call runs on — a context variable set before `asyncio.to_thread` is copied into it,
    and one set inside it is what the wire reads."""
    if seconds is None:
        yield None
        return
    deadline = Deadline(seconds, clock=clock)
    token = _CURRENT.set(deadline)
    try:
        yield deadline
    finally:
        _CURRENT.reset(token)


@contextmanager
def ensure_deadline(seconds: float | None) -> Iterator[Deadline | None]:
    """The deadline the call is already running under, or a new one of `seconds` when there is
    none — so `services.call_provider_charge` is bounded when a test or a script calls it
    directly, and is not given a *second*, later deadline when it is run by
    `attempt_provider_charge`, whose own started with the caller's wait."""
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    with call_deadline(seconds) as created:
        yield created


def run_with_deadline[T](seconds: float | None, fn: Callable[[], T]) -> T:
    """`fn()` under `call_deadline(seconds)` — the shape `asyncio.to_thread` takes."""
    with call_deadline(seconds):
        return fn()


# --------------------------------------------------------------------------- the credentials latch

#: How long the first rejected-credentials answer stops further calls to that provider. After it the
#: next call is a probe: if the key was fixed (or the rejection was the provider's own wobble) the
#: platform recovers by itself, and if not it trips again and pages again.
CREDENTIALS_LATCH_SECONDS = 900.0


class CredentialsLatch:
    """`credentials_rejected`, per provider, per process. Once a provider answers that the
    platform's credentials are wrong, calling it again only burns an attempt per charge and a
    bank-visible failure per customer — so for a while no call is made at all and no charge's
    attempt is consumed. In memory on purpose: a fixed key is a restart of the container (the
    environment is read at start), which clears it."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def trip(self, provider: str, *, seconds: float = CREDENTIALS_LATCH_SECONDS) -> bool:
        """Latch `provider`. True when this call tripped it (and so should page), False when it
        was already latched — one alert per outage, not one per charge."""
        with self._lock:
            now = self._clock()
            already = self._until.get(provider, 0.0) > now
            self._until[provider] = now + seconds
            return not already

    def tripped(self, provider: str) -> bool:
        with self._lock:
            return self._until.get(provider, 0.0) > self._clock()

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._until.clear()
            else:
                self._until.pop(provider, None)


#: The process-wide latch the service layer consults (`services.attempt_provider_charge`).
CREDENTIALS = CredentialsLatch()


# --------------------------------------------------------------------------- the HTTP wire


class ProviderWire(Protocol):
    """What an adapter calls when it has a live wire. One method, so a test can hand it a double
    that records requests and answers from a script. `json` is the body for a JSON POST; a wire
    that must encode numbers itself (Cashfree's rupee amounts) is handed the encoded `content`."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...


class HttpWire:
    """The one place a socket is opened to a payment provider. Synchronous on purpose: the adapter
    methods it serves are synchronous and always run on a worker thread under a deadline.

    A subclass supplies its provider's name, base URL and credentials (`_auth`, `_headers`), how to
    read its stable error code (`_error_code`), whether a body is that provider's invalid-key answer
    (`_invalid_credentials`) and, if it needs more than `json.loads`, how to parse a body
    (`_parse`). Nothing here logs a request, a response body, a header or a credential.
    """

    provider = "provider"

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        connect_timeout: float = WIRE_CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = WIRE_READ_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._full_timeout = connect_timeout + read_timeout

    def __repr__(self) -> str:  # never the credentials, in a traceback or a debugger
        return f"{type(self).__name__}(base_url={self._base_url!r})"

    # -- hooks ---------------------------------------------------------------------------

    def _auth(self) -> httpx.Auth | None:
        return None

    def _headers(self) -> dict[str, str]:
        return {}

    def _error_code(self, body: Any) -> str | None:
        return None

    def _invalid_credentials(self, status_code: int, body: Any) -> bool:
        return False

    def _parse(self, raw: bytes) -> Any:
        return json.loads(raw)

    # -- the call ------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        deadline = current_deadline()
        timeout = self._timeout
        if deadline is not None:
            if deadline.expired():
                raise ProviderTransient(self.provider, 0, "deadline_exceeded")
            if method.upper() == "POST" and deadline.remaining() < self._full_timeout:
                # A create that starts with less than one full timeout left can outlive the
                # caller's wait: abandoned, still in flight, and landing after the sweep's lookup
                # found nothing. Refused instead; the lookup finds whatever an earlier call made.
                raise ProviderTransient(self.provider, 0, "deadline_exceeded")
            timeout = deadline.clamp(timeout)
        merged = {**self._headers(), **(headers or {})}
        try:
            with (
                httpx.Client(
                    base_url=self._base_url,
                    auth=self._auth(),
                    timeout=timeout,
                    transport=self._transport,
                    # Never follow a redirect with credentials attached to wherever it points.
                    follow_redirects=False,
                ) as client,
                client.stream(
                    method, path, params=params, json=json, content=content, headers=merged
                ) as response,
            ):
                status_code = response.status_code
                raw = self._read(response, deadline)
        except httpx.TimeoutException:
            raise ProviderTransient(self.provider, 0, "timeout") from None
        except httpx.ConnectError:
            raise ProviderTransient(self.provider, 0, "connect_error") from None
        except httpx.HTTPError:
            raise ProviderTransient(self.provider, 0, "transport_error") from None

        try:
            body = self._parse(raw) if raw else None
        except ValueError:
            body = None
            readable = False
        else:
            readable = True
        if status_code >= 400:
            code = self._error_code(body) if readable else None
            invalid = readable and self._invalid_credentials(status_code, body)
            raise classify_status(status_code, invalid_credentials=invalid)(
                self.provider, status_code, code
            )
        if not readable:
            # A success that cannot be read: whatever it was, it may have been done.
            raise ProviderTransient(self.provider, status_code, "unreadable_response")
        return body

    def _read(self, response: httpx.Response, deadline: Deadline | None) -> bytes:
        """The body, in chunks, so the overall deadline and the size cap hold while it streams (a
        read timeout alone restarts on every chunk and would let a slow trickle through)."""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > WIRE_MAX_RESPONSE_BYTES:
                raise ProviderTransient(self.provider, response.status_code, "response_too_large")
            if deadline is not None and deadline.expired():
                raise ProviderTransient(self.provider, response.status_code, "deadline_exceeded")
            chunks.append(chunk)
        return b"".join(chunks)
