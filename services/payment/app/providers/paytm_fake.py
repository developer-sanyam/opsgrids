"""An in-process, stateful Paytm — the wire `COLLECTIONS_PAYTM_WIRE=stub` runs on, and the double
the Paytm tests drive.

A **wire**: it answers the signed JSON POSTs `PaytmCollectionsProvider` makes (Initiate
Transaction, Transaction Status, Refund, Refund Status) with Paytm's own shapes, so the real adapter
runs unchanged on top of it. Given a `merchant_key` it verifies every request's signature as Paytm
does (`330`, "Checksum provided is invalid", otherwise); without one it accepts any.

What it remembers: Orders by `orderId` (a repeat initiate for the same amount is `0002 Success
Idempotent` with a fresh token; one for another amount `2023 Repeat Request Inconsistent`),
attempts by Order (only the latest is reported), refunds by `refId` (a repeat is `617`). What it
does **not** do: decide outcomes — `pay(...)` records what a customer did.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import deque
from decimal import Decimal
from typing import Any

from app.providers.paytm_collections import verify_signature
from app.providers.wire import ProviderRejected

PROVIDER = "paytm"
_LOG_MAX = 200
_BODY = re.compile(rb'^\{"body": (?P<body>.*), "head": \{"signature": "(?P<sig>[^"]*)"\}\}$', re.S)


def _info(status: str, code: str, msg: str = "") -> dict[str, str]:
    return {"resultStatus": status, "resultCode": code, "resultMsg": msg}


class PaytmFakeWire:
    """A stateful Paytm behind `ProviderWire`. See the module docstring."""

    def __init__(self, *, mid: str | None = None, merchant_key: str | None = None) -> None:
        self._mid = mid
        self._key = merchant_key
        self.orders: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        #: Distinct Orders initiated — what "one Order, not two" is asserted against.
        self.created = 0
        self.requests: deque[tuple[str, str]] = deque(maxlen=_LOG_MAX)
        self.reject_credentials = False
        self._ids = itertools.count(20_201_016_000_000)

    def __repr__(self) -> str:
        return "PaytmFakeWire()"

    # -- what the customer does ----------------------------------------------------------

    def pay(self, order_id: str, *, status: str = "TXN_SUCCESS", code: str = "01") -> str:
        """Record one attempt on `order_id`; returns its Paytm `txnId`."""
        order = self.orders[order_id]
        txn_id = str(next(self._ids))
        order["attempts"].append({"txnId": txn_id, "status": status, "code": code})
        return txn_id

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
        match = _BODY.fullmatch(content or b"")
        if method != "POST" or match is None:
            raise ProviderRejected(PROVIDER, 400, "bad_request")
        body_text = match.group("body").decode()
        body = _loads(body_text)
        if self.reject_credentials or not self._authentic(body_text, body, match.group("sig")):
            return {
                "body": {"resultInfo": _info("TXN_FAILURE", "330", "Checksum provided is invalid")}
            }
        if path == "/theia/api/v1/initiateTransaction":
            return {"body": self._initiate(body)}
        if path == "/v3/order/status":
            return {"body": self._order_status(body)}
        if path == "/refund/apply":
            return {"body": self._apply_refund(body)}
        if path == "/v2/refund/status":
            return {"body": self._refund_status(body)}
        raise ProviderRejected(PROVIDER, 404, "not_found")

    def _authentic(self, body_text: str, body: dict[str, Any], signature: bytes) -> bool:
        if self._key is None:
            return True
        return body.get("mid") == self._mid and verify_signature(
            body_text, self._key, signature.decode()
        )

    def _initiate(self, body: dict[str, Any]) -> dict[str, Any]:
        order_id = str(body.get("orderId") or "")
        amount = str((body.get("txnAmount") or {}).get("value") or "")
        if not order_id or not body.get("callbackUrl") or not amount:
            return {"resultInfo": _info("F", "1001", "Request parameters are not valid")}
        order = self.orders.get(order_id)
        if order is None:
            self.created += 1
            self.orders[order_id] = {"amount": amount, "attempts": []}
            code, msg = "0000", "Success"
        elif order["amount"] != amount:
            return {"resultInfo": _info("F", "2023", "Repeat Request Inconsistent")}
        else:
            code, msg = "0002", "Success Idempotent"
        return {
            "resultInfo": _info("S", code, msg),
            "txnToken": f"stub-token-{next(self._ids)}",
            "isPromoCodeValid": False,
            "authenticated": False,
        }

    def _order_status(self, body: dict[str, Any]) -> dict[str, Any]:
        order = self.orders.get(str(body.get("orderId") or ""))
        if order is None:
            return {"resultInfo": _info("TXN_FAILURE", "334", "Invalid Order Id")}
        if not order["attempts"]:
            return {
                "resultInfo": _info("PENDING", "402", "Looks like the payment is not complete."),
                "orderId": body["orderId"],
                "txnAmount": order["amount"],
            }
        last = order["attempts"][-1]
        return {
            "resultInfo": _info(last["status"], last["code"]),
            "txnId": last["txnId"],
            "orderId": body["orderId"],
            "txnAmount": order["amount"],
            "txnDate": "2026-10-16 10:15:00.0",
        }

    def _apply_refund(self, body: dict[str, Any]) -> dict[str, Any]:
        ref_id = str(body.get("refId") or "")
        order = self.orders.get(str(body.get("orderId") or ""))
        paid = order is not None and any(
            a["txnId"] == body.get("txnId") and a["status"] == "TXN_SUCCESS"
            for a in order["attempts"]
        )
        if ref_id in self.refunds:
            return {"resultInfo": _info("TXN_FAILURE", "617", "Refund Already Raised")}
        amount = str(body.get("refundAmount") or "")
        if not paid or not amount or Decimal(amount) > Decimal(order["amount"]):  # type: ignore[index]
            return {"resultInfo": _info("TXN_FAILURE", "600", "Invalid refund request")}
        self.refunds[ref_id] = {
            "orderId": body["orderId"],
            "refundAmount": amount,
            "refundId": str(next(self._ids)),
            "status": "PENDING",
        }
        return {
            "resultInfo": _info(
                "PENDING", "601", "Refund request was raised for this transaction."
            ),
            "refId": ref_id,
            "refundId": self.refunds[ref_id]["refundId"],
            "refundAmount": amount,
        }

    def _refund_status(self, body: dict[str, Any]) -> dict[str, Any]:
        refund = self.refunds.get(str(body.get("refId") or ""))
        if refund is None:
            return {"resultInfo": _info("TXN_FAILURE", "631", "Record not found")}
        return {
            "resultInfo": _info(refund["status"], "601"),
            "refId": body["refId"],
            "refundId": refund["refundId"],
            "refundAmount": refund["refundAmount"],
        }


def _loads(text: str) -> dict[str, Any]:
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
