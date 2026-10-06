"""The live HTTP wire to Razorpay — for tenant collections (ADR-0024 §8) and, behind
`PAYMENT_WIRE=live`, for platform billing's customer-present checkout (ADR-0032).

`razorpay.py`'s adapter keeps its wire stubbed by default. Tenant collections needs a small part
of the API: it creates an Order, lets the customer pay it in Razorpay's own hosted Checkout, asks
what happened to that Order, and refunds. Platform checkout needs exactly the same (it differs only
in whose keys, and in who is paid), so `RazorpayProvider` takes an optional `wire` and
`app/deps.py` gives the platform provider one only when the owner turns `PAYMENT_WIRE` to `live`
(and a collections adapter one only when `COLLECTIONS_WIRE` says so).

The calls, exactly as Razorpay documents them (checked 2026-09-29; the open questions the owner-run
`tools/payments_smoke_razorpay.py` settles are listed in the Phase A design):

* `GET  /v1/orders?receipt=<receipt>` — search for an Order by the receipt this platform set on
  it. Razorpay's filter "contains" the value, so the adapter still matches the receipt exactly.
  **Best-effort only**: in the sandbox (2026-10-06) it answered `count: 0` for an Order created
  seconds earlier, and once a `429`.
* `POST /v1/orders` — `{amount, currency, receipt (≤ 40 chars), notes (≤ 15 keys)}`, and, for
  platform Orders, `payment_capture: true` (not echoed back on the Order). The receipt is **not**
  unique: a create with a receipt that already exists is accepted and makes a second Order.
* `GET  /v1/orders/{id}` — one Order by its id: how an Order the platform has recorded is found
  again (an id Razorpay does not hold is a `400 BAD_REQUEST_ERROR`).
* `GET  /v1/orders/{id}/payments` — every payment attempt against an Order.
* `POST /v1/payments/{id}/refund` — `{amount, speed}` with the `X-Refund-Idempotency` header
  (≥ 10 characters of `[A-Za-z0-9_-]`): a retry with the same key is the same refund.

Authentication is HTTP basic with the key id and key secret. **Nothing here logs a request, a
response body, a header or a credential**: an error keeps the HTTP status and Razorpay's stable
`error.code` only — never `description`, which can echo request data. The one thing read from a
description is a boolean ("is this Razorpay's invalid-key answer?"), computed and discarded, so a
bad key is classified `ProviderMisconfigured` whatever status Razorpay answers it with.

Every request has a connect and a read timeout (`WIRE_CONNECT_TIMEOUT_SECONDS`,
`WIRE_READ_TIMEOUT_SECONDS`), clamped to the call's overall deadline when there is one
(`app/providers/wire.py`), and the caller bounds the whole call on a worker thread. A call
abandoned that way is repeated safely for the customer: an Order is searched for by receipt before
one is created (which may still make a duplicate when the search misses — never handed to a browser:
only the id the service recorded is), a recorded Order is fetched by its id, and a refund carries
its idempotency header.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.providers.wire import HttpWire, ProviderApiError, ProviderTransient, ProviderWire

#: Razorpay's API root. Platform configuration, never tenant input — so not an egress-guarded
#: fetch (ADR-0016 covers URLs a tenant typed; a tenant here supplies only key material).
RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

#: The error a wire raises is the neutral, classified one (`app/providers/wire.py`); this name is
#: what the collections code and its tests have always imported.
RazorpayApiError = ProviderApiError

#: What a wire call is typed as in `razorpay.py`: one method, so a test hands it a scripted double.
RazorpayWire = ProviderWire

#: Razorpay's own answers to a bad key, normalised (lower-cased, trailing full stop dropped).
#: Matched to produce a boolean and then discarded. The sandbox answers a bad key with `401` and
#: `error.code = BAD_REQUEST_ERROR` (observed 2026-10-06), which the status alone already
#: classifies; the description match stays so a `400` carrying the same answer is not mistaken for
#: a refusal.
_INVALID_CREDENTIAL_DESCRIPTIONS = frozenset(
    {"the api key provided is invalid", "authentication failed"}
)


class RazorpayHttpWire(HttpWire):
    """The one class in this service that opens a socket to Razorpay."""

    provider = "razorpay"

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        base_url: str = RAZORPAY_API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url=base_url, transport=transport)
        self._basic = httpx.BasicAuth(key_id, key_secret)

    def _auth(self) -> httpx.Auth:
        return self._basic

    def _error_code(self, body: Any) -> str | None:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, str) and code.isascii() and len(code) <= 64 else None

    def _invalid_credentials(self, status_code: int, body: Any) -> bool:
        error = body.get("error") if isinstance(body, dict) else None
        description = error.get("description") if isinstance(error, dict) else None
        if not isinstance(description, str):
            return False
        return description.strip().rstrip(".").casefold() in _INVALID_CREDENTIAL_DESCRIPTIONS

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = super().request(
            method, path, params=params, json=json, content=content, headers=headers
        )
        if not isinstance(body, dict):
            # An answer that is not an object: whatever the call was, it may have been done.
            raise ProviderTransient("razorpay", 200, "unexpected_response")
        return body
