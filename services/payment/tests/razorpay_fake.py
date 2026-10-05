"""A stateful Razorpay, as an `httpx.MockTransport` handler — the double the live Razorpay
adapter is tested against (ADR-0029). No network, no real keys: every key here is a `dev-only-`
placeholder.

It remembers what a real account would — Orders by id, found by a **"contains"** receipt filter (so
the adapter's exact-match is proven, not assumed), the payments made against each Order — and it
answers the way the sandbox was **observed** to answer (the owner's smoke tool, real test keys,
2026-10-06):

* a create with a receipt that already exists is **accepted** and makes a second Order (no `400`);
* the receipt search can miss an Order that exists — `index_new_orders = False` (every Order created
  from then on is invisible to `GET /orders?receipt=`) or `unindexed` (chosen ids) model the
  `count: 0` it answered for an Order created seconds earlier;
* `payment_capture` is accepted on a create and **not echoed** on the Order (kept in
  `capture_requested` for a test to read);
* a bad key is `401` with `error.code = BAD_REQUEST_ERROR` (`invalid_key_status` changes the
  status);
* `GET /orders/{id}` finds an Order by its id; an id it does not hold is `400 BAD_REQUEST_ERROR`.

It still refuses what Razorpay refuses on a platform order: an amount below 100 paise, or a
currency that is not INR.

Knobs a test turns to exercise the failure paths: `inject` queues a scripted answer for the next
request (a `429`, a `500`, a `400`), `lose_next_create_response` makes the next `POST /v1/orders`
create the Order and then fail the way a timeout does (the first create landed and its answer was
lost), and `duplicate` makes a second Order for a receipt directly — what an unknown-outcome create
leaves behind.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import deque
from typing import Any

import httpx

KEY_ID = "rzp_test_FakeAccount01"
KEY_SECRET = "dev-only-razorpay-fake-secret"  # pragma: allowlist secret


class FakeRazorpay:
    def __init__(self, *, key_id: str = KEY_ID, key_secret: str = KEY_SECRET) -> None:
        self._auth = "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self.orders: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, list[dict[str, Any]]] = {}
        #: Refunds by their `X-Refund-Idempotency` key.
        self.refunds: dict[str, dict[str, Any]] = {}
        self.capture_requested: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._inject: deque[httpx.Response | Exception] = deque()
        self.lose_next_create_response = False
        self.invalid_key_status = 401
        #: Whether an Order created from now on shows in the receipt search. False models the
        #: sandbox's `count: 0` for an Order created seconds earlier.
        self.index_new_orders = True
        #: Order ids the receipt search does not show (yet).
        self.unindexed: set[str] = set()

    # -- knobs ---------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def inject(self, answer: httpx.Response | Exception) -> None:
        """The next request is answered with `answer` (or raises it), whatever it was."""
        self._inject.append(answer)

    def methods(self) -> list[str]:
        """`METHOD path` for every request made, in order — what a test asserts the adapter did."""
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def receipt_orders(self, receipt: str) -> list[str]:
        """Every Order id the account holds whose receipt is exactly `receipt`."""
        return [o["id"] for o in self.orders.values() if o["receipt"] == receipt]

    def duplicate(self, receipt: str, **over: Any) -> str:
        """A second Order for `receipt`, as an unknown-outcome create would leave behind: same
        money and notes as the first unless `over` says otherwise. Not a request."""
        first = next(o for o in self.orders.values() if o["receipt"] == receipt)
        order_id = f"order_{uuid.uuid4().hex[:14]}"
        self.orders[order_id] = {**first, "id": order_id, **over}
        return order_id

    def pay(
        self,
        order_id: str,
        *,
        status: str = "captured",
        amount: int | None = None,
        payment_id: str | None = None,
        **extra: Any,
    ) -> str:
        """Record a payment a customer made in Checkout against `order_id`."""
        order = self.orders[order_id]
        entity: dict[str, Any] = {
            "id": payment_id or f"pay_{uuid.uuid4().hex[:14]}",
            "entity": "payment",
            "order_id": order_id,
            "status": status,
            "amount": order["amount"] if amount is None else amount,
            "currency": order["currency"],
            "created_at": 1_700_000_000 + len(self.payments.get(order_id, [])),
            # What a real payment carries and the adapter must never copy out.
            "email": "customer@example.com",
            "contact": "+919876543210",
            "vpa": "customer@upi",
            **extra,
        }
        self.payments.setdefault(order_id, []).append(entity)
        return str(entity["id"])

    # -- the API -------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.content:
            self.bodies.append(json.loads(request.content))
        if self._inject:
            answer = self._inject.popleft()
            if isinstance(answer, Exception):
                raise answer
            return answer
        if request.headers.get("Authorization") != self._auth:
            return self._error(
                self.invalid_key_status, "BAD_REQUEST_ERROR", "The api key provided is invalid"
            )
        path = request.url.path
        if request.method == "GET" and path == "/v1/orders":
            return self._list_orders(request)
        if request.method == "POST" and path == "/v1/orders":
            return self._create_order(request)
        parts = path.split("/")
        if request.method == "GET" and len(parts) == 4 and parts[2] == "orders":
            order = self.orders.get(parts[3])
            if order is None:
                return self._error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
            return httpx.Response(200, json=order)
        if (
            request.method == "POST"
            and path.startswith("/v1/payments/")
            and path.endswith("/refund")
        ):
            return self._refund(request, path.split("/")[3])
        if (
            request.method == "GET"
            and path.startswith("/v1/orders/")
            and path.endswith("/payments")
        ):
            order_id = parts[3]
            if order_id not in self.orders:
                return self._error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
            return httpx.Response(
                200, json={"entity": "collection", "items": self.payments.get(order_id, [])}
            )
        return self._error(404, "BAD_REQUEST_ERROR", "The requested URL was not found")

    def _refund(self, request: httpx.Request, payment_id: str) -> httpx.Response:
        """`POST /v1/payments/{id}/refund` — the same `X-Refund-Idempotency` is the same refund
        (tenant collections, ADR-0030's contract)."""
        payment = next(
            (p for items in self.payments.values() for p in items if p["id"] == payment_id), None
        )
        if payment is None:
            return self._error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
        key = request.headers.get("X-Refund-Idempotency", "")
        known = self.refunds.get(key)
        if known is not None:
            return httpx.Response(200, json=known)
        body = json.loads(request.content)
        refund = {
            "id": f"rfnd_{uuid.uuid4().hex[:14]}",
            "entity": "refund",
            "payment_id": payment_id,
            "amount": body.get("amount"),
            "currency": payment["currency"],
            "status": "pending",
        }
        self.refunds[key] = refund
        return httpx.Response(200, json=refund)

    @staticmethod
    def _error(status: int, code: str, description: str) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": code, "description": description}})

    def _list_orders(self, request: httpx.Request) -> httpx.Response:
        receipt = request.url.params.get("receipt")
        count = int(request.url.params.get("count", "10"))
        items = [
            o
            for o in self.orders.values()
            if (receipt is None or receipt in o["receipt"]) and o["id"] not in self.unindexed
        ]
        return httpx.Response(
            200, json={"entity": "collection", "count": len(items[:count]), "items": items[:count]}
        )

    def _create_order(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("amount", 0) < 100:
            return self._error(400, "BAD_REQUEST_ERROR", "Order amount less than minimum amount")
        if body.get("currency") != "INR":
            return self._error(400, "BAD_REQUEST_ERROR", "The currency is not supported")
        # A receipt that already exists is accepted: a second Order (observed 2026-10-06).
        order_id = f"order_{uuid.uuid4().hex[:14]}"
        self.orders[order_id] = {
            "id": order_id,
            "entity": "order",
            "amount": body["amount"],
            "currency": body["currency"],
            "receipt": body.get("receipt"),
            "status": "created",
            "notes": body.get("notes", {}),
        }
        self.capture_requested[order_id] = body.get("payment_capture")
        if not self.index_new_orders:
            self.unindexed.add(order_id)
        if self.lose_next_create_response:
            self.lose_next_create_response = False
            raise httpx.ReadTimeout("the answer was lost")
        return httpx.Response(200, json=self.orders[order_id])
