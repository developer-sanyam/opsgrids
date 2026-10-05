"""The live HTTP wire to Cashfree — a thin `httpx` client, no SDK (ADR-0029).

Cashfree's PG API as Phase A uses it (every endpoint, enum and length below comes from
model-summarised documentation, not a raw OpenAPI file or a live call: the owner-run
`tools/payments_smoke_cashfree.py` is what settles the open items, and the Phase A design lists
them):

* `POST /pg/orders` — create the Order the customer pays: the merchant's own `order_id` (here the
  charge id), `order_amount` (a rupee **number**), `order_currency`, `customer_details`
  (`customer_id` and a 10-digit `customer_phone` are required), `order_meta.return_url`,
  `order_expiry_time`, `order_tags`. Answers `payment_session_id`, which the browser's
  `Cashfree().checkout()` is opened with.
* `GET  /pg/orders/{order_id}` — the Order as it stands; a `404` for one that does not exist.
* `GET  /pg/orders/{order_id}/payments` — every payment attempt against it.

Credentials go in headers only (`x-client-id`, `x-client-secret`), with the pinned `x-api-version`.
Redirects are never followed, the transport is injectable (a test hands in an `httpx.MockTransport`
or the stateful fake in `cashfree_fake.py`), `repr` shows no credential, and nothing here logs a
request, a response body, a header or a credential. An error keeps the HTTP status and Cashfree's
stable `code` only — never `message`, which can echo request data — and is raised `from None`.
Bodies are parsed with `Decimal` for every non-integer number (`cashfree_codec.loads`) and
requests are encoded by `cashfree_codec.dumps`, so an amount never touches a float.

Every request carries a connect and a read timeout, clamped to the call's overall deadline when
there is one, and a `POST` refuses to start with less than one full timeout left
(`app/providers/wire.py`).
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from app.providers import cashfree_codec as codec
from app.providers.wire import HttpWire

#: Cashfree's PG API roots, by environment. Platform configuration, never tenant input.
CASHFREE_API_BASE: dict[str, str] = {
    "sandbox": "https://sandbox.cashfree.com/pg",
    "production": "https://api.cashfree.com/pg",
}


class CashfreeHttpWire(HttpWire):
    """The one class in this service that opens a socket to Cashfree."""

    provider = "cashfree"

    def __init__(
        self,
        *,
        app_id: str,
        secret_key: str,
        env: Literal["sandbox", "production"],
        transport: httpx.BaseTransport | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url or CASHFREE_API_BASE[env], transport=transport)
        self._app_id = app_id
        self._secret_key = secret_key

    def _headers(self) -> dict[str, str]:
        return {
            "x-client-id": self._app_id,
            "x-client-secret": self._secret_key,
            "x-api-version": codec.API_VERSION,
            "accept": "application/json",
        }

    def _parse(self, raw: bytes) -> Any:
        return codec.loads(raw)

    def _error_code(self, body: Any) -> str | None:
        code = body.get("code") if isinstance(body, dict) else None
        return code if isinstance(code, str) and code.isascii() and len(code) <= 64 else None

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
        merged = dict(headers or {})
        if json is not None:
            content = codec.dumps(json)
            merged["content-type"] = "application/json"
        return super().request(method, path, params=params, content=content, headers=merged or None)
