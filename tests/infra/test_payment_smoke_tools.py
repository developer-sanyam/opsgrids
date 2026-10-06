"""The owner-run payment smoke tools (`tools/payments_smoke_*.py`, ADR-0032) — their *rules*.

These are the one place in the repository that can call a real payment provider, so the tests are
about what they must never do, run entirely against `httpx.MockTransport`:

* nothing is called without `--confirm`, in CI, under pytest, or when `ENVIRONMENT` is a deployed
  one — and a refusal reaches the transport with **zero** requests;
* a key that is not provably test-mode is refused before a request is built, and a refusal never
  prints the key;
* only the provider's own test/sandbox host is ever addressed;
* the output carries ids, statuses, codes, counts, booleans and field *names* — never a body, a
  description, an email, a phone number, a header value or a credential;
* the keys come from the process environment: the tools do not read `.env`, import service
  `Settings`, or open a file;
* no workflow, Makefile target or test invokes them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import sys
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import payments_smoke_cashfree as cashfree  # noqa: E402
import payments_smoke_common as common  # noqa: E402
import payments_smoke_razorpay as razorpay  # noqa: E402

# Shaped like nothing real: the repo-wide secret-shape scan (test_payment_secret_shapes) covers
# this file too.
RZP_ID = "rzp_test_SmokeKey0001"  # pragma: allowlist secret
RZP_SECRET = "dev-only-rzp-secret-for-smoke-tests"  # pragma: allowlist secret
CF_APP = "TESTdev-only-app"
CF_SECRET = "dev-only-cashfree-secret-for-smoke"  # pragma: allowlist secret
PERSON = "payer.person@example.com"
PHONE_DIGITS = "9876543210"

RAZORPAY_ENV = {"RAZORPAY_KEY_ID": RZP_ID, "RAZORPAY_KEY_SECRET": RZP_SECRET}
CASHFREE_ENV = {"CASHFREE_APP_ID": CF_APP, "CASHFREE_SECRET_KEY": CF_SECRET}


class Recorder:
    """A transport that records every request and answers through `respond`."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda request: httpx.Response(500))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def run_tool(
    tool: Any,
    argv: list[str],
    environ: dict[str, str],
    recorder: Recorder,
    *,
    modules: dict[str, Any] | None = None,
) -> tuple[int, str]:
    out = io.StringIO()
    code = tool.run(
        argv,
        environ=environ,
        transport=recorder.transport,
        out=out,
        modules={} if modules is None else modules,
    )
    return code, out.getvalue()


# --------------------------------------------------------------------------- the shared guard
class TestGuard:
    @pytest.mark.parametrize(
        ("environ", "why"),
        [
            ({"CI": "true"}, "CI"),
            ({"GITHUB_ACTIONS": "true"}, "CI"),
            ({"ENVIRONMENT": "production"}, "deployed"),
            ({"ENVIRONMENT": "staging"}, "deployed"),
            ({"ENVIRONMENT": " UAT "}, "deployed"),
            ({"ENVIRONMENT": "ci"}, "deployed"),
            ({"PYTEST_CURRENT_TEST": "x"}, "pytest"),
        ],
    )
    def test_each_refusal(self, environ: dict[str, str], why: str) -> None:
        with pytest.raises(common.Refused):
            common.guard(environ, confirmed=True, modules={})

    def test_confirmation_is_required(self) -> None:
        with pytest.raises(common.Refused, match="--confirm"):
            common.guard({}, confirmed=False, modules={})

    def test_a_laptop_with_confirm_passes(self) -> None:
        common.guard({"ENVIRONMENT": "development"}, confirmed=True, modules={})
        common.guard({}, confirmed=True, modules={})

    def test_pytest_in_the_process_refuses_even_with_confirm(self) -> None:
        # No `modules` override: the real `sys.modules`, which holds pytest right now.
        with pytest.raises(common.Refused, match="pytest"):
            common.guard({}, confirmed=True)

    def test_a_refusal_names_the_rule_and_never_a_value(self) -> None:
        with pytest.raises(common.Refused) as refused:
            common.guard(
                {"ENVIRONMENT": "production", "CI": RZP_SECRET}, confirmed=True, modules={}
            )
        assert RZP_SECRET not in str(refused.value)


