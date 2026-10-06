#!/usr/bin/env python3
"""Owner-run, opt-in smoke test of Cashfree Payments in the **sandbox** (ADR-0032). Settles what the
Phase A design could only assume about Cashfree's own API — the "Unverified" list of
`docs/superpowers/specs/2026-10-06-payments-phase-a-design.md`, §9. Read
`tools/payments_smoke_common.py` first: the rules (opt-in, never CI or production, keys from the
process environment only, sandbox keys only, prints only ids / statuses / codes / field names) are
shared by every smoke tool and are the reason this is safe to run at all.

    # from the repository root, on your own machine. `uv run`, NOT `make` (make re-exports `.env`).
    # `read -rs` types the value without echoing it and keeps it out of the shell history.
    read -rs CASHFREE_APP_ID && export CASHFREE_APP_ID            # a sandbox App ID (TEST...) only
    read -rs CASHFREE_SECRET_KEY && export CASHFREE_SECRET_KEY    # a sandbox secret only
    uv run python tools/payments_smoke_cashfree.py plan
    uv run python tools/payments_smoke_cashfree.py probe --confirm
    uv run python tools/payments_smoke_cashfree.py payments <order_id> --confirm   # after paying it
    uv run python tools/payments_smoke_cashfree.py refund <order_id> --confirm
    uv run python tools/payments_smoke_cashfree.py webhook-listen --confirm        # see below

The base URL is the sandbox's and is not configurable: this tool cannot reach production. If
`CASHFREE_ENV` is set to anything but `sandbox` it refuses.

**`probe`** answers, each as one printed line:

1. `credentials` / `bad_key` — **the real status and error code** of an unknown order id with a good
   key (the "does it 404 for a missing order, and with what code" question the adapter's
   `get_order` depends on) and with a wrong secret (the status the adapter classifies as
   `ProviderMisconfigured`).
2. `api_version_pinned` / `api_version_bogus` — whether the account accepts `x-api-version`
   `2025-01-01` (the version the adapter pins) and what it answers to a made-up one.
3. `order_create` — an Order of Re 1 with a unique id: its status, and the **shape** of the answer
   (`order_amount` as a JSON number or a string; whether `payment_session_id` is present).
4. `order_refetch` — whether `GET /orders/{id}` answers `payment_session_id` **again**, which
decides
   whether a checkout page can be re-opened by id.
5. `order_duplicate` / `order_idempotency_key` — the same order id twice (status and code), and the
   same request twice with an `x-idempotency-key` (same Order back, or refused).
6. `amount_below_minimum` / `amount_three_decimals` / `currency_usd` — what Cashfree answers for the
   things the platform refuses before any call.
7. `phone_international` — whether a customer phone with a country code is accepted (the platform
   sends 10 digits and refuses the rest; this shows whether that is Cashfree's rule or ours).
8. `tags_15` / `tags_16` — the `order_tags` limit.
9. `order_fields` — the *names* of the Order's fields, and whether `customer_details` comes back.

**`payments <order_id>`** is run after you pay the Order by hand in the sandbox Checkout: each
attempt's status, amount type, currency, and the **names** of the fields the payment entity carries
(including which of `customer_details`, `payment_method`, `bank_reference` are present) — what the
webhook redaction allow-list is checked against. **`refund <order_id>`** refunds Re 1 of a paid
sandbox Order twice with the same refund id and prints whether the second answer is the same refund.

**`webhook-listen`** starts a one-shot HTTP listener on `127.0.0.1` (port 8765 by default, `--port`)
and waits for **one** delivery; expose it through a tunnel you trust, point a sandbox webhook at it
in the Cashfree dashboard and trigger a payment. It prints the **header names** the delivery used,
whether the timestamp is seconds or milliseconds (by digit count), whether the signature verifies
under `base64(HMAC-SHA256(secret, timestamp + body))`, the body's field **names**, and whether the
fields that carry a person (`customer_details`, `payment_method`) are present — never a value. A
delivery that arrives is not stored anywhere.

What the sandbox cannot prove (and this tool does not claim to): production behaviour, settlement
timing, real issuer declines, webhook retry cadence and rate limits.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, TextIO

import httpx
import payments_smoke_common as common

SANDBOX_API = "https://sandbox.cashfree.com/pg"
PINNED_API_VERSION = "2025-01-01"
PLAN = (
    "probe: credentials, bad_key, api_version_pinned, api_version_bogus, order_create, "
    "order_refetch, order_duplicate, order_idempotency_key, amount_below_minimum, "
    "amount_three_decimals, currency_usd, phone_international, tags_15, tags_16, order_fields",
    "payments <order_id>: each attempt's status, amount type and the payment entity's field names",
    "refund <order_id>: a Re 1 refund twice with one refund id (same refund?)",
    "webhook-listen: one delivery on 127.0.0.1: header names, timestamp unit, signature check, "
    "field names",
)
#: Fields that carry a person or a payment instrument: their presence, never their content, is
#: what gets reported.
PERSON_FIELDS = ("customer_details", "payment_method", "payer_vpa", "bank_reference", "card")


def parse(raw: bytes) -> Any:
    """JSON with every non-integer number as a `Decimal`: an amount never becomes a float."""
    try:
        return json.loads(raw, parse_float=Decimal)
    except ValueError:
        return None


def encode(payload: Mapping[str, Any], *, numbers: Mapping[str, str] | None = None) -> bytes:
    """JSON where the named top-level fields are written as **bare numbers** exactly as given
    (`"1.00"` stays `1.00`, not `1.0` and not a string) — the way Cashfree takes an amount."""
    placeholders = {name: f"@@{name}@@" for name in (numbers or {})}
    text = json.dumps({**payload, **placeholders}, separators=(",", ":"))
    for name, literal in (numbers or {}).items():
        text = text.replace(f'"{placeholders[name]}"', literal)
    return text.encode()


def type_name(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    return "absent" if value is None else type(value).__name__


class Client:
    def __init__(
        self,
        app_id: str,
        secret_key: str,
        transport: httpx.BaseTransport | None,
        report: common.Reporter,
    ) -> None:
        self._app_id = app_id
        self._secret = secret_key
        self._client = httpx.Client(
            base_url=SANDBOX_API,
            transport=transport,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        )
        self.report = report

    def call(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        secret: str | None = None,
    ) -> tuple[int, Any]:
        merged = {
            "x-client-id": self._app_id,
            "x-client-secret": secret if secret is not None else self._secret,
            "x-api-version": PINNED_API_VERSION,
            "x-request-id": str(uuid.uuid4()),
            "accept": "application/json",
            **({"content-type": "application/json"} if body is not None else {}),
            **(headers or {}),
        }
        response = self._client.request(method, path, content=body, headers=merged)
        return response.status_code, parse(response.content)


def _error(body: Any) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, None
    code, kind = body.get("code"), body.get("type")
    return (code if isinstance(code, str) else None), (kind if isinstance(kind, str) else None)


def _new_order(
    order_id: str,
    *,
    amount: str = "1.00",
    currency: str = "INR",
    phone: str = "9999999999",
    tags: int = 0,
) -> bytes:
    payload: dict[str, Any] = {
        "order_id": order_id,
        "order_currency": currency,
        "customer_details": {"customer_id": "smoke" + order_id[-12:], "customer_phone": phone},
    }
    if tags:
        payload["order_tags"] = {f"t{n}": "1" for n in range(tags)}
    return encode(payload, numbers={"order_amount": amount})


def _order_id() -> str:
    return "smoke" + uuid.uuid4().hex[:24]  # 29 characters: inside Cashfree's 50


def probe(client: Client) -> None:
    say = client.report.line
    missing = "smoke-missing-" + uuid.uuid4().hex[:12]
    status, body = client.call("GET", f"/orders/{missing}")
    code, kind = _error(body)
    say("credentials", status=status, code=code, error_type=kind, authenticated=status != 401)
    status, body = client.call("GET", f"/orders/{missing}", secret=client._secret + "x")
    code, kind = _error(body)
    say("bad_key", status=status, code=code, error_type=kind)
    if client.call("GET", f"/orders/{missing}")[0] == 401:
        client.report.text("stopping: the key pair does not authenticate; fix it and re-run")
        return

    status, body = client.call("GET", f"/orders/{missing}")
    say("api_version_pinned", status=status, accepted=status in (200, 404))
    status, body = client.call("GET", f"/orders/{missing}", headers={"x-api-version": "1999-01-01"})
    say("api_version_bogus", status=status, code=_error(body)[0])

    order_id = _order_id()
    status, body = client.call("POST", "/orders", body=_new_order(order_id))
    say(
        "order_create",
        status=status,
        order=common.short(order_id),
        code=_error(body)[0],
        order_status=body.get("order_status") if isinstance(body, dict) else None,
        amount_type=type_name(body.get("order_amount")) if isinstance(body, dict) else None,
        has_payment_session_id=isinstance(body, dict) and bool(body.get("payment_session_id")),
    )
    created = status == 200
    if created:
        status, body = client.call("GET", f"/orders/{order_id}")
        say(
            "order_refetch",
            status=status,
            has_payment_session_id=isinstance(body, dict) and bool(body.get("payment_session_id")),
        )
        client.report.names("order_fields", body.keys() if isinstance(body, dict) else [])
        status, body = client.call("POST", "/orders", body=_new_order(order_id))
        say("order_duplicate", status=status, code=_error(body)[0])

    key = "smoke-" + uuid.uuid4().hex
    second_id = _order_id()
    first_status, first = client.call(
        "POST", "/orders", body=_new_order(second_id), headers={"x-idempotency-key": key}
    )
    again_status, again = client.call(
        "POST", "/orders", body=_new_order(second_id), headers={"x-idempotency-key": key}
    )
    say(
        "order_idempotency_key",
        first_status=first_status,
        second_status=again_status,
        same_order_back=isinstance(first, dict)
        and isinstance(again, dict)
        and first.get("cf_order_id") is not None
        and first.get("cf_order_id") == again.get("cf_order_id"),
        second_code=_error(again)[0],
    )

    for step, kwargs in (
        ("amount_below_minimum", {"amount": "0.99"}),
        ("amount_three_decimals", {"amount": "1.005"}),
        ("currency_usd", {"currency": "USD"}),
        ("phone_international", {"phone": "+14155550100"}),
        ("tags_15", {"tags": 15}),
        ("tags_16", {"tags": 16}),
    ):
        status, body = client.call("POST", "/orders", body=_new_order(_order_id(), **kwargs))
        say(step, status=status, code=_error(body)[0], created=status == 200)
    if created:
        client.report.text(
            f"to complete one by hand: pay order {common.short(order_id)} in the sandbox Checkout "
            "(the payment_session_id it was created with), then run `payments <order_id>`"
        )


def payments(client: Client, order_id: str) -> None:
    status, body = client.call("GET", f"/orders/{order_id}/payments")
    items = body if isinstance(body, list) else []
    client.report.line(
        "order_payments", status=status, count=len(items), is_list=isinstance(body, list)
    )
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        client.report.line(
            f"attempt_{n}",
            payment=common.short(item.get("cf_payment_id")),
            status=item.get("payment_status"),
            amount_type=type_name(item.get("payment_amount")),
            currency=item.get("payment_currency"),
            group=item.get("payment_group"),
        )
        client.report.names(f"attempt_{n}_fields", item.keys())
        client.report.line(
            f"attempt_{n}_person_fields",
            **{name: name in item for name in PERSON_FIELDS},
        )


def refund(client: Client, order_id: str) -> None:
    refund_id = "smokerf" + uuid.uuid4().hex[:20]
    request = encode(
        {"refund_id": refund_id, "refund_speed": "STANDARD"}, numbers={"refund_amount": "1.00"}
    )
    first_status, first = client.call("POST", f"/orders/{order_id}/refunds", body=request)
    second_status, second = client.call("POST", f"/orders/{order_id}/refunds", body=request)
    client.report.line(
        "refund_first",
        status=first_status,
        refund=common.short(first.get("cf_refund_id") if isinstance(first, dict) else None),
        refund_status=first.get("refund_status") if isinstance(first, dict) else None,
        code=_error(first)[0],
    )
    client.report.line(
        "refund_second",
        status=second_status,
        code=_error(second)[0],
        same_refund=isinstance(first, dict)
        and isinstance(second, dict)
        and first.get("cf_refund_id") is not None
        and first.get("cf_refund_id") == second.get("cf_refund_id"),
    )


def inspect_delivery(
    report: common.Reporter, headers: Mapping[str, str], body: bytes, secret: str
) -> None:
    """What one webhook delivery looked like — names, a unit and a boolean, never a value."""
    lowered = {name.lower(): value for name, value in headers.items()}
    report.names("webhook_header_names", lowered.keys())
    timestamp = lowered.get("x-webhook-timestamp", "")
    signature = lowered.get("x-webhook-signature", "")
    digits = len(timestamp) if timestamp.isdigit() else 0
    expected = base64.b64encode(
        hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    ).decode()
    report.line(
        "webhook_signature",
        has_timestamp_header=bool(timestamp),
        has_signature_header=bool(signature),
        timestamp_digits=digits,
        timestamp_unit="milliseconds" if digits == 13 else "seconds" if digits == 10 else "unknown",
        signature_verifies=bool(signature)
        and hmac.compare_digest(signature.encode(), expected.encode()),
        version_header=lowered.get("x-webhook-version"),
    )
    parsed = parse(body)
    if not isinstance(parsed, dict):
        report.line("webhook_body", is_json_object=False)
        return
    report.line("webhook_body", is_json_object=True, type=parsed.get("type"))
    report.names("webhook_top_level_fields", parsed.keys())
    data = parsed.get("data")
    if isinstance(data, dict):
        report.names("webhook_data_fields", data.keys())
        for section in ("order", "payment", "customer_details"):
            inner = data.get(section)
            if isinstance(inner, dict):
                report.names(f"webhook_{section}_fields", inner.keys())
        report.line(
            "webhook_person_fields",
            **{name: name in data or name in (data.get("payment") or {}) for name in PERSON_FIELDS},
        )


def listen(report: common.Reporter, secret: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = min(int(self.headers.get("content-length") or 0), 1 << 20)
            body = self.rfile.read(length)
            self.send_response(200)
            self.end_headers()
            inspect_delivery(report, dict(self.headers.items()), body, secret)

        def log_message(self, format: str, *args: object) -> None:
            return  # the default logs the client address and request line to stderr

    server = HTTPServer(("127.0.0.1", port), Handler)
    report.text(f"listening on 127.0.0.1:{port} for one delivery; Ctrl-C to stop")
    try:
        server.handle_request()
    except KeyboardInterrupt:
        report.text("stopped")
    finally:
        server.server_close()


def check_sandbox_keys(environ: Mapping[str, str], app_id: str, secret_key: str) -> None:
    if (environ.get("CASHFREE_ENV") or "sandbox").strip().lower() != "sandbox":
        raise common.Refused("refused: CASHFREE_ENV is set and is not sandbox")
    if not app_id.startswith("TEST"):
        raise common.Refused("refused: only a sandbox App ID (TEST...) is accepted")
    if secret_key.startswith("cfsk_ma_prod_"):
        raise common.Refused("refused: that is a production secret key")


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    modules: Mapping[str, Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "command", choices=["plan", "probe", "payments", "refund", "webhook-listen"]
    )
    parser.add_argument("target", nargs="?", help="an order id (payments, refund)")
    parser.add_argument("--confirm", action="store_true", help="actually call the Cashfree sandbox")
    parser.add_argument("--port", type=int, default=8765, help="webhook-listen only")
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
        keys = common.read_env(environment, "CASHFREE_APP_ID", "CASHFREE_SECRET_KEY")
        check_sandbox_keys(environment, keys["CASHFREE_APP_ID"], keys["CASHFREE_SECRET_KEY"])
        if args.command in ("payments", "refund") and not args.target:
            raise common.Refused(f"refused: {args.command} needs an order id")
    except common.Refused as refusal:
        stream.write(str(refusal) + "\n")
        return 2
    report = common.Reporter(stream, secrets=[keys["CASHFREE_SECRET_KEY"], keys["CASHFREE_APP_ID"]])
    if args.command == "webhook-listen":
        listen(report, keys["CASHFREE_SECRET_KEY"], args.port)
        return 0
    client = Client(keys["CASHFREE_APP_ID"], keys["CASHFREE_SECRET_KEY"], transport, report)
    try:
        if args.command == "probe":
            probe(client)
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
