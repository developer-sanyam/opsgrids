#!/usr/bin/env python3
"""Owner-run, opt-in smoke test of Razorpay in **test mode** (ADR-0032). Settles what the Phase A
design could only assume about Razorpay's own API — see the "Unverified" list of
`docs/superpowers/specs/2026-10-06-payments-phase-a-design.md`, §9, which this tool answers line by
line. Read `tools/payments_smoke_common.py` first: the rules (opt-in, never CI or production, keys
from the process environment only, test keys only, prints only ids / statuses / codes / field names)
are shared by every smoke tool and are the reason this is safe to run at all.

    # from the repository root, on your own machine. `uv run`, NOT `make` (make re-exports `.env`).
    # `read -rs` types the value without echoing it and keeps it out of the shell history (an
    # `export NAME=value` typed at the prompt is recorded there like any other command).
    read -rs RAZORPAY_KEY_ID && export RAZORPAY_KEY_ID          # rzp_test_... only; else refused
    read -rs RAZORPAY_KEY_SECRET && export RAZORPAY_KEY_SECRET
    uv run python tools/payments_smoke_razorpay.py plan                  # calls nothing
    uv run python tools/payments_smoke_razorpay.py probe --confirm       # non-interactive checks
    uv run python tools/payments_smoke_razorpay.py payments order_XXXX --confirm  # after paying it
    uv run python tools/payments_smoke_razorpay.py refund pay_XXXX --confirm

**`probe`** answers, each as one printed line:

1. `credentials` — does the key pair authenticate (`GET /orders?count=1`)?
2. `bad_key` — **the real HTTP status and error code of a rejected key** (the design assumed `401`;
   the classification does not depend on it, and this shows which it is), and whether the body is
   the invalid-key answer the adapter recognises (a boolean — the description itself is never kept).
3. `order_capture_true` / `order_capture_unset` — an Order created with `payment_capture: true` and
   one without: whether the field is echoed, so "captured automatically" is observed, not assumed.
4. `lookup_exact` / `lookup_prefix` — `GET /orders?receipt=`: found, and whether the filter is the
   documented "contains" (a prefix also matches), which is why the adapter matches the receipt
   exactly.
5. `duplicate_receipt` — **the real status and code of a duplicate receipt** and whether its message
   says "unique" (the reports disagreed; the adapter looks once more after any `400`).
6. `amount_below_minimum`, `currency_usd` — what Razorpay answers for the two things the platform
   refuses before any call, so the refusals are known to be Razorpay's rules and not our guess.
7. `order_payments_empty` — the shape of an unpaid Order's payments list.

**`payments <order_id>`** is run after you pay that Order with Razorpay's **test card** in a
Checkout opened by hand (a local page with `checkout.js` and the Order id): it prints each
attempt's status, amount, currency and **the names of the fields the payment entity carries**
(`email`, `contact`, `vpa`, `card`, `notes`...) — names only, never values — which is what the
webhook redaction allow-list is checked against. **`refund <payment_id>`** refunds Re 1 of a
captured test payment with a valid idempotency key, twice, and prints whether the second call is
the same refund (the status vocabulary is printed too).

What test mode cannot prove (and this tool does not claim to): real bank mandate activation, the
pre-debit notice window, UPI cutoffs, issuer declines, webhook retry behaviour, fee-bearer amount
differences and rate limits.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

import httpx
import payments_smoke_common as common

API = "https://api.razorpay.com/v1"
#: The descriptions the adapter treats as "this is the invalid-key answer" (a boolean only).
_INVALID_KEY = {"the api key provided is invalid", "authentication failed"}
PLAN = (
    "probe: credentials, bad_key, order_capture_true, order_capture_unset, lookup_exact, "
    "lookup_prefix, duplicate_receipt, amount_below_minimum, currency_usd, order_payments_empty",
    "payments <order_id>: each attempt's status, amount, currency and the payment entity's "
    "field names",
    "refund <payment_id>: a Re 1 refund with an idempotency key, twice (same refund?)",
)


class Client:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        transport: httpx.BaseTransport | None,
        report: common.Reporter,
    ) -> None:
        self._auth = (key_id, key_secret)
        self._client = httpx.Client(
            base_url=API, transport=transport, timeout=httpx.Timeout(10.0), follow_redirects=False
        )
        self.report = report

    def call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        key_id, secret = auth or self._auth
        merged = {
            "Authorization": "Basic " + base64.b64encode(f"{key_id}:{secret}".encode()).decode(),
            **(headers or {}),
        }
        response = self._client.request(method, path, json=json, params=params, headers=merged)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}


def _error(body: dict[str, Any]) -> tuple[str | None, str]:
    error = body.get("error")
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    description = str(error.get("description") or "")
    return (code if isinstance(code, str) else None), description


def _items(body: dict[str, Any]) -> list[Any]:
    items = body.get("items")
    return items if isinstance(items, list) else []


def probe(client: Client, secret: str) -> None:
    say = client.report.line
    status, _ = client.call("GET", "/orders", params={"count": 1})
    say("credentials", status=status, ok=status == 200)
    if status != 200:
        client.report.text("stopping: the key pair does not authenticate; fix it and re-run")
        return

    key_id = client._auth[0]
    status, body = client.call("GET", "/orders", params={"count": 1}, auth=(key_id, secret + "x"))
    code, description = _error(body)
    say(
        "bad_key",
        status=status,
        code=code,
        is_invalid_key_description=description.strip().rstrip(".").casefold() in _INVALID_KEY,
    )

    run = uuid.uuid4().hex[:24]
    receipt = f"smoke-{run}"  # 30 characters: inside Razorpay's 40
    status, body = client.call(
        "POST",
        "/orders",
        json={
            "amount": 100,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": True,
            "notes": {"smoke": "1"},
        },
    )
    order_id = body.get("id") if isinstance(body.get("id"), str) else None
    say(
        "order_capture_true",
        status=status,
        order=common.short(order_id),
        echoes_payment_capture="payment_capture" in body,
        order_status=body.get("status"),
    )
    status, body = client.call(
        "POST",
        "/orders",
        json={"amount": 100, "currency": "INR", "receipt": f"smoke-{uuid.uuid4().hex[:24]}"},
    )
    say(
        "order_capture_unset",
        status=status,
        order=common.short(body.get("id")),
        echoes_payment_capture="payment_capture" in body,
    )

    status, body = client.call("GET", "/orders", params={"receipt": receipt, "count": 10})
    items = _items(body)
    say(
        "lookup_exact",
        status=status,
        count=len(items),
        exact_match=any(isinstance(i, dict) and i.get("receipt") == receipt for i in items),
    )
    status, body = client.call("GET", "/orders", params={"receipt": receipt[:-4], "count": 10})
    items = _items(body)
    say(
        "lookup_prefix",
        status=status,
        count=len(items),
        prefix_matches_the_full_receipt=any(
            isinstance(i, dict) and i.get("receipt") == receipt for i in items
        ),
    )

    status, body = client.call(
        "POST", "/orders", json={"amount": 100, "currency": "INR", "receipt": receipt}
    )
    code, description = _error(body)
    say(
        "duplicate_receipt",
        status=status,
        code=code,
        description_mentions_unique="unique" in description.lower(),
        created_a_second_order=status == 200,
    )
    status, body = client.call(
        "POST",
        "/orders",
        json={"amount": 99, "currency": "INR", "receipt": f"smoke-{uuid.uuid4().hex[:24]}"},
    )
    say("amount_below_minimum", status=status, code=_error(body)[0])
    status, body = client.call(
        "POST",
        "/orders",
        json={"amount": 100, "currency": "USD", "receipt": f"smoke-{uuid.uuid4().hex[:24]}"},
    )
    say("currency_usd", status=status, code=_error(body)[0], created=status == 200)

    if order_id:
        status, body = client.call("GET", f"/orders/{order_id}/payments")
        say(
            "order_payments_empty",
            status=status,
            items_is_list=isinstance(body.get("items"), list),
            count=len(_items(body)),
        )
        client.report.text(
            f"to complete one by hand: open Checkout with order {common.short(order_id)}, pay with "
            "Razorpay's test card, then run `payments <order_id>`"
        )


def payments(client: Client, order_id: str) -> None:
    status, body = client.call("GET", f"/orders/{order_id}/payments")
    items = _items(body)
    client.report.line("order_payments", status=status, count=len(items))
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        client.report.line(
            f"attempt_{n}",
            payment=common.short(item.get("id")),
            status=item.get("status"),
            amount=item.get("amount"),
            currency=item.get("currency"),
            error_code=item.get("error_code"),
        )
        # Names only: which keys the entity carries decides what the redaction must drop.
        client.report.names(f"attempt_{n}_fields", item.keys())
        client.report.line(
            f"attempt_{n}_person_fields",
            email="email" in item,
            contact="contact" in item,
            vpa="vpa" in item,
            card="card" in item or "card_id" in item,
            notes_is_list=isinstance(item.get("notes"), list),
        )


def refund(client: Client, payment_id: str) -> None:
    key = "rfd" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:36]  # the platform's shape
    first_status, first = client.call(
        "POST",
        f"/payments/{payment_id}/refund",
        json={"amount": 100, "speed": "normal"},
        headers={"X-Refund-Idempotency": key},
    )
    second_status, second = client.call(
        "POST",
        f"/payments/{payment_id}/refund",
        json={"amount": 100, "speed": "normal"},
        headers={"X-Refund-Idempotency": key},
    )
    client.report.line(
        "refund_first",
        status=first_status,
        refund=common.short(first.get("id")),
        refund_status=first.get("status"),
        code=_error(first)[0],
    )
    client.report.line(
        "refund_second",
        status=second_status,
        refund=common.short(second.get("id")),
        same_refund=first.get("id") is not None and first.get("id") == second.get("id"),
        code=_error(second)[0],
    )


def check_test_keys(key_id: str) -> None:
    if not key_id.startswith("rzp_test_"):
        raise common.Refused("refused: only rzp_test_ keys (test mode) are accepted")


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    modules: Mapping[str, Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("command", choices=["plan", "probe", "payments", "refund"])
    parser.add_argument("target", nargs="?", help="an order id (payments) or a payment id (refund)")
    parser.add_argument("--confirm", action="store_true", help="actually call Razorpay (test mode)")
    args = parser.parse_args(argv)
    environment = common.default_environ() if environ is None else environ
    stream = sys.stdout if out is None else out
    if args.command == "plan":
        for line in PLAN:
            stream.write(line + "\n")
        stream.write("nothing was called: re-run a command with --confirm\n")
        return 2
    try:
        common.guard(environment, confirmed=args.confirm, modules=modules)
        keys = common.read_env(environment, "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")
        check_test_keys(keys["RAZORPAY_KEY_ID"])
        if args.command in ("payments", "refund") and not args.target:
            raise common.Refused(f"refused: {args.command} needs an id")
    except common.Refused as refusal:
        stream.write(str(refusal) + "\n")
        return 2
    report = common.Reporter(stream, secrets=[keys["RAZORPAY_KEY_SECRET"], keys["RAZORPAY_KEY_ID"]])
    client = Client(keys["RAZORPAY_KEY_ID"], keys["RAZORPAY_KEY_SECRET"], transport, report)
    try:
        if args.command == "probe":
            probe(client, keys["RAZORPAY_KEY_SECRET"])
        elif args.command == "payments":
            payments(client, args.target)
        else:
            refund(client, args.target)
    except httpx.HTTPError as exc:
        report.line("transport_error", error=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
