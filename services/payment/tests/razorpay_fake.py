"""A stateful Razorpay, as an `httpx.MockTransport` handler — the double the live Razorpay
adapter is tested against (ADR-0029). No network, no real keys: every key here is a `dev-only-`
placeholder.

It remembers what a real account would — Orders by id, found by a **"contains"** receipt filter (so
the adapter's exact-match is proven, not assumed), the payments made against each Order — and it
refuses what Razorpay refuses: wrong credentials (a `401`), an amount below 100 paise or a currency
that is not INR on a platform order, and a **duplicate receipt** (`400 BAD_REQUEST_ERROR`, which is
what Create Order documents and what a create that landed-and-was-retried meets).

Knobs a test turns to exercise the failure paths: `inject` queues a scripted answer for the next
request, `lose_next_create_response` makes the next `POST /v1/orders` create the Order and then fail
the way a timeout does (the first create landed and its answer was lost), and `invalid_key_status`
sets the status a bad key is answered with (the real one is for the smoke tool to observe).
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
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._inject: deque[httpx.Response | Exception] = deque()
        self.lose_next_create_response = False
        self.invalid_key_status = 401

    # -- knobs ---------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def inject(self, answer: httpx.Response | Exception) -> None:
        """The next request is answered with `answer` (or raises it), whatever it was."""
        self._inject.append(answer)

    def methods(self) -> list[str]:
        """`METHOD path` for every request made, in order — what a test asserts the adapter did."""
        return [f"{r.method} {r.url.path}" for r in self.requests]

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
            order_id = path.split("/")[3]
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
        items = [o for o in self.orders.values() if receipt is None or receipt in o["receipt"]]
        return httpx.Response(200, json={"entity": "collection", "items": items[:count]})

    def _create_order(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        receipt = body.get("receipt")
        if body.get("amount", 0) < 100:
            return self._error(400, "BAD_REQUEST_ERROR", "Order amount less than minimum amount")
        if body.get("currency") != "INR":
            return self._error(400, "BAD_REQUEST_ERROR", "The currency is not supported")
        if any(o["receipt"] == receipt for o in self.orders.values()):
            return self._error(400, "BAD_REQUEST_ERROR", "Order receipt should be unique.")
        order_id = f"order_{uuid.uuid4().hex[:14]}"
        self.orders[order_id] = {
            "id": order_id,
            "entity": "order",
            "amount": body["amount"],
            "currency": body["currency"],
            "receipt": receipt,
            "status": "created",
            "notes": body.get("notes", {}),
            "payment_capture": body.get("payment_capture"),
        }
        if self.lose_next_create_response:
            self.lose_next_create_response = False
            raise httpx.ReadTimeout("the answer was lost")
        return httpx.Response(200, json=self.orders[order_id])
