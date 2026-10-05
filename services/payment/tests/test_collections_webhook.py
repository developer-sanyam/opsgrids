"""Opening a checkout, and the merchant webhook that is the only thing (besides a server-to-server
status check) that may ever mark a collection paid.

The properties this proves: a verified capture moves the collection to `captured` and stages
`payment.collections.payment.captured` **exactly once** — never `commerce.payment.*`, which would
reach subscription dunning; a redelivery is a no-op; a signature made with any secret but the
merchant's own is refused and recorded nowhere; a failed payment stays unpaid; out-of-order and
late events never undo a capture; the inbox never keeps the tenant's customer's personal data; and
one tenant's webhook can never move another tenant's collection.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.razorpay import RazorpayProvider
from tests.collections_support import (
    MERCHANT_KEY_SECRET,
    MERCHANT_WEBHOOK_SECRET,
    MerchantProviders,
    as_principal,
    checkout,
    connect,
    deliver,
    internal_base,
    operations_token,
    payment_webhook,
    principal_for,
)
from tests.helpers import audit_rows, emitted, inbox_rows, razorpay_webhook_body, rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

CAPTURED = "payment.collections.payment.captured"
FAILED = "payment.collections.payment.failed"


async def _collection(admin: AsyncSession, charge_id: str) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, payee, provider_payment_id, provider_order_id, failure_reason, "
        "reference_type, reference_id, amount_minor, currency FROM payment.charges WHERE id = :id",
        id=uuid.UUID(charge_id),
    )
    return found[0]


async def _history(admin: AsyncSession, charge_id: str) -> list[tuple[Any, ...]]:
    found = await rows(
        admin,
        "SELECT from_state, to_state, source FROM payment.status_history "
        "WHERE entity_id = :id ORDER BY at, created_at",
        id=uuid.UUID(charge_id),
    )
    return [(r["from_state"], r["to_state"], r["source"]) for r in found]


async def _open(
    client: AsyncClient, org_id: uuid.UUID, *, amount_minor: int = 50_000
) -> tuple[str, dict[str, Any], uuid.UUID]:
    account = await connect(client, org_id)
    order_id = uuid.uuid4()
    response = await checkout(client, org_id, order_id, amount_minor=amount_minor)
    assert response.status_code == 200, response.text
    return account["id"], response.json(), order_id


class TestCheckout:
    async def test_a_checkout_hands_the_browser_the_public_key_and_order_and_nothing_secret(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        session_out = opened["checkout"]
        assert session_out["key_id"] == "rzp_test_Merchant0001"
        assert session_out["provider_order_id"].startswith("order_")
        assert session_out["amount_minor"] == 50_000 and session_out["currency"] == "INR"
        assert session_out["mode"] == "test"
        assert session_out["openable"] is False  # the stub wire: nothing real to pay
        collection = opened["collection"]
        assert collection["state"] == "created"
        assert collection["reference_id"] == str(order_id)
        text = json.dumps(opened)
        assert MERCHANT_KEY_SECRET not in text and MERCHANT_WEBHOOK_SECRET not in text
        # The sealed key really was opened and handed to the provider call, and only there.
        assert merchant_providers.key_secrets_seen == [MERCHANT_KEY_SECRET]

        stored = await _collection(admin_session, collection["id"])
        assert stored["payee"] == "merchant" and stored["state"] == "created"
        assert await _history(admin_session, collection["id"]) == [(None, "created", "api")]
        assert await emitted(admin_session, org_id) == []

    async def test_collecting_again_reuses_the_one_open_checkout(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, first, order_id = await _open(client, org_id)
        second = (await checkout(client, org_id, order_id)).json()
        assert second["collection"]["id"] == first["collection"]["id"]
        assert second["checkout"]["provider_order_id"] == first["checkout"]["provider_order_id"]
        different = await checkout(client, org_id, order_id, amount_minor=60_000)
        assert different.status_code == 409

    async def test_nothing_can_be_collected_without_a_connected_account(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        response = await checkout(client, org_id, uuid.uuid4())
        assert response.status_code == 409 and "not set up" in response.text

    @pytest.mark.parametrize("caller", ["billing", "subscription", "finance"])
    async def test_only_operations_may_open_a_checkout(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        caller: str,
    ) -> None:
        await connect(client, org_id)
        assert (await checkout(client, org_id, uuid.uuid4(), caller=caller)).status_code == 403

    async def test_no_service_token_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"{internal_base(org_id, uuid.uuid4())}/checkout",
            json={"amount_minor": 1, "currency": "INR"},
        )
        assert response.status_code == 401

    async def test_another_organizations_collections_are_invisible(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        seen_by_b = await client.get(internal_base(org_b, order_id), headers=operations_token())
        assert seen_by_b.status_code == 200 and seen_by_b.json()["collections"] == []
        sync_by_b = await client.post(
            f"{internal_base(org_b, order_id)}/charges/{opened['collection']['id']}/sync",
            headers=operations_token(),
        )
        assert sync_by_b.status_code == 404


class TestVerifiedCapture:
    async def test_a_verified_capture_marks_it_paid_exactly_once(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        order = opened["checkout"]["provider_order_id"]
        body = payment_webhook(event="payment.captured", payment_id="pay_A", order_id=order)

        first = await deliver(client, account_id, body, event_id="evt_cap_1")
        assert first.status_code == 200, first.text
        assert first.json()["reconciled"] is True and first.json()["duplicate"] is False

        again = await deliver(client, account_id, body, event_id="evt_cap_1")
        assert again.status_code == 200 and again.json()["duplicate"] is True

        # The same outcome under a fresh delivery id (Razorpay retries can) is still one capture.
        other_id = await deliver(client, account_id, body, event_id="evt_cap_2")
        assert other_id.json()["reconciled"] is False

        # `order.paid` for the same payment, out of order, changes nothing either.
        paid = payment_webhook(event="order.paid", payment_id="pay_A", order_id=order)
        assert (await deliver(client, account_id, paid, event_id="evt_paid")).json()[
            "reconciled"
        ] is False

        stored = await _collection(admin_session, opened["collection"]["id"])
        assert stored["state"] == "captured" and stored["provider_payment_id"] == "pay_A"
        assert await emitted(admin_session, org_id) == [CAPTURED]
        assert await _history(admin_session, opened["collection"]["id"]) == [
            (None, "created", "api"),
            ("created", "captured", "webhook"),
        ]
        inbox = await inbox_rows(admin_session, org_id)
        assert len(inbox) == 3  # evt_cap_1, evt_cap_2, evt_paid — the redelivery added nothing

    async def test_the_inbox_never_keeps_the_customers_personal_data(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_A",
            order_id=opened["checkout"]["provider_order_id"],
        )
        await deliver(client, account_id, body, event_id="evt_pii")
        (row,) = await inbox_rows(admin_session, org_id)
        stored = json.dumps(row["payload"])
        for leaked in ("customer@example.com", "+919876543210", "okbank", "1111", "A Customer"):
            assert leaked not in stored
        payee = await rows(
            admin_session,
            "SELECT payee FROM payment.provider_events WHERE organization_id = :o",
            o=org_id,
        )
        assert payee == [{"payee": "merchant"}]

    async def test_a_capture_emits_nothing_the_subscription_loop_listens_to(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        order = opened["checkout"]["provider_order_id"]
        await deliver(
            client,
            account_id,
            payment_webhook(
                event="payment.failed",
                payment_id="pay_F",
                order_id=order,
                error_code="BAD_REQUEST_ERROR",
            ),
        )
        await deliver(
            client,
            account_id,
            payment_webhook(event="payment.captured", payment_id="pay_G", order_id=order),
        )
        events = await emitted(admin_session, org_id)
        assert events == [FAILED, CAPTURED]
        assert not any(e.startswith("commerce.payment.") for e in events)


class TestFailures:
    async def test_a_failed_payment_leaves_it_unpaid_and_a_retry_can_still_pay(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        order = opened["checkout"]["provider_order_id"]
        failed = payment_webhook(
            event="payment.failed",
            payment_id="pay_1",
            order_id=order,
            error_code="BAD_REQUEST_ERROR",
        )
        assert (await deliver(client, account_id, failed)).json()["reconciled"] is True
        stored = await _collection(admin_session, opened["collection"]["id"])
        assert stored["state"] == "failed"
        assert stored["failure_reason"] == "Payment was declined by the bank"

        retried = payment_webhook(
            event="payment.captured", payment_id="pay_2", order_id=order, created_at=1_700_000_900
        )
        await deliver(client, account_id, retried)
        # The first attempt's failure, redelivered late under a new id, never undoes the capture.
        late = payment_webhook(
            event="payment.failed", payment_id="pay_1", order_id=order, created_at=1_700_000_950
        )
        assert (await deliver(client, account_id, late)).json()["reconciled"] is False
        stored = await _collection(admin_session, opened["collection"]["id"])
        assert stored["state"] == "captured" and stored["provider_payment_id"] == "pay_2"

    async def test_a_payment_for_another_amount_is_a_recorded_conflict(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_1",
            order_id=opened["checkout"]["provider_order_id"],
            amount_minor=1,
        )
        ack = (await deliver(client, account_id, body)).json()
        assert ack["ignored_reason"] == "amount_mismatch"
        assert (await _collection(admin_session, opened["collection"]["id"]))["state"] == "created"
        (row,) = await inbox_rows(admin_session, org_id)
        assert row["disposition"] == "conflict"
        assert await emitted(admin_session, org_id) == []


class TestSignature:
    async def test_a_body_signed_with_anything_but_the_merchants_secret_is_refused(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
    ) -> None:
        account_id, opened, _ = await _open(client, org_id)
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_1",
            order_id=opened["checkout"]["provider_order_id"],
        )
        # The platform's own webhook secret is not this merchant's.
        assert (await deliver(client, account_id, body, signer=provider)).status_code == 400
        assert (await deliver(client, account_id, body, signature="0" * 64)).status_code == 400
        tampered = body.replace(b'"amount": 50000', b'"amount": 50001')
        assert (
            await deliver(
                client,
                account_id,
                tampered,
                signature=RazorpayProvider(
                    key_id=None, key_secret=None, webhook_secret=MERCHANT_WEBHOOK_SECRET
                ).sign(body),
            )
        ).status_code == 400
        assert await inbox_rows(admin_session, org_id) == []
        assert (await _collection(admin_session, opened["collection"]["id"]))["state"] == "created"

    @pytest.mark.parametrize("account", ["not-a-uuid", str(uuid.uuid4())])
    async def test_an_unknown_account_is_the_same_400(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        account: str,
    ) -> None:
        await connect(client, org_id)
        body = payment_webhook(event="payment.captured", payment_id="pay_1", order_id="order_x")
        assert (await deliver(client, account, body)).status_code == 400

    async def test_a_removed_account_no_longer_verifies_anything(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, order_id = await _open(client, org_id)
        # An account with an open checkout cannot be disconnected (ADR-0030): cancel it first.
        cancelled = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/cancel",
            headers=operations_token(),
            json={},
        )
        assert cancelled.status_code == 200, cancelled.text
        with as_principal(
            principal_for(org_id, ["operations.payments.merchant_account.write:ORG"])
        ):
            assert (
                await client.delete("/api/v1/payment/collections/merchant-account")
            ).status_code == 204
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_1",
            order_id=opened["checkout"]["provider_order_id"],
        )
        assert (await deliver(client, account_id, body)).status_code == 400


class TestIsolation:
    async def test_one_tenants_webhook_can_never_move_anothers_collection(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        _, opened_a, _ = await _open(client, org_id)
        # Org B connects the *same* Razorpay test keys (nothing stops two tenants typing the same
        # thing) and delivers a body naming A's Order to B's own URL, signed correctly for B.
        account_b = await connect(client, org_b)
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_evil",
            order_id=opened_a["checkout"]["provider_order_id"],
            notes={"organization_id": str(org_id), "charge_id": opened_a["collection"]["id"]},
        )
        ack = (await deliver(client, account_b["id"], body)).json()
        assert ack["ignored_reason"] == "unknown_charge"
        assert (await _collection(admin_session, opened_a["collection"]["id"]))[
            "state"
        ] == "created"
        assert await emitted(admin_session, org_id) == []

    async def test_the_platform_webhook_can_never_move_a_merchant_collection(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
    ) -> None:
        _, opened, _ = await _open(client, org_id)
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_platform",
            organization_id=org_id,
            charge_id=uuid.UUID(opened["collection"]["id"]),
            amount_minor=50_000,
            currency="INR",
        )
        response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": provider.sign(body)},
        )
        assert response.status_code == 200 and response.json()["ignored_reason"] == "unknown_charge"
        assert (await _collection(admin_session, opened["collection"]["id"]))["state"] == "created"
        events = await emitted(admin_session, org_id)
        assert not any(e.startswith("commerce.payment.") for e in events)


class TestLateAndLocalDecisions:
    async def test_a_capture_after_an_operator_cancelled_is_still_recorded_and_flagged(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, order_id = await _open(client, org_id)
        charge_id = opened["collection"]["id"]
        cancelled = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{charge_id}/cancel",
            headers=operations_token(),
            json={},
        )
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        body = payment_webhook(
            event="payment.captured",
            payment_id="pay_late",
            order_id=opened["checkout"]["provider_order_id"],
        )
        assert (await deliver(client, account_id, body)).json()["reconciled"] is True
        assert (await _collection(admin_session, charge_id))["state"] == "captured"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.collection.captured_after_cancelled" in actions
        assert await emitted(admin_session, org_id) == [CAPTURED]
