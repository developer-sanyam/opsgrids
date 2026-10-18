"""Merchant connect with Paytm, through the real routes: connecting (the merchant key must be an AES
key), the form checkout, the neutral webhook path with Paytm's `CHECKSUMHASH` failing closed,
status checks and refunds — with negative-authorization and cross-tenant cases.

The merchant's Paytm is the real `PaytmCollectionsProvider` over the stateful `PaytmFakeWire`
(`MerchantProviders.paytm_wires`). No request leaves the machine.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.paytm_collections import generate_signature, params_string
from app.providers.paytm_fake import PaytmFakeWire
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
from tests.helpers import emitted, inbox_rows, rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

MID = "TESTMID0001"
KEY = "dev-only-key-016"  # pragma: allowlist secret
NEUTRAL = "/api/v1/webhooks/collections"
CAPTURED = "payment.collections.payment.captured"


def paytm_body(**over: Any) -> dict[str, Any]:
    return {"provider": "paytm", "key_id": MID, "key_secret": KEY, "mode": "sandbox", **over}


async def connect_paytm(client: AsyncClient, org: uuid.UUID, **over: Any) -> dict[str, Any]:
    with as_principal(principal_for(org, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=paytm_body(**over))
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def wire(providers: MerchantProviders) -> PaytmFakeWire:
    return providers.paytm_wires[MID]


async def opened(
    client: AsyncClient, providers: MerchantProviders, org: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
    account = await connect_paytm(client, org)
    order = uuid.uuid4()
    response = await client.post(
        f"{internal_base(org, order)}/checkout",
        headers=operations_token(),
        json={"amount_minor": 49_999, "currency": "INR"},
    )
    assert response.status_code == 200, response.text
    return account, response.json(), order


def status_webhook(order_id: str, txn_id: str, *, amount: str = "499.99", key: str = KEY) -> bytes:
    fields = {
        "ORDERID": order_id,
        "MID": MID,
        "TXNID": txn_id,
        "TXNAMOUNT": amount,
        "CURRENCY": "INR",
        "TXNDATE": "2026-10-16 10:15:02.0",
        "STATUS": "TXN_SUCCESS",
        "RESPCODE": "01",
        "RESPMSG": "Txn Success",
        "BANKNAME": "buyer@paytm",
    }
    fields["CHECKSUMHASH"] = generate_signature(params_string(fields), key)
    return urllib.parse.urlencode(fields).encode()


async def deliver(client: AsyncClient, account_id: str, body: bytes) -> Any:
    return await client.post(
        f"{NEUTRAL}?account={account_id}",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


class TestConnect:
    async def test_paytm_holds_one_sealed_secret_and_no_webhook_secret(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        account = await connect_paytm(client, org_id)
        assert (account["provider"], account["mode"], account["webhook_secret_masked"]) == (
            "paytm",
            "test",
            None,
        )
        assert KEY not in json.dumps(account)
        (row,) = await rows(
            admin_session,
            "SELECT webhook_secret_enc FROM payment.merchant_accounts WHERE organization_id = :o",
            o=org_id,
        )
        assert row["webhook_secret_enc"] is None

    @pytest.mark.parametrize(
        "over",
        [
            {"key_secret": "dev-only-key-0017"},
            {"webhook_secret": "dev-only-hook-secret"},
            {"mode": None},
            {"mode": "production"},
        ],
    )
    async def test_a_malformed_paytm_account_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID, over: dict[str, Any]
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.put(ACCOUNT, json=paytm_body(**over))).status_code == 422

    @pytest.mark.parametrize(("perms", "amr"), [([READ], None), ([READ, WRITE], ["pwd"])])
    async def test_connecting_needs_the_write_permission_and_a_one_time_code(
        self, client: AsyncClient, org_id: uuid.UUID, perms: list[str], amr: list[str] | None
    ) -> None:
        with as_principal(principal_for(org_id, perms, amr=amr)):
            assert (await client.put(ACCOUNT, json=paytm_body())).status_code == 403


class TestCheckoutWebhooksAndRefunds:
    async def test_the_browser_gets_a_form_to_paytms_page_and_nothing_is_stored(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, response, _ = await opened(client, merchant_providers, org_id)
        handle = response["checkout"]
        assert (handle["provider"], handle["method"]) == ("paytm", "POST")
        assert handle["url"].startswith(
            "https://securestage.paytmpayments.com/theia/api/v1/showPaymentPage?"
        )
        assert handle["fields"]["orderId"] == handle["provider_order_id"]
        assert wire(merchant_providers).orders[handle["provider_order_id"]]["amount"] == "499.99"
        token = handle["fields"]["txnToken"]
        written = []
        for table in ("charges", "audit_logs", "outbox", "status_history"):
            written += await rows(
                admin_session,
                f"SELECT row_to_json(t)::text AS t FROM payment.{table} t "  # noqa: S608
                "WHERE organization_id = :o",
                o=org_id,
            )
        assert token not in json.dumps(written, default=str)

    async def test_a_genuine_status_webhook_captures_once_and_keeps_no_bank_field(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        body = status_webhook(response["checkout"]["provider_order_id"], "20201016001")
        first = await deliver(client, account["id"], body)
        assert first.status_code == 200 and first.json()["reconciled"] is True
        assert (await deliver(client, account["id"], body)).json()["duplicate"] is True
        assert await emitted(admin_session, org_id) == [CAPTURED]
        assert "buyer@paytm" not in json.dumps(await inbox_rows(admin_session, org_id), default=str)

    @pytest.mark.parametrize("variant", ["wrong_key", "tampered_amount", "no_checksum"])
    async def test_anything_paytm_did_not_sign_is_400_and_records_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        variant: str,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        order_id = response["checkout"]["provider_order_id"]
        body = status_webhook(
            order_id, "1", key="dev-only-wrong16" if variant == "wrong_key" else KEY
        )
        if variant == "tampered_amount":
            body = body.replace(b"TXNAMOUNT=499.99", b"TXNAMOUNT=1.00")
        elif variant == "no_checksum":
            body = body.split(b"&CHECKSUMHASH=")[0]
        assert (await deliver(client, account["id"], body)).status_code == 400
        assert await inbox_rows(admin_session, org_id) == []

    async def test_a_status_check_then_a_refund(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, response, order = await opened(client, merchant_providers, org_id)
        charge_id = response["collection"]["id"]
        base = f"{internal_base(org_id, order)}/charges/{charge_id}"
        assert (await client.post(f"{base}/sync", headers=operations_token())).json()[
            "state"
        ] == "created"
        txn = wire(merchant_providers).pay(response["checkout"]["provider_order_id"])
        synced = (await client.post(f"{base}/sync", headers=operations_token())).json()
        assert synced["state"] == "captured" and synced["provider_payment_id"] == txn
        refund = await client.post(
            f"{base}/refunds",
            headers=operations_token(),
            json={"amount_minor": 10_001, "reason": "damaged", "idempotency_key": "op-key-1"},
        )
        assert refund.status_code == 200, refund.text
        (made,) = refund.json()["refunds"]
        assert made["state"] == "created"
        assert (
            wire(merchant_providers).refunds[made["provider_refund_id"]]["refundAmount"] == "100.01"
        )

    async def test_another_organizations_key_never_opens_this_account(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        await connect_paytm(client, org_b, key_secret="dev-only-orgb-16")
        body = status_webhook(
            response["checkout"]["provider_order_id"], "9", key="dev-only-orgb-16"
        )
        assert (await deliver(client, account["id"], body)).status_code == 400
        with as_principal(principal_for(org_b, [READ])):
            assert (await client.get(ACCOUNT)).json()["id"] != account["id"]
