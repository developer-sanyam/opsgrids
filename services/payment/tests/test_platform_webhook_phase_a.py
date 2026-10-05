"""The platform webhook ingest, ready for the day it is made public (ADR-0029) — and private today.

The owner's decision is **poll-only**: no webhook path is public and the gateway's public-path lists
are untouched. Everything on this side is built and held to its promises now, so that day is only a
route-table edit:

* **Redaction before any real event flows.** The platform inbox is append-only; a real payment
  entity carries an email, a phone, a VPA and card hints. What is stored is an allow-list, still
  readable by `parse_webhook` (`services._identity_of`).
* **Cashfree** signs `timestamp + body` under the API secret, fails closed, caps the body before the
  HMAC, and answers 404 unless it is enabled — a public route can never run on an unconfigured box.
* **A checkout moves on a capture only**, and an event about another Order is a flagged conflict.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.api.webhooks import MAX_WEBHOOK_BODY_BYTES
from app.config import settings
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from tests import checkout_support as cs
from tests.helpers import audit_rows, emitted, inbox_rows, rows

pytestmark = pytest.mark.integration

RAZORPAY = "/api/v1/webhooks/razorpay"
CASHFREE = "/api/v1/webhooks/cashfree"
TS = "1697191215000"


def _razorpay_body(
    *,
    event: str,
    payment_id: str,
    order_id: str,
    org: uuid.UUID,
    charge_id: str,
    amount: int = 49_900,
    created_at: int = 1_700_000_000,
) -> bytes:
    entity = {
        "id": payment_id,
        "entity": "payment",
        "order_id": order_id,
        "amount": amount,
        "currency": "INR",
        "status": event.split(".")[1],
        "method": "upi",
        "email": "customer@example.com",
        "contact": "+919876543210",
        "vpa": "customer@okbank",
        "card": {"last4": "1111", "name": "A Customer"},
        "notes": {"organization_id": str(org), "charge_id": charge_id},
        "error_description": "Payment was declined by the bank"
        if event.endswith("failed")
        else None,
    }
    body = {
        "entity": "event",
        "account_id": "acc_platform",
        "event": event,
        "payload": {"payment": {"entity": entity}},
        "created_at": created_at,
    }
    return json.dumps(body).encode()


def _cashfree_body(
    *,
    etype: str = "PAYMENT_SUCCESS_WEBHOOK",
    status: str = "SUCCESS",
    order_id: str,
    org: uuid.UUID | None,
    charge_id: str | None,
    order_amount: str = "499.00",
    payment_amount: str = "499.00",
    cf_payment_id: int = 3001,
    event_time: str = "2026-10-06T10:15:15+05:30",
) -> bytes:
    tags: dict[str, str] = {}
    if org is not None:
        tags["organization_id"] = str(org)
    if charge_id is not None:
        tags["charge_id"] = charge_id
    tags_json = json.dumps(tags)
    text = (
        f'{{"type":"{etype}","event_time":"{event_time}","data":{{"order":{{"order_id":"{order_id}",'
        f'"order_amount":{order_amount},"order_currency":"INR","order_tags":{tags_json}}},'
        f'"payment":{{"cf_payment_id":{cf_payment_id},"payment_status":"{status}",'
        f'"payment_amount":{payment_amount},"payment_currency":"INR",'
        '"payment_method":{"upi":{"upi_id":"asha@okbank"}}},'
        '"customer_details":{"customer_name":"Asha Customer","customer_email":"asha@example.com",'
        '"customer_phone":"9876543210"}}}'
    )
    return text.encode()


async def _row(admin: AsyncSession, charge_id: str) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, provider_order_id "
        "FROM payment.charges WHERE id = :id",
        id=uuid.UUID(charge_id),
    )
    return found[0]


async def _razorpay_checkout(client: AsyncClient, org_id: uuid.UUID) -> tuple[Any, dict[str, Any]]:
    provider = cs.install(cs.razorpay_live())
    invoice_id, _ = await cs.seed_invoice(client, org_id)
    out = (await cs.open_checkout(client, org_id, invoice_id)).json()
    return provider, out


async def _cashfree_checkout(
    client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> tuple[CashfreeProvider, dict[str, Any]]:
    provider = cs.install(cs.cashfree_stub())
    monkeypatch.setattr(settings(), "payment_provider", "cashfree")
    invoice_id, _ = await cs.seed_invoice(client, org_id)
    out = (await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())).json()
    return provider, out  # type: ignore[return-value]


def _signed(provider: CashfreeProvider, body: bytes, **headers: str) -> dict[str, str]:
    return {
        "x-webhook-signature": provider.sign(body, TS),
        "x-webhook-timestamp": TS,
        "x-webhook-version": "2025-01-01",
        **headers,
    }


class TestRazorpayPlatformInboxIsRedacted:
    async def test_no_customer_detail_reaches_the_append_only_table_and_it_still_parses(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, out = await _razorpay_checkout(client, org_id)
        order_id = out["client_action"]["order_id"]
        body = _razorpay_body(
            event="payment.captured",
            payment_id="pay_1",
            order_id=order_id,
            org=org_id,
            charge_id=out["charge_id"],
        )
        response = await client.post(
            RAZORPAY,
            content=body,
            headers={"X-Razorpay-Signature": provider.sign(body), "X-Razorpay-Event-Id": "evt_1"},
        )
        assert response.status_code == 200 and response.json()["reconciled"] is True

        (row,) = await inbox_rows(admin_session, org_id)
        stored = json.dumps(row["payload"])
        for pii in (
            "customer@example.com",
            "+919876543210",
            "customer@okbank",
            "1111",
            "A Customer",
            "declined by the bank",
            "notes",
        ):
            assert pii not in stored, f"{pii!r} reached the inbox"
        entity = row["payload"]["payload"]["payment"]["entity"]
        assert entity["id"] == "pay_1" and entity["order_id"] == order_id
        assert (entity["amount"], entity["currency"]) == (49_900, "INR")
        # What identifies the event is intact, so a stored body is still recognised.
        sent = provider.parse_webhook(body=body)
        assert services._identity_of(provider, row["payload"]) == domain.derive_event_id(sent)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "captured"


class TestACheckoutMovesOnACaptureOnly:
    async def test_a_failed_payment_event_changes_nothing_and_announces_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, out = await _razorpay_checkout(client, org_id)
        before = await emitted(admin_session, org_id)
        body = _razorpay_body(
            event="payment.failed",
            payment_id="pay_failed",
            order_id=out["client_action"]["order_id"],
            org=org_id,
            charge_id=out["charge_id"],
        )
        response = await client.post(
            RAZORPAY,
            content=body,
            headers={"X-Razorpay-Signature": provider.sign(body), "X-Razorpay-Event-Id": "evt_f"},
        )
        assert response.status_code == 200 and response.json()["reconciled"] is False
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"], row["failure_reason"]) == (
            "created",
            None,
            None,
        )
        assert await emitted(admin_session, org_id) == before
        assert [r["disposition"] for r in await inbox_rows(admin_session, org_id)] == ["processed"]

    async def test_the_attempt_that_then_succeeds_is_not_a_provider_id_conflict(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, out = await _razorpay_checkout(client, org_id)
        order_id = out["client_action"]["order_id"]
        for n, (event, payment) in enumerate(
            [("payment.failed", "pay_a"), ("payment.captured", "pay_b")]
        ):
            body = _razorpay_body(
                event=event,
                payment_id=payment,
                order_id=order_id,
                org=org_id,
                charge_id=out["charge_id"],
                created_at=1_700_000_000 + n,
            )
            response = await client.post(
                RAZORPAY,
                content=body,
                headers={
                    "X-Razorpay-Signature": provider.sign(body),
                    "X-Razorpay-Event-Id": f"evt_{n}",
                },
            )
            assert response.status_code == 200, response.text
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"]) == ("captured", "pay_b")
        assert {r["disposition"] for r in await inbox_rows(admin_session, org_id)} == {"processed"}

    async def test_an_event_about_another_order_is_a_flagged_conflict_not_a_state_change(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`notes` can be stamped by a browser: a payment on somebody else's Order that names this
        charge in them must not capture it."""
        provider, out = await _razorpay_checkout(client, org_id)
        body = _razorpay_body(
            event="payment.captured",
            payment_id="pay_other",
            order_id="order_somebody_else",
            org=org_id,
            charge_id=out["charge_id"],
        )
        response = await client.post(
            RAZORPAY,
            content=body,
            headers={"X-Razorpay-Signature": provider.sign(body), "X-Razorpay-Event-Id": "evt_o"},
        )
        assert response.status_code == 200
        assert response.json()["ignored_reason"] == domain.REASON_ORDER_ID_CONFLICT
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"
        (row,) = await inbox_rows(admin_session, org_id)
        assert (row["disposition"], row["disposition_reason"]) == ("conflict", "order_id_conflict")
        flagged = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.webhook.ignored"
        ]
        assert len(flagged) == 1


