"""An in-process, stateful Cashfree — the wire `CASHFREE_ENV=stub` runs on, and the double the
Cashfree tests drive (ADR-0032).

It is a **wire**, not an adapter: `CashfreeFakeWire.request` answers the same `(method, path)`
calls `CashfreeHttpWire` makes, so the real `CashfreeProvider` — its lookup, its create, its `409`
handling, its codec, its status maps — runs unchanged on top of it, in development and in tests,
and a bug in any of those is a bug here too. It opens no socket and needs no credentials, so keys
in the environment can never make a stub run reach the network.

What it remembers, as a real account would: Orders by `order_id` (a duplicate is a `409`, unless
the `x-idempotency-key` is the one the Order was created under, in which case it is the same
Order, replayed), payments by Order, and an unknown Order is a `404`. What it does **not** do:
decide outcomes. A customer paying is `pay(...)` — a test (or a developer driving the stub) records
what the customer did, exactly as `RazorpayProvider.stub_record_payment` does, and the poller and
the checkout reads find it through `GET /orders/{id}/payments`.

Every body crosses the same `Decimal`-aware JSON codec the real wire uses, so a response carries
`Decimal` amounts, a request's amounts were encoded as bare numbers, and a `float` anywhere is an
error — the fake is as strict about money as the codec.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.providers import cashfree_codec as codec
from app.providers.wire import ProviderMisconfigured, ProviderRejected

_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{3,45}$")
_REFUND_ID = re.compile(r"^[A-Za-z0-9]{3,40}$")
_PHONE = re.compile(r"^(?:[0-9]{10}|\+[0-9]{8,15})$")

PROVIDER = "cashfree"


class CashfreeFakeWire:
    """A stateful Cashfree behind `ProviderWire`. See the module docstring."""

    def __init__(self) -> None:
        #: Orders by `order_id`, as Cashfree would hold them (`Decimal` amounts, a status).
        self.orders: dict[str, dict[str, Any]] = {}
        #: Payments by `order_id`, oldest first.
        self.payments: dict[str, list[dict[str, Any]]] = {}
        self._idempotency: dict[str, str] = {}
        self._cf_ids = itertools.count(1_000_001)
        #: Refunds by `order_id`, oldest first (tenant collections, ADR-0033).
        self.refunds: dict[str, list[dict[str, Any]]] = {}
        self._cf_refund_ids = itertools.count(5_000_001)
        #: Every request made, `(method, path)`: what a test asserts the adapter did and did not do.
        self.requests: list[tuple[str, str]] = []
        #: Set to make every call answer as a rejected-credentials `401`.
        self.reject_credentials = False

    def __repr__(self) -> str:
        return "CashfreeFakeWire()"

    # -- what the customer does ----------------------------------------------------------

    def pay(
        self,
        order_id: str,
        *,
        status: str = "SUCCESS",
        amount: Decimal | None = None,
        error_code: str | None = None,
    ) -> int:
        """Record one payment attempt a customer made against `order_id`; returns its
        `cf_payment_id`. `status` is Cashfree's own word (`SUCCESS`, `FAILED`, `USER_DROPPED`,
        `PENDING`, …). `amount` defaults to the Order's — pass another to model a surcharge or a
        short payment."""
        order = self.orders[order_id]
        cf_payment_id = next(self._cf_ids)
        entry: dict[str, Any] = {
            "cf_payment_id": cf_payment_id,
            "order_id": order_id,
            "payment_status": status,
            "payment_amount": order["order_amount"] if amount is None else amount,
            "payment_currency": order["order_currency"],
            "order_amount": order["order_amount"],
            "payment_time": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if error_code is not None:
            entry["error_details"] = {"error_code": error_code}
        self.payments.setdefault(order_id, []).append(entry)
        return cf_payment_id

    def expire(self, order_id: str) -> None:
        self.orders[order_id]["order_status"] = "EXPIRED"

    # -- the API -------------------------------------------------------------------------

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
        self.requests.append((method.upper(), path))
        if self.reject_credentials:
            raise ProviderMisconfigured(PROVIDER, 401, "authentication_failed")
        method = method.upper()
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
        if method == "POST" and parts == ["orders"]:
            return self._create_order(json, content, headers or {})
        if method == "GET" and len(parts) == 2 and parts[0] == "orders":
            return self._get_order(parts[1])
        if method == "GET" and len(parts) == 3 and parts[0] == "orders" and parts[2] == "payments":
            self._get_order(parts[1])
            return codec.loads(codec.dumps(list(self.payments.get(parts[1], []))))
        if method == "POST" and len(parts) == 3 and parts[0] == "orders" and parts[2] == "refunds":
            return self._create_refund(parts[1], json, content, headers or {})
        if method == "GET" and len(parts) == 4 and parts[0] == "orders" and parts[2] == "refunds":
            return self._get_refund(parts[1], parts[3])
        raise ProviderRejected(PROVIDER, 404, "endpoint_not_found")

    def _get_refund(self, order_id: str, refund_id: str) -> dict[str, Any]:
        self._get_order(order_id)
        for refund in self.refunds.get(order_id, []):
            if refund["refund_id"] == refund_id:
                result: dict[str, Any] = codec.loads(codec.dumps(refund))
                return result
        raise ProviderRejected(PROVIDER, 404, "refund_not_found")

    def _create_refund(
        self, order_id: str, body: Any, content: bytes | None, headers: dict[str, str]
    ) -> dict[str, Any]:
        """`POST /orders/{id}/refunds`: Cashfree's own rules as the docs state them — a 3-40
        character alphanumeric `refund_id` unique per Order (a duplicate is a `409`, unless the
        `x-idempotency-key` is the one it was made under: the same refund, replayed), an amount no
        larger than what the Order's successful payment has left unrefunded."""
        order = self.orders.get(order_id)
        if order is None:
            raise ProviderRejected(PROVIDER, 404, "order_not_found")
        payload = codec.loads(content if content is not None else codec.dumps(body))
        refund_id = payload.get("refund_id")
        amount = payload.get("refund_amount")
        if not isinstance(refund_id, str) or not _REFUND_ID.fullmatch(refund_id):
            raise ProviderRejected(PROVIDER, 400, "refund_id_invalid")
        if not isinstance(amount, Decimal | int) or isinstance(amount, bool) or amount <= 0:
            raise ProviderRejected(PROVIDER, 400, "refund_amount_invalid")
        key = headers.get("x-idempotency-key")
        existing = self.refunds.setdefault(order_id, [])
        for refund in existing:
            if refund["refund_id"] == refund_id:
                if key is not None and self._idempotency.get(key) == f"refund:{refund_id}":
                    return self._get_refund(order_id, refund_id)
                raise ProviderRejected(PROVIDER, 409, "refund_already_exists")
        paid = next(
            (p for p in self.payments.get(order_id, []) if p["payment_status"] == "SUCCESS"), None
        )
        if paid is None:
            raise ProviderRejected(PROVIDER, 400, "refund_not_allowed")
        standing = sum(
            (Decimal(r["refund_amount"]) for r in existing if r["refund_status"] != "CANCELLED"),
            Decimal(0),
        )
        if standing + Decimal(amount) > Decimal(order["order_amount"]):
            raise ProviderRejected(PROVIDER, 400, "refund_amount_exceeded")
        existing.append(
            {
                "cf_refund_id": next(self._cf_refund_ids),
                "cf_payment_id": paid["cf_payment_id"],
                "refund_id": refund_id,
                "order_id": order_id,
                "refund_amount": Decimal(amount),
                "refund_currency": order["order_currency"],
                "refund_status": "PENDING",
                "refund_speed": payload.get("refund_speed"),
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        if key is not None:
            self._idempotency[key] = f"refund:{refund_id}"
        return self._get_refund(order_id, refund_id)

    def _get_order(self, order_id: str) -> dict[str, Any]:
        order = self.orders.get(order_id)
        if order is None:
            raise ProviderRejected(PROVIDER, 404, "order_not_found")
        result: dict[str, Any] = codec.loads(codec.dumps(order))
        return result

    def _create_order(
        self, body: Any, content: bytes | None, headers: dict[str, str]
    ) -> dict[str, Any]:
        # With no `content`, the same path the real wire takes: encoded with `dumps`, parsed with
        # `Decimal`.
        payload = codec.loads(content if content is not None else codec.dumps(body))
        order_id = payload.get("order_id")
        amount = payload.get("order_amount")
        details = codec.as_dict(payload.get("customer_details"))
        if not isinstance(order_id, str) or not _ORDER_ID.fullmatch(order_id):
            raise ProviderRejected(PROVIDER, 400, "order_id_invalid")
        if not isinstance(amount, Decimal) or amount < Decimal("1"):
            raise ProviderRejected(PROVIDER, 400, "order_amount_invalid")
        if payload.get("order_currency") != "INR":
            raise ProviderRejected(PROVIDER, 400, "order_currency_invalid")
        if not details.get("customer_id") or not _PHONE.fullmatch(
            str(details.get("customer_phone") or "")
        ):
            raise ProviderRejected(PROVIDER, 400, "customer_details_invalid")

        key = headers.get("x-idempotency-key")
        if order_id in self.orders:
            if key is not None and self._idempotency.get(key) == order_id:
                return self._get_order(order_id)  # the same create, replayed
            raise ProviderRejected(PROVIDER, 409, "order_already_exists")

        self.orders[order_id] = {
            "cf_order_id": 2_000_000 + len(self.orders),
            "order_id": order_id,
            "order_amount": amount,
            "order_currency": "INR",
            "order_status": "ACTIVE",
            # Deterministic and opaque: never stored by the platform, only handed to the browser.
            "payment_session_id": "session_stub_"
            + hashlib.sha256(order_id.encode()).hexdigest()[:32],
            "order_expiry_time": payload.get("order_expiry_time"),
            "order_tags": codec.as_dict(payload.get("order_tags")),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if key is not None:
            self._idempotency[key] = order_id
        return self._get_order(order_id)