class TestReporter:
    def make(self) -> tuple[common.Reporter, io.StringIO]:
        out = io.StringIO()
        return common.Reporter(out, secrets=[RZP_SECRET, CF_APP]), out

    def test_only_plain_short_values_pass(self) -> None:
        report, out = self.make()
        report.line("step", status=200, ok=True, code="BAD_REQUEST_ERROR", missing=None)
        assert out.getvalue() == "step status=200 ok=true code=BAD_REQUEST_ERROR missing=none\n"

    @pytest.mark.parametrize(
        "unsafe",
        [PERSON, "has a space", '{"a": 1}', "x" * 65, f"prefix-{RZP_SECRET}", f"{CF_APP}x", "a\nb"],
    )
    def test_everything_else_is_redacted(self, unsafe: str) -> None:
        report, out = self.make()
        report.line("step", value=unsafe)
        assert out.getvalue() == "step value=<redacted>\n"

    def test_a_credential_is_redacted_even_when_it_looks_plain(self) -> None:
        out = io.StringIO()
        report = common.Reporter(out, secrets=["plainlookingsecret"])
        report.line("step", value="plainlookingsecret")
        assert "plainlookingsecret" not in out.getvalue()

    def test_names_print_identifiers_and_count_the_rest(self) -> None:
        report, out = self.make()
        report.names("fields", ["email", "contact", PERSON, "a b", RZP_SECRET])
        assert out.getvalue() == "fields count=5 names=contact,email\n"

    def test_identifiers_are_truncated(self) -> None:
        assert common.short("order_" + "a" * 40) == "order_aaaaaaaa..."
        assert common.short("order_abc") == "order_abc"
        assert common.short(None) == ""


class TestReadEnv:
    def test_missing_keys_are_named_and_no_value_is_shown(self) -> None:
        with pytest.raises(common.Refused) as refused:
            common.read_env({"A": "secret-a"}, "A", "B")
        assert "B" in str(refused.value)
        assert "secret-a" not in str(refused.value)

    def test_blank_counts_as_missing(self) -> None:
        with pytest.raises(common.Refused):
            common.read_env({"A": "   "}, "A")


# --------------------------------------------------------------------------- both tools' refusals
TOOLS_UNDER_TEST = [
    pytest.param(razorpay, RAZORPAY_ENV, ["probe"], id="razorpay"),
    pytest.param(cashfree, CASHFREE_ENV, ["probe"], id="cashfree"),
]


@pytest.mark.parametrize(("tool", "keys", "argv"), TOOLS_UNDER_TEST)
class TestEveryRefusalSendsNothing:
    def test_without_confirm(self, tool: Any, keys: dict[str, str], argv: list[str]) -> None:
        recorder = Recorder()
        code, out = run_tool(tool, argv, dict(keys), recorder)
        assert code == 2
        assert "--confirm" in out
        assert recorder.requests == []

    @pytest.mark.parametrize(
        "extra",
        [
            {"CI": "1"},
            {"GITHUB_ACTIONS": "true"},
            {"ENVIRONMENT": "production"},
            {"ENVIRONMENT": "staging"},
        ],
    )
    def test_in_ci_or_a_deployed_environment(
        self, tool: Any, keys: dict[str, str], argv: list[str], extra: dict[str, str]
    ) -> None:
        recorder = Recorder()
        code, out = run_tool(tool, [*argv, "--confirm"], {**keys, **extra}, recorder)
        assert code == 2
        assert recorder.requests == []
        for value in keys.values():
            assert value not in out

    def test_under_pytest_without_the_modules_override(
        self, tool: Any, keys: dict[str, str], argv: list[str]
    ) -> None:
        recorder = Recorder()
        out = io.StringIO()
        code = tool.run(
            [*argv, "--confirm"], environ=dict(keys), transport=recorder.transport, out=out
        )
        assert code == 2
        assert "pytest" in out.getvalue()
        assert recorder.requests == []

    def test_with_no_keys(self, tool: Any, keys: dict[str, str], argv: list[str]) -> None:
        recorder = Recorder()
        code, out = run_tool(tool, [*argv, "--confirm"], {}, recorder)
        assert code == 2
        for name in keys:
            assert name in out
        assert recorder.requests == []

    def test_plan_calls_nothing_and_needs_no_keys(
        self, tool: Any, keys: dict[str, str], argv: list[str]
    ) -> None:
        recorder = Recorder()
        code, out = run_tool(tool, ["plan"], {}, recorder)
        assert code == 2
        assert "probe" in out
        assert recorder.requests == []

    def test_a_target_is_required_where_one_is_needed(
        self, tool: Any, keys: dict[str, str], argv: list[str]
    ) -> None:
        recorder = Recorder()
        code, out = run_tool(tool, ["refund", "--confirm"], dict(keys), recorder)
        assert code == 2
        assert "needs" in out
        assert recorder.requests == []