class TestTheCashfreeRoute:
    async def test_it_is_a_404_unless_cashfree_is_enabled(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(
            cs.cashfree_stub()
        )  # the adapter exists; the deployment is not enabled
        body = _cashfree_body(order_id="o", org=org_id, charge_id=str(uuid.uuid4()))
        response = await client.post(CASHFREE, content=body, headers=_signed(provider, body))  # type: ignore[arg-type]
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "flag", [("payment_provider", "cashfree"), ("cashfree_env", "sandbox")]
    )
    async def test_either_switch_enables_it(
        self,
        client: AsyncClient,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        flag: tuple[str, str],
    ) -> None:
        monkeypatch.setattr(settings(), *flag)
        provider = cs.install(cs.cashfree_stub())
        body = _cashfree_body(order_id="o", org=None, charge_id=None)
        response = await client.post(CASHFREE, content=body, headers=_signed(provider, body))  # type: ignore[arg-type]
        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "unattributable"

    async def test_a_success_captures_the_checkout_and_stores_only_the_allow_list(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        body = _cashfree_body(order_id=out["charge_id"], org=org_id, charge_id=out["charge_id"])
        response = await client.post(
            CASHFREE,
            content=body,
            headers=_signed(provider, body, **{"x-idempotency-header": "cfh_1"}),
        )
        assert response.status_code == 200, response.text
        ack = response.json()
        assert ack["reconciled"] is True and ack["provider_event_id"] == "cfh_1"
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"]) == ("captured", "3001")
        assert "commerce.payment.captured" in await emitted(admin_session, org_id)

        (inbox,) = await inbox_rows(admin_session, org_id)
        stored = json.dumps(inbox["payload"])
        for pii in ("Asha Customer", "asha@example.com", "9876543210", "asha@okbank", "upi"):
            assert pii not in stored
        data = inbox["payload"]["data"]
        assert data["order"]["order_amount"] == "499.00"  # a string: a Decimal never reaches JSONB
        assert data["payment"]["payment_amount"] == "499.00"
        assert inbox["payload"]["_meta"] == {"webhook_version": "2025-01-01"}
        provider_rows = await rows(admin_session, "SELECT provider FROM payment.provider_events")
        assert provider_rows == [{"provider": "cashfree"}]

    async def test_a_redelivery_is_a_duplicate_under_either_header_spelling(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        body = _cashfree_body(order_id=out["charge_id"], org=org_id, charge_id=out["charge_id"])
        first = await client.post(
            CASHFREE,
            content=body,
            headers=_signed(provider, body, **{"x-idempotency-header": "cfh_2"}),
        )
        again = await client.post(
            CASHFREE,
            content=body,
            headers=_signed(provider, body, **{"x-idempotency-key": "cfh_2"}),
        )
        assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
        assert len(await inbox_rows(admin_session, org_id)) == 1
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_with_no_delivery_id_the_event_is_identified_by_what_it_says(
        self, client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        body = _cashfree_body(order_id=out["charge_id"], org=org_id, charge_id=out["charge_id"])
        one = await client.post(CASHFREE, content=body, headers=_signed(provider, body))
        two = await client.post(CASHFREE, content=body, headers=_signed(provider, body))
        assert one.json()["provider_event_id"].startswith("derived:")
        assert two.json()["provider_event_id"] == one.json()["provider_event_id"]
        assert two.json()["duplicate"] is True

    async def test_a_failed_or_dropped_attempt_changes_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        before = await emitted(admin_session, org_id)
        for n, (etype, status) in enumerate(
            [("PAYMENT_FAILED_WEBHOOK", "FAILED"), ("PAYMENT_USER_DROPPED_WEBHOOK", "USER_DROPPED")]
        ):
            body = _cashfree_body(
                etype=etype,
                status=status,
                order_id=out["charge_id"],
                org=org_id,
                charge_id=out["charge_id"],
                cf_payment_id=100 + n,
                event_time=f"2026-10-06T10:1{n}:00+05:30",
            )
            assert (
                await client.post(CASHFREE, content=body, headers=_signed(provider, body))
            ).status_code == 200
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"]) == ("created", None)
        assert await emitted(admin_session, org_id) == before

    async def test_a_surcharge_is_accepted_and_a_short_payment_is_a_flagged_conflict(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        short = _cashfree_body(
            order_id=out["charge_id"],
            org=org_id,
            charge_id=out["charge_id"],
            payment_amount="400.00",
            cf_payment_id=1,
            event_time="2026-10-06T10:00:00+05:30",
        )
        response = await client.post(CASHFREE, content=short, headers=_signed(provider, short))
        assert response.json()["ignored_reason"] == "amount_mismatch"
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

        surcharged = _cashfree_body(
            order_id=out["charge_id"],
            org=org_id,
            charge_id=out["charge_id"],
            payment_amount="509.00",
            cf_payment_id=2,
            event_time="2026-10-06T10:05:00+05:30",
        )
        response = await client.post(
            CASHFREE, content=surcharged, headers=_signed(provider, surcharged)
        )
        assert response.json()["reconciled"] is True
        assert (await _row(admin_session, out["charge_id"]))["state"] == "captured"

    async def test_an_event_about_another_order_is_a_flagged_conflict(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider, out = await _cashfree_checkout(client, org_id, monkeypatch)
        body = _cashfree_body(
            order_id="someone-elses-order", org=org_id, charge_id=out["charge_id"]
        )
        response = await client.post(CASHFREE, content=body, headers=_signed(provider, body))
        assert response.json()["ignored_reason"] == "order_id_conflict"
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"


class TestFailClosed:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings(), "cashfree_env", "sandbox")

    async def test_no_signature_a_wrong_one_or_no_timestamp_is_a_400_and_nothing_is_recorded(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        body = _cashfree_body(order_id="o", org=org_id, charge_id=str(uuid.uuid4()))
        good = _signed(provider, body)  # type: ignore[arg-type]
        attempts = [
            {},
            {"x-webhook-timestamp": TS},
            {"x-webhook-signature": good["x-webhook-signature"]},
            {**good, "x-webhook-signature": "AAAA" * 11},
            {**good, "x-webhook-timestamp": "1697191215001"},
        ]
        for headers in attempts:
            response = await client.post(CASHFREE, content=body, headers=headers)
            assert response.status_code == 400, headers
        # A non-ASCII signature (raw bytes on the wire): a 400, never a 500.
        raw = [(b"x-webhook-signature", "☃".encode()), (b"x-webhook-timestamp", TS.encode())]
        assert (await client.post(CASHFREE, content=body, headers=raw)).status_code == 400
        assert await inbox_rows(admin_session, org_id) == []

    async def test_a_razorpay_style_hex_signature_does_not_verify(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.cashfree_stub())
        body = _cashfree_body(order_id="o", org=org_id, charge_id=str(uuid.uuid4()))
        hexsig = "0" * 64
        response = await client.post(
            CASHFREE,
            content=body,
            headers={"x-webhook-signature": hexsig, "x-webhook-timestamp": TS},
        )
        assert response.status_code == 400

    async def test_an_unset_secret_verifies_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        unconfigured = CashfreeProvider(wire=CashfreeFakeWire(), secret_key=None, mode="stub")
        cs.install(unconfigured)
        body = _cashfree_body(order_id="o", org=org_id, charge_id=str(uuid.uuid4()))
        for sig in ("", "x", cs.cashfree_stub().sign(body, TS)):
            response = await client.post(
                CASHFREE,
                content=body,
                headers={"x-webhook-signature": sig, "x-webhook-timestamp": TS},
            )
            assert response.status_code == 400

    async def test_an_oversized_body_is_a_413_before_any_signature_work(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.cashfree_stub())
        big = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)
        # A bad signature would be a 400: a 413 proves the cap came first.
        for path in (CASHFREE, RAZORPAY):
            response = await client.post(path, content=big, headers={"x-webhook-signature": "bad"})
            assert response.status_code == 413, path
        edge = b"x" * MAX_WEBHOOK_BODY_BYTES
        assert (
            await client.post(CASHFREE, content=edge, headers={"x-webhook-signature": "bad"})
        ).status_code == 400

    @pytest.mark.parametrize("path", [RAZORPAY, CASHFREE, "/api/v1/webhooks/razorpay/collections"])
    async def test_a_chunked_body_with_no_length_is_refused_without_being_read_whole(
        self, client: AsyncClient, org_id: uuid.UUID, path: str
    ) -> None:
        """A sender with no `Content-Length` (chunked) cannot make the service buffer its body: the
        stream is counted as it arrives and refused at the first byte over the cap."""
        cs.install(cs.cashfree_stub())
        chunk = b"x" * (64 * 1024)
        total = 4 * (MAX_WEBHOOK_BODY_BYTES // len(chunk))
        pulled = 0

        async def stream() -> Any:
            nonlocal pulled
            for _ in range(total):
                pulled += 1
                yield chunk

        url = f"{path}?account={uuid.uuid4()}" if path.endswith("collections") else path
        response = await client.post(url, content=stream(), headers={"x-webhook-signature": "x"})

        assert response.status_code == 413
        assert pulled < total, "the whole body was read before it was refused"
