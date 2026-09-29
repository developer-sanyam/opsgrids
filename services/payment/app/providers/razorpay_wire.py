"""The live HTTP wire to Razorpay — for tenant collections only (ADR-0024 §8).

`razorpay.py`'s adapter keeps its wire stubbed for **platform** billing: that is B5g's job, bound
by `tests/provider_contract.py` and deliberately not done here. Tenant collections needs far less
of the API, and none of the part B5g is hard about (debiting a saved mandate): it creates an
Order, lets the customer pay it in Razorpay's own hosted Checkout, asks what happened to that
Order, and refunds. So `RazorpayProvider` takes an optional `wire`; `app/deps.py` never gives the
platform provider one, and `app/collections/` gives one to a merchant's adapter only when the
deployment says so (`Settings.collections_wire == "razorpay"`).

The calls, exactly as Razorpay documents them (checked 2026-09-29):

* `GET  /v1/orders?receipt=<receipt>` — find an Order by the receipt this platform set on it.
  Razorpay's filter "contains" the value, so the adapter still matches the receipt exactly.
* `POST /v1/orders` — `{amount, currency, receipt (≤ 40 chars), notes (≤ 15 keys)}`.
* `GET  /v1/orders/{id}/payments` — every payment attempt against an Order.
* `POST /v1/payments/{id}/refund` — `{amount, speed}` with the `X-Refund-Idempotency` header
  (≥ 10 characters of `[A-Za-z0-9_-]`): a retry with the same key is the same refund.

Authentication is HTTP basic with the merchant's own key id and key secret. **Nothing here logs
a request, a response body, a header or a credential**: an error keeps Razorpay's stable
`error.code` and the HTTP status only — never `description`, which can echo request data.

Every request has a connect and a read timeout (`WIRE_CONNECT_TIMEOUT_SECONDS`,
`WIRE_READ_TIMEOUT_SECONDS`); the caller additionally bounds the whole call on a worker thread
(`app.collections.services._off_loop`), since a read timeout restarts whenever bytes arrive.
A call abandoned that way is still safe to repeat: an Order is looked up by receipt before one
is created, and a refund carries its idempotency header.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.providers.base import ProviderCallError
from app.providers.razorpay import WIRE_CONNECT_TIMEOUT_SECONDS, WIRE_READ_TIMEOUT_SECONDS

#: Razorpay's API root. Platform configuration, never tenant input — so not an egress-guarded
#: fetch (ADR-0016 covers URLs a tenant typed; a tenant here supplies only key material).
RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


class RazorpayApiError(ProviderCallError):
    """Razorpay answered, and not with success. Carries the HTTP status and Razorpay's stable
    `error.code` (e.g. `BAD_REQUEST_ERROR`) — never the description or any part of the body, so
    the message is always safe to log and to put in a problem response."""

    def __init__(self, status_code: int, code: str | None) -> None:
        super().__init__(f"razorpay answered {status_code} ({code or 'no error code'})")
        self.status_code = status_code
        self.code = code


class RazorpayWire(Protocol):
    """What `RazorpayProvider` calls when it has a live wire. One method, so a test can hand it a
    double that records requests and answers from a script."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code.isascii() and len(code) <= 64 else None


class RazorpayHttpWire:
    """The one class in this service that opens a socket to Razorpay. Synchronous on purpose —
    the adapter methods it serves are synchronous and always run on a worker thread."""

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        base_url: str = RAZORPAY_API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._auth = httpx.BasicAuth(key_id, key_secret)
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = httpx.Timeout(
            WIRE_READ_TIMEOUT_SECONDS, connect=WIRE_CONNECT_TIMEOUT_SECONDS
        )

    def __repr__(self) -> str:  # never the credentials, in a traceback or a debugger
        return f"RazorpayHttpWire(base_url={self._base_url!r})"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(
            base_url=self._base_url,
            auth=self._auth,
            timeout=self._timeout,
            transport=self._transport,
            # Never follow a redirect with credentials attached to wherever it points.
            follow_redirects=False,
        ) as client:
            response = client.request(method, path, params=params, json=json, headers=headers)
        if response.status_code >= 400:
            raise RazorpayApiError(response.status_code, _error_code(response))
        try:
            body = response.json()
        except ValueError:
            raise RazorpayApiError(response.status_code, "unreadable_response") from None
        if not isinstance(body, dict):
            raise RazorpayApiError(response.status_code, "unexpected_response")
        return body