class TestOnlyTestKeysAreAccepted:
    @pytest.mark.parametrize(
        "key_id",
        ["rzp_live_" + "Abcdef012345", "Abcdef012345", "rzp_tes_Abcdef012345", ""],
    )
    def test_razorpay_refuses_a_non_test_key_id(self, key_id: str) -> None:
        recorder = Recorder()
        code, out = run_tool(
            razorpay,
            ["probe", "--confirm"],
            {"RAZORPAY_KEY_ID": key_id or "x", "RAZORPAY_KEY_SECRET": RZP_SECRET},
            recorder,
        )
        assert code == 2
        assert recorder.requests == []
        assert key_id not in out or key_id == ""
        assert RZP_SECRET not in out

    def test_cashfree_refuses_a_production_secret(self) -> None:
        recorder = Recorder()
        secret = "cfsk_ma_" + "prod_" + "dev-only-not-real"
        code, out = run_tool(
            cashfree,
            ["probe", "--confirm"],
            {"CASHFREE_APP_ID": CF_APP, "CASHFREE_SECRET_KEY": secret},
            recorder,
        )
        assert code == 2
        assert recorder.requests == []
        assert secret not in out

    def test_cashfree_refuses_a_non_sandbox_app_id(self) -> None:
        recorder = Recorder()
        code, out = run_tool(
            cashfree,
            ["probe", "--confirm"],
            {"CASHFREE_APP_ID": "PRODdev-only-app", "CASHFREE_SECRET_KEY": CF_SECRET},
            recorder,
        )
        assert code == 2
        assert recorder.requests == []
        assert "PRODdev-only-app" not in out

    def test_cashfree_refuses_when_the_environment_variable_says_production(self) -> None:
        recorder = Recorder()
        code, _ = run_tool(
            cashfree,
            ["probe", "--confirm"],
            {**CASHFREE_ENV, "CASHFREE_ENV": "production"},
            recorder,
        )
        assert code == 2
        assert recorder.requests == []


