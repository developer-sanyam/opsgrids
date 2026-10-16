"""Merchant connect with PhonePe (Standard Checkout V2), through the real routes: connecting with
its config, the redirect checkout, the neutral webhook path and its header credential failing
closed, status checks, refunds, the return page — with negative-authorization and cross-tenant
cases.

The merchant's PhonePe is the real `PhonePeCollectionsProvider` over the stateful
`PhonePeFakeWire` (`MerchantProviders.phonepe_wires`). No request leaves the machine.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.phonepe_collections import webhook_authorization
from app.providers.phonepe_fake import PhonePeFakeWire
from tests.collections_support import (
    ACCOUNT,
    READ,
    WRITE,
    MerchantProviders,
    as_principal,
    internal_base,
    operations_token,
    principal_for,
)
from tests.helpers import audit_rows, emitted, inbox_rows, rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

CLIENT_ID = "TESTDEVONLYCLIENT01"  # pragma: allowlist secret
CLIENT_SECRET = "dev-only-phonepe-client-secret-01"  # pragma: allowlist secret
HOOK_USER = "opsgrids-hook"
HOOK_PASSWORD = "dev-only-hook-password-01"  # pragma: allowlist secret
CONFIG = {"client_version": 1, "webhook_username": HOOK_USER}
NEUTRAL = "/api/v1/webhooks/collections"
RETURN = "/api/v1/pay/return"
CAPTURED = "payment.collections.payment.captured"


def phonepe_body(**over: Any) -> dict[str, Any]:
    return {
        "provider": "phonepe",
        "key_id": CLIENT_ID,
        "key_secret": CLIENT_SECRET,
        "webhook_secret": HOOK_PASSWORD,
        "mode": "sandbox",
        "config": CONFIG,
        **over,
    }


async def connect_phonepe(client: AsyncClient, org: uuid.UUID, **over: Any) -> dict[str, Any]:
    with as_principal(principal_for(org, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=phonepe_body(**over))
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def wire(providers: MerchantProviders) -> PhonePeFakeWire:
    return providers.phonepe_wires[CLIENT_ID]


async def opened(
    client: AsyncClient, providers: MerchantProviders, org: uuid.UUID, *, amount_minor: int = 49_999
) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
    account = await connect_phonepe(client, org)
    order = uuid.uuid4()
    response = await client.post(
        f"{internal_base(org, order)}/checkout",
        headers=operations_token(),
        json={"amount_minor": amount_minor, "currency": "INR"},
    )
    assert response.status_code == 200, response.text
    return account, response.json(), order


def completed(order_id: str, *, amount: int = 49_999, txn: str = "OM100") -> bytes:
    return json.dumps(
        {
            "event": "checkout.order.completed",
            "payload": {
                "orderId": "OMO1",
                "merchantOrderId": order_id,
                "state": "COMPLETED",
                "amount": amount,
                "expireAt": 1_760_000_900_000,
                "paymentDetails": [
                    {
                        "transactionId": txn,
                        "paymentMode": "UPI_QR",
                        "timestamp": 1_760_000_100_000,
                        "amount": amount,
                        "state": "COMPLETED",
                        "rail": {"type": "UPI", "vpa": "buyer@ybl", "utr": "UTR99"},
                    }
                ],
            },
        }
    ).encode()


async def deliver(
    client: AsyncClient, account_id: str, body: bytes, headers: dict[str, str]
) -> Any:
    return await client.post(f"{NEUTRAL}?account={account_id}", content=body, headers=headers)


def signed(password: str = HOOK_PASSWORD, user: str = HOOK_USER) -> dict[str, str]:
    return {"authorization": webhook_authorization(user, password)}


class TestConnect:
    async def test_the_config_is_stored_the_password_sealed_and_neither_secret_echoed(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        account = await connect_phonepe(client, org_id)
        assert account["provider"] == "phonepe" and account["mode"] == "test"
        assert account["config"] == CONFIG
        assert account["webhook_secret_masked"].endswith(HOOK_PASSWORD[-4:])
        assert account["webhook_url"].endswith(f"/webhooks/collections?account={account['id']}")
        assert "checkout.order.completed" in account["webhook_events"]
        assert CLIENT_SECRET not in json.dumps(account) and HOOK_PASSWORD not in json.dumps(account)
        (row,) = await rows(
            admin_session,
            "SELECT key_secret_enc, webhook_secret_enc FROM payment.merchant_accounts "
            "WHERE organization_id = :o",
            o=org_id,
        )
        assert row["key_secret_enc"] and row["webhook_secret_enc"]
        assert CLIENT_SECRET.encode() not in bytes(row["key_secret_enc"])
        audit = json.dumps(await audit_rows(admin_session, org_id), default=str)
        assert CLIENT_SECRET not in audit and HOOK_PASSWORD not in audit

    @pytest.mark.parametrize(
        "over",
        [
            {"config": None},
            {"config": {"client_version": 1}},
            {"config": {"webhook_username": HOOK_USER}},
            {"webhook_secret": None},
            {"mode": None},
            {"mode": "production"},
        ],
    )
    async def test_an_incomplete_phonepe_account_is_refused(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, over: Any
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE])):
            response = await client.put(ACCOUNT, json=phonepe_body(**over))
        assert response.status_code == 422, response.text
        assert (
            await rows(
                admin_session,
                "SELECT id FROM payment.merchant_accounts WHERE organization_id = :o",
                o=org_id,
            )
            == []
        )

    async def test_a_rotation_without_config_keeps_and_revalidates_the_stored_one(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        first = await connect_phonepe(client, org_id)
        body = phonepe_body(key_secret="dev-only-rotated-secret-02")
        del body["config"]
        with as_principal(principal_for(org_id, [READ, WRITE])):
            rotated = await client.put(ACCOUNT, json=body)
        assert rotated.status_code == 200 and rotated.json()["id"] == first["id"]
        assert rotated.json()["config"] == CONFIG


class TestAuthorization:
    @pytest.mark.parametrize(
        ("perms", "amr"),
        [([], None), ([READ], None), ([READ, WRITE], ["pwd"])],
    )
    async def test_connecting_needs_the_write_permission_and_a_one_time_code(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        perms: list[str],
        amr: list[str] | None,
    ) -> None:
        with as_principal(principal_for(org_id, perms, amr=amr)):
            response = await client.put(ACCOUNT, json=phonepe_body())
        assert response.status_code == 403
        assert (
            await rows(
                admin_session,
                "SELECT id FROM payment.merchant_accounts WHERE organization_id = :o",
                o=org_id,
            )
            == []
        )

    async def test_another_organization_sees_nothing_and_its_webhook_cannot_be_borrowed(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        account, checkout, _ = await opened(client, merchant_providers, org_id)
        with as_principal(principal_for(org_b, [READ, WRITE])):
            seen = (await client.get(ACCOUNT)).json()
        assert seen["configured"] is False and seen["config"] == {}
        # Org B connects its own PhonePe with another webhook password: B's credential never
        # opens A's account, whatever the body says.
        other = await connect_phonepe(
            client, org_b, webhook_secret="dev-only-org-b-password", config=CONFIG
        )
        body = completed(checkout["collection"]["id"])
        refused = await client.post(
            f"{NEUTRAL}?account={account['id']}",
            content=body,
            headers=signed("dev-only-org-b-password"),
        )
        assert refused.status_code == 400
        # And A's order delivered to B's account finds no collection of B's.
        foreign = await client.post(
            f"{NEUTRAL}?account={other['id']}",
            content=body,
            headers=signed("dev-only-org-b-password"),
        )
        assert foreign.status_code == 200 and foreign.json()["ignored_reason"] == "unknown_charge"
        assert await emitted(admin_session, org_id) == []


class TestCheckout:
    async def test_the_browser_gets_a_redirect_in_paise_and_nothing_is_stored(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            _, response, _ = await opened(client, merchant_providers, org_id, amount_minor=49_999)
        charge_id = response["collection"]["id"]
        handle = response["checkout"]
        assert handle["provider"] == "phonepe" and handle["method"] == "GET"
        assert handle["url"].startswith("https://") and handle["fields"] == {}
        assert handle["provider_order_id"] == charge_id
        assert (handle["amount_minor"], handle["currency"]) == (49_999, "INR")
        assert handle["openable"] is False  # the stub cannot be paid for real
        order = wire(merchant_providers).orders[charge_id]
        assert order["amount"] == 49_999  # paise, exactly, on the wire
        assert merchant_providers.configs[-1] == CONFIG
        written = []
        for table in ("charges", "audit_logs", "outbox", "status_history"):
            written += await rows(
                admin_session,
                f"SELECT row_to_json(t)::text AS t FROM payment.{table} t "  # noqa: S608
                "WHERE organization_id = :o",
                o=org_id,
            )
        dumped = json.dumps({"logs": logs, "rows": written}, default=str)
        assert "stub.phonepe.invalid" not in dumped  # the token-bearing URL is never kept

    async def test_reopening_asks_phonepe_for_a_fresh_url_and_creates_no_second_order(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, first, order = await opened(client, merchant_providers, org_id)
        again = await client.post(
            f"{internal_base(org_id, order)}/checkout",
            headers=operations_token(),
            json={"amount_minor": 49_999, "currency": "INR"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["collection"]["id"] == first["collection"]["id"]
        assert again.json()["checkout"]["url"] != first["checkout"]["url"]
        assert wire(merchant_providers).created == 1

    async def test_an_order_with_an_attempt_cannot_be_reopened_and_says_how_to_go_on(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, first, order = await opened(client, merchant_providers, org_id)
        wire(merchant_providers).pay(first["collection"]["id"], state="FAILED")
        again = await client.post(
            f"{internal_base(org_id, order)}/checkout",
            headers=operations_token(),
            json={"amount_minor": 49_999, "currency": "INR"},
        )
        assert again.status_code == 409 and "cancel it" in again.json()["detail"]


class TestWebhooksAndStatus:
    async def test_a_genuine_completed_webhook_captures_once_and_keeps_no_vpa(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        body = completed(response["collection"]["id"])
        first = await client.post(
            f"{NEUTRAL}?account={account['id']}", content=body, headers=signed()
        )
        assert first.status_code == 200 and first.json()["reconciled"] is True
        again = await client.post(
            f"{NEUTRAL}?account={account['id']}", content=body, headers=signed()
        )
        assert again.json()["duplicate"] is True
        assert await emitted(admin_session, org_id) == [CAPTURED]
        (inbox,) = await inbox_rows(admin_session, org_id)
        stored = json.dumps(inbox, default=str)
        for kept_out in ("buyer@ybl", "UTR99", webhook_authorization(HOOK_USER, HOOK_PASSWORD)):
            assert kept_out not in stored

    async def test_a_captured_amount_that_is_not_the_collections_is_not_applied(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        body = completed(response["collection"]["id"], amount=100)
        answer = await client.post(
            f"{NEUTRAL}?account={account['id']}", content=body, headers=signed()
        )
        assert answer.status_code == 200 and answer.json()["reconciled"] is False
        assert await emitted(admin_session, org_id) == []

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"authorization": ""},
            {"authorization": "0" * 64},
            {"authorization": webhook_authorization(HOOK_USER, "dev-only-wrong-password")},
            {"authorization": webhook_authorization("another-user", HOOK_PASSWORD)},
        ],
    )
    async def test_anything_but_the_exact_credential_is_400_and_records_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        headers: dict[str, str],
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        body = completed(response["collection"]["id"])
        answer = await client.post(
            f"{NEUTRAL}?account={account['id']}", content=body, headers=headers
        )
        assert answer.status_code == 400
        assert await inbox_rows(admin_session, org_id) == []
        assert await emitted(admin_session, org_id) == []

    async def test_a_status_check_and_a_refund_with_our_refund_id(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, response, order = await opened(client, merchant_providers, org_id)
        charge_id = response["collection"]["id"]
        base = f"{internal_base(org_id, order)}/charges/{charge_id}"
        wire(merchant_providers).pay(charge_id, state="PENDING")
        assert (await client.post(f"{base}/sync", headers=operations_token())).json()[
            "state"
        ] == "created"
        payment = wire(merchant_providers).pay(charge_id)
        synced = (await client.post(f"{base}/sync", headers=operations_token())).json()
        assert synced["state"] == "captured" and synced["provider_payment_id"] == payment
        refund = await client.post(
            f"{base}/refunds",
            headers=operations_token(),
            json={"amount_minor": 10_001, "reason": "damaged", "idempotency_key": "op-key-1"},
        )
        assert refund.status_code == 200, refund.text
        (made,) = refund.json()["refunds"]
        assert made["provider_refund_id"].startswith("rfd") and made["amount_minor"] == 10_001
        assert wire(merchant_providers).refunds[made["provider_refund_id"]]["amount"] == 10_001


class TestReturnPage:
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_it_is_static_reads_nothing_and_is_never_cached(
        self, client: AsyncClient, method: str
    ) -> None:
        response = await client.request(
            method,
            f"{RETURN}?next=https://evil.example",
            content=b"status=success&hash=abc&email=buyer@example.com",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "evil.example" not in response.text and "buyer@example.com" not in response.text
        assert "location" not in response.headers


def test_the_return_url_is_built_from_the_public_base() -> None:
    from app.collections import domain

    assert domain.return_url(settings().collections_public_api_base).endswith(RETURN)
