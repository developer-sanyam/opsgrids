"""An in-process, stateful PhonePe PG (Standard Checkout V2) — the wire
`COLLECTIONS_PHONEPE_WIRE=stub` runs on, and the double the PhonePe tests drive.

A **wire**, not an adapter: it answers the same `(method, path)` calls `PhonePeHttpWire` makes (the
token is the HTTP wire's own business, so it is not modelled here), so the real
`PhonePeCollectionsProvider` runs unchanged on top of it. It opens no socket and needs no
credentials.

What it remembers, as a real account would: Orders by `merchantOrderId` (posting one again while
it is `PENDING` with no attempt replays it with a fresh `redirectUrl`; otherwise it is a
`BAD_REQUEST`, as PhonePe documents), payment attempts by Order, refunds by `merchantRefundId` (a
duplicate id is a `BAD_REQUEST`). An unknown Order or refund is PhonePe's documented
`INVALID_MERCHANT_ORDER_ID` body. What it does **not** do: decide outcomes — `pay(...)` records
what a customer did.
"""

from __future__ import annotations

import itertools
import re
import time
import urllib.parse
from collections import deque
from typing import Any

from app.providers.wire import ProviderMisconfigured, ProviderRejected

PROVIDER = "phonepe"
_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
_STATUS = re.compile(r"^/checkout/v2/order/([^/]+)/status$")
_REFUND_STATUS = re.compile(r"^/payments/v2/refund/([^/]+)/status$")
_LOG_MAX = 200


class PhonePeFakeWire:
    """A stateful PhonePe behind `ProviderWire`. See the module docstring."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        #: Distinct Orders created — what "one Order, not two" is asserted against.
        self.created = 0
        #: Every request made, `(method, path)` — bounded, so a stub left running does not grow.
        self.requests: deque[tuple[str, str]] = deque(maxlen=_LOG_MAX)
        #: Set to make every call answer as the token endpoint refusing the credentials.
        self.reject_credentials = False
        self._ids = itertools.count(1)

    def __repr__(self) -> str:
        return "PhonePeFakeWire()"

    # -- what the customer does ----------------------------------------------------------

    def pay(self, order_id: str, *, state: str = "COMPLETED", error_code: str | None = None) -> str:
        """Record one attempt on `order_id`; returns its `transactionId`. `COMPLETED` also
        completes the Order, as PhonePe does."""
        order = self.orders[order_id]
        txn = f"OM{next(self._ids):010d}"
        attempt: dict[str, Any] = {
            "transactionId": txn,
            "paymentMode": "UPI_QR",
            "timestamp": int(time.time() * 1000),
            "amount": order["amount"],
            "state": state,
        }
        if error_code:
            attempt["errorCode"] = error_code
        order["paymentDetails"].append(attempt)
        if state == "COMPLETED":
            order["state"] = "COMPLETED"
        return txn

    # -- the wire ------------------------------------------------------------------------

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
        self.requests.append((method, path))
        if self.reject_credentials:
            raise ProviderMisconfigured(PROVIDER, 401, "invalid_client")
        if method == "POST" and path == "/checkout/v2/pay":
            return self._create_order(json or {})
        if method == "POST" and path == "/payments/v2/refund":
            return self._create_refund(json or {})
        if method == "GET" and (match := _STATUS.fullmatch(path)):
            order = self.orders.get(urllib.parse.unquote(match.group(1)))
            if order is None:
                raise ProviderRejected(PROVIDER, 400, "INVALID_MERCHANT_ORDER_ID")
            detailed = (params or {}).get("details") == "true"
            attempts = order["paymentDetails"] if detailed else order["paymentDetails"][-1:]
            return {
                **{k: v for k, v in order.items() if k != "paymentDetails"},
                "paymentDetails": attempts,
            }
        if method == "GET" and (match := _REFUND_STATUS.fullmatch(path)):
            refund = self.refunds.get(urllib.parse.unquote(match.group(1)))
            if refund is None:
                raise ProviderRejected(PROVIDER, 400, "INVALID_REFUND_ID")
            return dict(refund)
        raise ProviderRejected(PROVIDER, 404, "NOT_FOUND")

    def _create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        order_id = body.get("merchantOrderId")
        amount = body.get("amount")
        flow = body.get("paymentFlow") or {}
        redirect = (flow.get("merchantUrls") or {}).get("redirectUrl")
        if (
            not isinstance(order_id, str)
            or not _ORDER_ID.fullmatch(order_id)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 100
            or flow.get("type") != "PG_CHECKOUT"
            or not redirect
        ):
            raise ProviderRejected(PROVIDER, 400, "BAD_REQUEST")
        expire_after = body.get("expireAfter")
        if expire_after is not None and not 300 <= expire_after <= 3600:
            raise ProviderRejected(PROVIDER, 400, "BAD_REQUEST")
        order = self.orders.get(order_id)
        if order is None:
            self.created += 1
            order = self.orders[order_id] = {
                "orderId": f"OMO{next(self._ids):010d}",
                "state": "PENDING",
                "amount": amount,
                "expireAt": int(time.time() * 1000) + 1000 * (expire_after or 1200),
                "paymentDetails": [],
            }
        elif order["state"] != "PENDING" or order["paymentDetails"] or order["amount"] != amount:
            raise ProviderRejected(PROVIDER, 400, "BAD_REQUEST")
        return {
            "orderId": order["orderId"],
            "state": order["state"],
            "expireAt": order["expireAt"],
            "redirectUrl": f"https://stub.phonepe.invalid/pay?token={next(self._ids)}",
        }

    def _create_refund(self, body: dict[str, Any]) -> dict[str, Any]:
        refund_id = body.get("merchantRefundId")
        order = self.orders.get(str(body.get("originalMerchantOrderId")))
        amount = body.get("amount")
        if (
            not isinstance(refund_id, str)
            or order is None
            or order["state"] != "COMPLETED"
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or not 100 <= amount <= order["amount"]
        ):
            raise ProviderRejected(PROVIDER, 400, "BAD_REQUEST")
        if refund_id in self.refunds:
            raise ProviderRejected(PROVIDER, 400, "BAD_REQUEST")
        self.refunds[refund_id] = {
            "originalMerchantOrderId": body["originalMerchantOrderId"],
            "refundId": f"OMR{next(self._ids):010d}",
            "amount": amount,
            "state": "PENDING",
            "timestamp": int(time.time() * 1000),
        }
        return {k: self.refunds[refund_id][k] for k in ("refundId", "amount", "state")}
