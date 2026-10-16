"""An in-process, stateful PayU merchant API — the wire `COLLECTIONS_PAYU_WIRE=stub` runs on, and
the double the PayU tests drive.

A **wire**: it answers the form POSTs `PayUCollectionsProvider` makes to `postservice.php`
(`verify_payment`, `cancel_refund_transaction`, `getAllRefundsFromTxnIds`) with PayU's own JSON
shapes, so the real adapter runs unchanged on top of it. It opens no socket. Given a `salt` it
checks every command's hash as PayU does (answering "Invalid Hash." otherwise); without one it
accepts any, like every stub here.

What it remembers: attempts by `txnid` (only the latest is reported, as `verify_payment` does) and
refunds by token (a reused token is "token already used or request pending"). What it does **not**
do: decide outcomes — `pay(...)` records what a customer did on PayU's page.
"""

from __future__ import annotations

import hashlib
import itertools
import urllib.parse
from collections import deque
from decimal import Decimal
from typing import Any

from app.providers.wire import ProviderRejected

PROVIDER = "payu"
_LOG_MAX = 200


class PayUFakeWire:
    """A stateful PayU behind `ProviderWire`. See the module docstring."""

    def __init__(self, *, key: str | None = None, salt: str | None = None) -> None:
        self._key = key
        self._salt = salt
        #: Attempts by `txnid`, oldest first.
        self.attempts: dict[str, list[dict[str, Any]]] = {}
        #: Refunds by token.
        self.refunds: dict[str, dict[str, Any]] = {}
        self.requests: deque[tuple[str, str]] = deque(maxlen=_LOG_MAX)
        #: Set to answer every command as PayU does a wrong key or salt.
        self.reject_credentials = False
        self._ids = itertools.count(40_399_371_500)

    def __repr__(self) -> str:
        return "PayUFakeWire()"

    # -- what the customer does ----------------------------------------------------------

    def pay(
        self,
        txnid: str,
        amount_minor: int,
        *,
        status: str = "success",
        unmapped: str = "captured",
        error: str = "E000",
    ) -> str:
        """Record one attempt on `txnid`; returns its `mihpayid`."""
        mihpayid = str(next(self._ids))
        self.attempts.setdefault(txnid, []).append(
            {
                "mihpayid": mihpayid,
                "txnid": txnid,
                "amt": f"{amount_minor // 100}.{amount_minor % 100:02d}",
                "transaction_amount": f"{amount_minor // 100}.{amount_minor % 100:02d}",
                "status": status,
                "unmappedstatus": unmapped,
                "error_code": error,
                "addedon": "2026-10-16 10:15:00",
                "mode": "UPI",
            }
        )
        return mihpayid

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
        if method != "POST" or path != "/merchant/postservice.php":
            raise ProviderRejected(PROVIDER, 404, "not_found")
        form = dict(urllib.parse.parse_qsl((content or b"").decode()))
        command, var1 = form.get("command", ""), form.get("var1", "")
        if self.reject_credentials or not self._authentic(form):
            return {"status": 0, "msg": "Invalid Hash."}
        if command == "verify_payment":
            return self._verify_payment(var1)
        if command == "cancel_refund_transaction":
            return self._create_refund(var1, form.get("var2", ""), form.get("var3", ""))
        if command == "getAllRefundsFromTxnIds":
            return self._all_refunds(var1)
        return {"status": 0, "msg": "Invalid command"}

    def _authentic(self, form: dict[str, str]) -> bool:
        if self._salt is None:
            return True
        material = "|".join(
            (form.get("key", ""), form.get("command", ""), form.get("var1", ""), self._salt)
        )
        expected = hashlib.sha512(material.encode()).hexdigest()
        return form.get("key") == self._key and form.get("hash") == expected

    def _verify_payment(self, txnid: str) -> dict[str, Any]:
        attempts = self.attempts.get(txnid)
        if not attempts:
            return {
                "status": 0,
                "msg": "0 out of 1 Transactions Fetched Successfully",
                "transaction_details": {txnid: {"mihpayid": "Not Found", "status": "Not Found"}},
            }
        return {
            "status": 1,
            "msg": "1 out of 1 Transactions Fetched Successfully",
            "transaction_details": {txnid: dict(attempts[-1])},
        }

    def _attempt_by_id(self, mihpayid: str) -> dict[str, Any] | None:
        return next(
            (a for group in self.attempts.values() for a in group if a["mihpayid"] == mihpayid),
            None,
        )

    def _create_refund(self, mihpayid: str, token: str, amount: str) -> dict[str, Any]:
        payment = self._attempt_by_id(mihpayid)
        if not token:
            return {"status": 0, "msg": "token is empty", "mihpayid": mihpayid}
        if payment is None or payment["status"] != "success":
            return {"status": 0, "msg": "transaction not exists"}
        if token in self.refunds:
            return {"status": 0, "msg": "token already used or request pending"}
        if not amount or Decimal(amount) > Decimal(payment["amt"]):
            return {"status": 0, "msg": "Refund request failed"}
        request_id = str(next(self._ids))
        self.refunds[token] = {
            "PayuID": mihpayid,
            "txnid": payment["txnid"],
            "RequestID": request_id,
            "RefundToken": token,
            "Amount": amount,
            "Status": "queued",
        }
        return {
            "status": 1,
            "msg": "Refund Request Queued",
            "request_id": request_id,
            "mihpayid": mihpayid,
            "error_code": 102,
        }

    def _all_refunds(self, txnid: str) -> dict[str, Any]:
        found = [dict(r) for r in self.refunds.values() if r["txnid"] == txnid]
        if not found:
            return {"status": 1, "msg": "No Refunds Found for the transaction."}
        return {
            "status": 1,
            "msg": "Refunds fetched successfully.",
            "Refund Details": {found[0]["PayuID"]: found},
        }