# --------------------------------------------------------------------------- a Razorpay fake
class RazorpayWorld:
    """Just enough of Razorpay's test mode to run every probe, with traps in its answers: a person's
    email and phone in descriptions and notes, a `contains` receipt filter, a duplicate-receipt
    400."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}

    @staticmethod
    def error(status: int, description: str, code: str = "BAD_REQUEST_ERROR") -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": code, "description": f"{description} ({PERSON})"}}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        sent = base64.b64decode(auth.removeprefix("Basic ")).decode()
        if sent != f"{RZP_ID}:{RZP_SECRET}":
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": "BAD_REQUEST_ERROR",
                        "description": "The api key provided is invalid",
                    }
                },
            )
        path = request.url.path
        if request.method == "GET" and path == "/v1/orders":
            receipt = request.url.params.get("receipt")
            items = [o for o in self.orders.values() if receipt is None or receipt in o["receipt"]]
            return httpx.Response(200, json={"entity": "collection", "items": items})
        if request.method == "POST" and path == "/v1/orders":
            body = json.loads(request.content)
            if body["currency"] != "INR":
                return self.error(400, "Currency is not supported")
            if body["amount"] < 100:
                return self.error(400, "Order amount less than minimum amount allowed")
            if any(o["receipt"] == body["receipt"] for o in self.orders.values()):
                return self.error(400, "Order receipt should be unique.")
            order = {
                "id": "order_" + uuid.uuid4().hex[:14],
                "amount": body["amount"],
                "currency": "INR",
                "receipt": body["receipt"],
                "status": "created",
                "notes": {"payer": PERSON},
            }
            if "payment_capture" in body:
                order["payment_capture"] = body["payment_capture"]
            self.orders[order["id"]] = order
            return httpx.Response(200, json=order)
        match = re.fullmatch(r"/v1/orders/(order_\w+)/payments", path)
        if request.method == "GET" and match:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "pay_" + "a" * 14,
                            "status": "captured",
                            "amount": 100,
                            "currency": "INR",
                            "email": PERSON,
                            "contact": "+91" + PHONE_DIGITS,
                            "vpa": "payer@upi",
                            "notes": [],
                            "card": {"last4": "1111"},
                        }
                    ]
                },
            )
        match = re.fullmatch(r"/v1/payments/(pay_\w+)/refund", path)
        if request.method == "POST" and match:
            key = request.headers["x-refund-idempotency"]
            refund = self.refunds.setdefault(
                key, {"id": "rfnd_" + uuid.uuid4().hex[:14], "status": "processed"}
            )
            return httpx.Response(200, json=refund)
        return httpx.Response(
            404, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "x"}}
        )


def razorpay_run(argv: list[str], world: RazorpayWorld | None = None) -> tuple[int, str, Recorder]:
    recorder = Recorder(world or RazorpayWorld())
    code, out = run_tool(razorpay, argv, dict(RAZORPAY_ENV), recorder)
    return code, out, recorder


class TestRazorpayProbe:
    def test_every_question_is_answered_on_its_own_line(self) -> None:
        code, out, _ = razorpay_run(["probe", "--confirm"])
        assert code == 0
        steps = [line.split()[0] for line in out.splitlines() if line.split()]
        for expected in (
            "credentials",
            "bad_key",
            "order_capture_true",
            "order_capture_unset",
            "lookup_exact",
            "lookup_prefix",
            "duplicate_receipt",
            "amount_below_minimum",
            "currency_usd",
            "order_payments_empty",
        ):
            assert expected in steps

    def test_the_observed_answers_are_the_ones_printed(self) -> None:
        _, out, _ = razorpay_run(["probe", "--confirm"])
        lines = {line.split()[0]: line for line in out.splitlines() if line.split()}
        assert "status=401" in lines["bad_key"]
        assert "is_invalid_key_description=true" in lines["bad_key"]
        assert "echoes_payment_capture=true" in lines["order_capture_true"]
        assert "echoes_payment_capture=false" in lines["order_capture_unset"]
        assert "exact_match=true" in lines["lookup_exact"]
        assert "prefix_matches_the_full_receipt=true" in lines["lookup_prefix"]
        assert "status=400" in lines["duplicate_receipt"]
        assert "description_mentions_unique=true" in lines["duplicate_receipt"]
        assert "created_a_second_order=false" in lines["duplicate_receipt"]
        assert "status=400" in lines["amount_below_minimum"]
        assert "created=false" in lines["currency_usd"]

    def test_a_bad_key_stops_the_run_after_the_first_question(self) -> None:
        recorder = Recorder(lambda request: httpx.Response(401, json={"error": {"code": "X"}}))
        code, out = run_tool(razorpay, ["probe", "--confirm"], dict(RAZORPAY_ENV), recorder)
        assert code == 0
        assert "status=401" in out
        assert "stopping" in out
        assert len(recorder.requests) == 1

    def test_only_razorpay_is_addressed_and_only_the_documented_paths(self) -> None:
        _, _, recorder = razorpay_run(["probe", "--confirm"])
        assert {r.url.host for r in recorder.requests} == {"api.razorpay.com"}
        assert all(r.url.path.startswith("/v1/") for r in recorder.requests)
        assert {r.method for r in recorder.requests} <= {"GET", "POST"}

    def test_every_order_is_inr_at_least_a_rupee_with_a_unique_receipt(self) -> None:
        _, _, recorder = razorpay_run(["probe", "--confirm"])
        bodies = [
            json.loads(r.content)
            for r in recorder.requests
            if r.method == "POST" and r.url.path == "/v1/orders"
        ]
        # The deliberately-refused ones are the below-minimum and the USD probes; every other
        # body is the real thing.
        deliberate = [b for b in bodies if b["amount"] < 100 or b["currency"] != "INR"]
        assert len(deliberate) == 2
        receipts = [b["receipt"] for b in bodies]
        assert len(set(receipts)) == len(receipts) - 1  # the duplicate probe repeats one on purpose
        assert all(len(r) <= 40 for r in receipts)

    def test_a_second_run_does_not_collide_with_the_first(self) -> None:
        world = RazorpayWorld()
        razorpay_run(["probe", "--confirm"], world)
        _, out, _ = razorpay_run(["probe", "--confirm"], world)
        line = next(x for x in out.splitlines() if x.startswith("order_capture_true"))
        assert "status=200" in line

    def test_nothing_that_identifies_a_person_or_a_credential_is_printed(self) -> None:
        _, out, _ = razorpay_run(["probe", "--confirm"])
        for forbidden in (PERSON, RZP_SECRET, RZP_ID, PHONE_DIGITS, "{", '"', "@", "Basic"):
            assert forbidden not in out


class TestRazorpayPaymentsAndRefund:
    def test_payments_prints_field_names_and_never_their_values(self) -> None:
        code, out, _ = razorpay_run(["payments", "order_abc123", "--confirm"])
        assert code == 0
        names = next(x for x in out.splitlines() if x.startswith("attempt_0_fields"))
        assert "email" in names and "contact" in names and "vpa" in names and "card" in names
        assert "email=true" in out and "contact=true" in out
        for forbidden in (PERSON, PHONE_DIGITS, "payer@upi", "1111", RZP_SECRET):
            assert forbidden not in out

    def test_refund_sends_the_platforms_key_shape_and_reports_idempotence(self) -> None:
        code, out, recorder = razorpay_run(["refund", "pay_aaaaaaaaaaaaaa", "--confirm"])
        assert code == 0
        keys = {r.headers["x-refund-idempotency"] for r in recorder.requests}
        assert len(keys) == 1
        assert re.fullmatch(r"rfd[0-9a-f]{36}", next(iter(keys)))
        assert "same_refund=true" in out
        assert {json.loads(r.content)["amount"] for r in recorder.requests} == {100}


class TestRazorpayTransportFailure:
    def test_an_error_is_reported_by_type_only(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {RZP_SECRET} for {PERSON}")

        recorder = Recorder(boom)
        code, out = run_tool(razorpay, ["probe", "--confirm"], dict(RAZORPAY_ENV), recorder)
        assert code == 1
        assert "ConnectError" in out
        assert RZP_SECRET not in out and PERSON not in out


# --------------------------------------------------------------------------- a Cashfree fake
class CashfreeWorld:
    """Cashfree's sandbox as far as the probes reach, with traps in its answers."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.idempotent: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self.raw_bodies: list[bytes] = []

    @staticmethod
    def error(status: int, code: str, kind: str) -> httpx.Response:
        return httpx.Response(
            status, json={"code": code, "type": kind, "message": f"bad request for {PERSON}"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-client-id") != CF_APP:
            return self.error(401, "authentication_error", "authentication_error")
        if request.headers.get("x-client-secret") != CF_SECRET:
            return self.error(401, "authentication_error", "authentication_error")
        if request.headers.get("x-api-version") != "2025-01-01":
            return self.error(400, "request_failed", "invalid_request_error")
        path = request.url.path.removeprefix("/pg")
        if request.method == "POST" and path == "/orders":
            self.raw_bodies.append(request.content)
            body = json.loads(request.content, parse_float=Decimal)
            amount = body["order_amount"]
            tags = body.get("order_tags") or {}
            phone = body["customer_details"]["customer_phone"]
            if body["order_currency"] != "INR":
                return self.error(400, "order_currency_invalid", "invalid_request_error")
            if amount < Decimal("1") or amount != amount.quantize(Decimal("0.01")):
                return self.error(400, "order_amount_invalid", "invalid_request_error")
            if not phone.isdigit() or len(phone) != 10:
                return self.error(
                    400, "customer_details.customer_phone_invalid", "invalid_request_error"
                )
            if len(tags) > 15:
                return self.error(400, "order_tags_invalid", "invalid_request_error")
            key = request.headers.get("x-idempotency-key")
            if key and key in self.idempotent:
                return httpx.Response(200, json=self.idempotent[key])
            if body["order_id"] in self.orders:
                return self.error(409, "order_already_exists", "invalid_request_error")
            order = {
                "cf_order_id": uuid.uuid4().int % 10**9,
                "order_id": body["order_id"],
                "order_amount": 1.0,
                "order_currency": "INR",
                "order_status": "ACTIVE",
                "payment_session_id": "session_" + uuid.uuid4().hex,
                "customer_details": {"customer_id": "c", "customer_email": PERSON},
            }
            self.orders[body["order_id"]] = order
            if key:
                self.idempotent[key] = order
            return httpx.Response(200, json=order)
        match = re.fullmatch(r"/orders/([^/]+)", path)
        if request.method == "GET" and match:
            order = self.orders.get(match.group(1))
            if order is None:
                return self.error(404, "order_not_found", "invalid_request_error")
            return httpx.Response(200, json=order)
        match = re.fullmatch(r"/orders/([^/]+)/payments", path)
        if request.method == "GET" and match:
            return httpx.Response(
                200,
                json=[
                    {
                        "cf_payment_id": 1234567890,
                        "payment_status": "SUCCESS",
                        "payment_amount": 1.0,
                        "payment_currency": "INR",
                        "payment_group": "upi",
                        "payment_method": {"upi": {"upi_id": "payer@upi"}},
                        "customer_details": {
                            "customer_email": PERSON,
                            "customer_phone": PHONE_DIGITS,
                        },
                        "bank_reference": "ref-" + PHONE_DIGITS,
                    }
                ],
            )
        match = re.fullmatch(r"/orders/([^/]+)/refunds", path)
        if request.method == "POST" and match:
            body = json.loads(request.content, parse_float=Decimal)
            refund = self.refunds.setdefault(
                body["refund_id"],
                {"cf_refund_id": "rf_" + uuid.uuid4().hex[:10], "refund_status": "PENDING"},
            )
            return httpx.Response(200, json=refund)
        return self.error(404, "route_not_found", "invalid_request_error")


def cashfree_run(argv: list[str], world: CashfreeWorld | None = None) -> tuple[int, str, Recorder]:
    recorder = Recorder(world or CashfreeWorld())
    code, out = run_tool(cashfree, argv, dict(CASHFREE_ENV), recorder)
    return code, out, recorder


class TestCashfreeProbe:
    def test_every_question_is_answered_on_its_own_line(self) -> None:
        code, out, _ = cashfree_run(["probe", "--confirm"])
        assert code == 0
        steps = {line.split()[0] for line in out.splitlines() if line.split()}
        for expected in (
            "credentials",
            "bad_key",
            "api_version_pinned",
            "api_version_bogus",
            "order_create",
            "order_refetch",
            "order_fields",
            "order_duplicate",
            "order_idempotency_key",
            "amount_below_minimum",
            "amount_three_decimals",
            "currency_usd",
            "phone_international",
            "tags_15",
            "tags_16",
        ):
            assert expected in steps

    def test_the_observed_answers_are_the_ones_printed(self) -> None:
        _, out, _ = cashfree_run(["probe", "--confirm"])
        lines = {line.split()[0]: line for line in out.splitlines() if line.split()}
        assert "status=404" in lines["credentials"]
        assert "code=order_not_found" in lines["credentials"]
        assert "authenticated=true" in lines["credentials"]
        assert "status=401" in lines["bad_key"]
        assert "accepted=true" in lines["api_version_pinned"]
        assert "status=400" in lines["api_version_bogus"]
        assert "has_payment_session_id=true" in lines["order_create"]
        assert "amount_type=number" in lines["order_create"]
        assert "has_payment_session_id=true" in lines["order_refetch"]
        assert "status=409" in lines["order_duplicate"]
        assert "same_order_back=true" in lines["order_idempotency_key"]
        assert "created=false" in lines["amount_below_minimum"]
        assert "created=false" in lines["amount_three_decimals"]
        assert "created=false" in lines["phone_international"]
        assert "created=true" in lines["tags_15"]
        assert "created=false" in lines["tags_16"]

    def test_an_unauthenticated_key_stops_the_run(self) -> None:
        recorder = Recorder(
            lambda request: httpx.Response(401, json={"code": "authentication_error"})
        )
        code, out = run_tool(cashfree, ["probe", "--confirm"], dict(CASHFREE_ENV), recorder)
        assert code == 0
        assert "stopping" in out
        assert len(recorder.requests) == 3  # good key, bad key, good key again

    def test_only_the_sandbox_is_addressed_and_the_version_is_pinned(self) -> None:
        _, _, recorder = cashfree_run(["probe", "--confirm"])
        assert {r.url.host for r in recorder.requests} == {"sandbox.cashfree.com"}
        assert all(r.url.path.startswith("/pg/") for r in recorder.requests)
        versions = {r.headers["x-api-version"] for r in recorder.requests}
        assert versions == {"2025-01-01", "1999-01-01"}  # the second is the deliberate bogus probe

    def test_amounts_go_out_as_bare_decimal_numbers_never_floats_or_strings(self) -> None:
        world = CashfreeWorld()
        cashfree_run(["probe", "--confirm"], world)
        bodies = world.raw_bodies
        assert any(b'"order_amount":1.00,' in b or b'"order_amount":1.00}' in b for b in bodies)
        assert any(b'"order_amount":0.99' in b for b in bodies)
        assert any(b'"order_amount":1.005' in b for b in bodies)
        assert not any(b'"order_amount":"' in b for b in bodies)
        assert not any(b'"order_amount":1.0,' in b or b'"order_amount":1.0}' in b for b in bodies)

    def test_order_ids_fit_cashfrees_limit_and_are_unique_per_run(self) -> None:
        world = CashfreeWorld()
        cashfree_run(["probe", "--confirm"], world)
        ids = [json.loads(b)["order_id"] for b in world.raw_bodies]
        assert all(len(i) <= 50 and i.isalnum() for i in ids)
        assert (
            len(set(ids)) == len(ids) - 2
        )  # the duplicate probe and the idempotent replay repeat one

    def test_nothing_that_identifies_a_person_or_a_credential_is_printed(self) -> None:
        _, out, _ = cashfree_run(["probe", "--confirm"])
        for forbidden in (PERSON, CF_SECRET, CF_APP, PHONE_DIGITS, "{", '"', "@"):
            assert forbidden not in out
        assert (
            re.search(r"session_[0-9a-f]{32}", out) is None
        )  # the checkout session is a credential


class TestCashfreePaymentsAndRefund:
    def test_payments_reports_presence_of_person_fields_and_never_values(self) -> None:
        code, out, _ = cashfree_run(["payments", "someorder", "--confirm"])
        assert code == 0
        assert "amount_type=number" in out
        assert "customer_details=true" in out and "payment_method=true" in out
        names = next(x for x in out.splitlines() if x.startswith("attempt_0_fields"))
        assert "customer_details" in names and "payment_method" in names
        for forbidden in (PERSON, PHONE_DIGITS, "payer@upi", CF_SECRET):
            assert forbidden not in out

    def test_refund_repeats_one_refund_id_and_reports_whether_it_is_the_same_refund(self) -> None:
        code, out, recorder = cashfree_run(["refund", "someorder", "--confirm"])
        assert code == 0
        ids = {json.loads(r.content, parse_float=Decimal)["refund_id"] for r in recorder.requests}
        assert len(ids) == 1
        assert "same_refund=true" in out
        assert all(b'"refund_amount":1.00' in r.content for r in recorder.requests)


class TestEncoding:
    def test_a_named_number_is_written_exactly_as_given(self) -> None:
        assert cashfree.encode({"a": 1}, numbers={"order_amount": "1.00"}) == (
            b'{"a":1,"order_amount":1.00}'
        )
        assert cashfree.encode({}, numbers={"n": "0.99"}) == b'{"n":0.99}'

    def test_parse_keeps_decimals_exact(self) -> None:
        assert cashfree.parse(b'{"a": 19.99}') == {"a": Decimal("19.99")}
        assert cashfree.parse(b"not json") is None

    def test_type_names(self) -> None:
        assert cashfree.type_name(Decimal("1.5")) == "number"
        assert cashfree.type_name(3) == "number"
        assert cashfree.type_name("1.00") == "string"
        assert cashfree.type_name(True) == "boolean"
        assert cashfree.type_name(None) == "absent"


# --------------------------------------------------------------------------- the webhook listener
class TestWebhookInspection:
    BODY = json.dumps(
        {
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {"order_id": "o1", "order_amount": 1.0},
                "payment": {"cf_payment_id": 1, "payment_method": {"upi": {"upi_id": "payer@upi"}}},
                "customer_details": {"customer_email": PERSON, "customer_phone": PHONE_DIGITS},
            },
        }
    ).encode()

    def sign(self, timestamp: str, body: bytes, secret: str = CF_SECRET) -> str:
        digest = hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def inspect(self, headers: dict[str, str], body: bytes) -> str:
        out = io.StringIO()
        cashfree.inspect_delivery(
            common.Reporter(out, secrets=[CF_SECRET, CF_APP]), headers, body, CF_SECRET
        )
        return out.getvalue()

    def test_a_good_delivery_verifies_and_reports_the_timestamp_unit(self) -> None:
        stamp = "1760000000123"
        out = self.inspect(
            {
                "X-Webhook-Timestamp": stamp,
                "X-Webhook-Signature": self.sign(stamp, self.BODY),
                "X-Webhook-Version": "2025-01-01",
                "Content-Type": "application/json",
            },
            self.BODY,
        )
        assert "signature_verifies=true" in out
        assert "timestamp_unit=milliseconds" in out
        assert "timestamp_digits=13" in out
        names = "content-type,x-webhook-signature,x-webhook-timestamp,x-webhook-version"
        assert f"webhook_header_names count=4 names={names}" in out
        assert self.sign(stamp, self.BODY) not in out  # the header's *value* is never printed

    def test_seconds_are_told_from_milliseconds(self) -> None:
        stamp = "1760000000"
        out = self.inspect(
            {"x-webhook-timestamp": stamp, "x-webhook-signature": self.sign(stamp, self.BODY)},
            self.BODY,
        )
        assert "timestamp_unit=seconds" in out

    def test_a_tampered_or_missing_signature_does_not_verify(self) -> None:
        stamp = "1760000000"
        wrong = self.inspect(
            {
                "x-webhook-timestamp": stamp,
                "x-webhook-signature": self.sign(stamp, self.BODY, "other"),
            },
            self.BODY,
        )
        assert "signature_verifies=false" in wrong
        missing = self.inspect({}, self.BODY)
        assert "signature_verifies=false" in missing
        assert "has_signature_header=false" in missing

    def test_names_and_presence_are_reported_and_values_never(self) -> None:
        stamp = "1760000000"
        out = self.inspect(
            {"x-webhook-timestamp": stamp, "x-webhook-signature": self.sign(stamp, self.BODY)},
            self.BODY,
        )
        assert "webhook_top_level_fields" in out and "type" in out
        assert "webhook_customer_details_fields" in out
        assert "customer_email" in out  # a field *name*
        for forbidden in (PERSON, PHONE_DIGITS, "payer@upi", CF_SECRET):
            assert forbidden not in out

    def test_a_non_json_body_is_survived(self) -> None:
        out = self.inspect({}, b"<html>")
        assert "is_json_object=false" in out


# --------------------------------------------------------------------------- what they never do
SMOKE_TOOLS = sorted(TOOLS.glob("payments_smoke_*.py"))


class TestTheToolsStayInTheirLane:
    def test_the_tools_are_where_the_runbook_says(self) -> None:
        assert {p.name for p in SMOKE_TOOLS} == {
            "payments_smoke_cashfree.py",
            "payments_smoke_common.py",
            "payments_smoke_razorpay.py",
        }

    @pytest.mark.parametrize("path", SMOKE_TOOLS, ids=lambda p: p.name)
    def test_no_dotenv_no_settings_no_service_code_no_file_reads(self, path: Path) -> None:
        executable = "\n".join(
            line
            for line in _strip_docstrings(path.read_text()).splitlines()
            if not line.lstrip().startswith("#")
        )
        for needle in (
            "dotenv",
            "load_dotenv",
            "ServiceSettings",
            "pydantic_settings",
            "platform_core",
            "from app",
            "import app",
            "open(",
            "read_text",
            "subprocess",
            "os.system",
        ):
            assert needle not in executable, f"{path.name} uses {needle}"
        assert not re.search(r"\.env\b", executable), f"{path.name} names a .env file"

    @pytest.mark.parametrize(
        "path", [p for p in SMOKE_TOOLS if "common" not in p.name], ids=lambda p: p.name
    )
    def test_only_common_touches_the_process_environment(self, path: Path) -> None:
        assert "os.environ" not in _strip_docstrings(path.read_text())
        assert "import os" not in path.read_text()

    def test_no_key_arrives_by_argument(self) -> None:
        for tool in (razorpay, cashfree):
            recorder = Recorder()
            with pytest.raises(SystemExit):
                tool.run(["probe", "--key", "abc"], environ={}, transport=recorder.transport)
            assert recorder.requests == []

    def test_nothing_in_ci_the_makefile_or_the_tests_invokes_them(self) -> None:
        offenders = []
        for path in [REPO_ROOT / "Makefile", *(REPO_ROOT / ".github").rglob("*.yml")]:
            if path.is_file() and "payments_smoke" in path.read_text():
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        for path in (REPO_ROOT / "services").rglob("tests/**/*.py"):
            if "payments_smoke" in path.read_text():
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        assert offenders == []

    def test_the_only_hosts_in_the_tools_are_the_test_ones(self) -> None:
        hosts: set[str] = set()
        for path in SMOKE_TOOLS:
            hosts |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text()))
        # api.razorpay.com serves test and live alike (the key id's mode decides, and the tool
        # refuses anything but rzp_test_); Cashfree's production host must not appear at all.
        assert hosts - {"api.razorpay.com", "sandbox.cashfree.com", "127.0.0.1"} == set()


def _strip_docstrings(source: str) -> str:
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
