"""Merchant connect with PayU (Hosted Checkout), through the real routes: connecting, the payer a
PayU payment needs, the form checkout, the neutral webhook path with PayU's reverse hash failing
closed, status checks, refunds, the shared return page taking PayU's form POST — with
negative-authorization and cross-tenant cases.

The merchant's PayU is the real `PayUCollectionsProvider` over the stateful `PayUFakeWire`
(`MerchantProviders.payu_wires`). No request leaves the machine.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.payu_collections import response_hash
from app.providers.payu_fake import PayUFakeWire
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

KEY = "TESTdevKey01"  # pragma: allowlist secret
SALT = "dev-only-payu-salt-0001"  # pragma: allowlist secret
NEUTRAL = "/api/v1/webhooks/collections"
RETURN = "/api/v1/pay/return"
CAPTURED = "payment.collections.payment.captured"
PHONE = "9876501234"


@pytest.fixture(autouse=True)
def short_public_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """PayU caps `surl` at 50 characters; production's `https://app.opsgrids.com` fits."""
    monkeypatch.setattr(settings(), "app_base_url", "https://app.opsgrids.com")


def payu_body(**over: Any) -> dict[str, Any]:
    return {"provider": "payu", "key_id": KEY, "key_secret": SALT, "mode": "sandbox", **over}


async def connect_payu(client: AsyncClient, org: uuid.UUID, **over: Any) -> dict[str, Any]:
    with as_principal(principal_for(org, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=payu_body(**over))
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def wire(providers: MerchantProviders) -> PayUFakeWire:
    return providers.payu_wires[KEY]


async def checkout(client: AsyncClient, org: uuid.UUID, order: uuid.UUID, **body: Any) -> Any:
    return await client.post(
        f"{internal_base(org, order)}/checkout",
        headers=operations_token(),
        json={"amount_minor": 49_999, "currency": "INR", **body},
    )


async def opened(
    client: AsyncClient, providers: MerchantProviders, org: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
    account = await connect_payu(client, org)
    order = uuid.uuid4()
    response = await checkout(client, org, order, payer={"name": "Asha Verma", "phone": PHONE})
    assert response.status_code == 200, response.text
    return account, response.json(), order


def payu_webhook(txnid: str, mihpayid: str, *, amount: str = "499.99", salt: str = SALT) -> bytes:
    fields = {
        "mihpayid": mihpayid,
        "key": KEY,
        "txnid": txnid,
        "amount": amount,
        "productinfo": "Order payment",
        "firstname": "Asha Verma",
        "email": "",
        "phone": PHONE,
        "status": "success",
        "unmappedstatus": "captured",
        "addedon": "2026-10-16 10:15:00",
        "field3": "buyer@okaxis",
        "udf1": "",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }
    fields["hash"] = response_hash(fields, salt)
    return urllib.parse.urlencode(fields).encode()


async def deliver(client: AsyncClient, account_id: str, body: bytes) -> Any:
    return await client.post(
        f"{NEUTRAL}?account={account_id}",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


class TestConnect:
    async def test_payu_holds_one_sealed_secret_and_no_webhook_secret(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        account = await connect_payu(client, org_id)
        assert (account["provider"], account["mode"]) == ("payu", "test")
        assert account["webhook_secret_masked"] is None
        assert account["webhook_events"] == ["Successful", "Failed"]
        assert SALT not in json.dumps(account)
        (row,) = await rows(
            admin_session,
            "SELECT webhook_secret_enc FROM payment.merchant_accounts WHERE organization_id = :o",
            o=org_id,
        )
        assert row["webhook_secret_enc"] is None

    @pytest.mark.parametrize(
        "over",
        [{"webhook_secret": "dev-only-hook-secret"}, {"mode": None}, {"key_id": "bad key!"}],
    )
    async def test_a_malformed_payu_account_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID, over: dict[str, Any]
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.put(ACCOUNT, json=payu_body(**over))).status_code == 422

    async def test_production_needs_live_keys_allowed(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.put(ACCOUNT, json=payu_body(mode="production"))).status_code == 422

    @pytest.mark.parametrize(("perms", "amr"), [([READ], None), ([READ, WRITE], ["pwd"])])
    async def test_connecting_needs_the_write_permission_and_a_one_time_code(
        self, client: AsyncClient, org_id: uuid.UUID, perms: list[str], amr: list[str] | None
    ) -> None:
        with as_principal(principal_for(org_id, perms, amr=amr)):
            assert (await client.put(ACCOUNT, json=payu_body())).status_code == 403


class TestCheckout:
    async def test_without_a_payer_the_answer_is_payer_required_and_nothing_is_written(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await connect_payu(client, org_id)
        response = await checkout(client, org_id, uuid.uuid4())
        assert response.status_code == 422
        assert response.json()["type"].endswith("/payer-required")
        assert (
            await rows(
                admin_session, "SELECT id FROM payment.charges WHERE organization_id = :o", o=org_id
            )
            == []
        )

    async def test_the_browser_gets_a_form_in_exact_rupees_and_the_phone_is_kept_nowhere(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, response, _ = await opened(client, merchant_providers, org_id)
        handle = response["checkout"]
        assert (handle["provider"], handle["method"]) == ("payu", "POST")
        assert handle["url"] == "https://test.payu.in/_payment"
        fields = handle["fields"]
        assert fields["amount"] == "499.99" and fields["txnid"] == handle["provider_order_id"]
        assert fields["surl"] == fields["furl"] == "https://app.opsgrids.com/api/v1/pay/return"
        assert len(fields["txnid"]) == 25 and SALT not in json.dumps(response)
        assert (handle["amount_minor"], handle["openable"]) == (49_999, False)
        written = []
        for table in ("charges", "audit_logs", "outbox", "status_history"):
            written += await rows(
                admin_session,
                f"SELECT row_to_json(t)::text AS t FROM payment.{table} t "  # noqa: S608
                "WHERE organization_id = :o",
                o=org_id,
            )
        dumped = json.dumps(written, default=str)
        assert PHONE not in dumped and fields["hash"] not in dumped

    async def test_a_paid_transaction_is_never_handed_out_again(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, response, order = await opened(client, merchant_providers, org_id)
        wire(merchant_providers).pay(response["checkout"]["provider_order_id"], 49_999)
        again = await checkout(client, org_id, order, payer={"phone": PHONE})
        assert again.status_code == 409


class TestWebhooksStatusAndRefunds:
    async def test_a_genuine_success_captures_once_and_keeps_no_person(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        body = payu_webhook(response["checkout"]["provider_order_id"], "403993715531")
        first = await deliver(client, account["id"], body)
        assert first.status_code == 200 and first.json()["reconciled"] is True
        assert (await deliver(client, account["id"], body)).json()["duplicate"] is True
        assert await emitted(admin_session, org_id) == [CAPTURED]
        stored = json.dumps(await inbox_rows(admin_session, org_id), default=str)
        for kept_out in (PHONE, "Asha", "okaxis"):
            assert kept_out not in stored

    @pytest.mark.parametrize("variant", ["wrong_salt", "tampered_amount", "no_hash", "refund_json"])
    async def test_anything_payu_did_not_sign_is_400_and_records_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        variant: str,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        txnid = response["checkout"]["provider_order_id"]
        if variant == "wrong_salt":
            body = payu_webhook(txnid, "1", salt="dev-only-other-salt")
        elif variant == "tampered_amount":
            body = payu_webhook(txnid, "1").replace(b"amount=499.99", b"amount=1.00")
        elif variant == "no_hash":
            body = payu_webhook(txnid, "1").split(b"&hash=")[0]
        else:
            body = json.dumps({"action": "refund", "status": "success", "key": KEY}).encode()
        assert (await deliver(client, account["id"], body)).status_code == 400
        assert await inbox_rows(admin_session, org_id) == []

    async def test_a_status_check_then_a_refund_with_a_23_character_token(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, response, order = await opened(client, merchant_providers, org_id)
        charge_id = response["collection"]["id"]
        base = f"{internal_base(org_id, order)}/charges/{charge_id}"
        txnid = response["checkout"]["provider_order_id"]
        wire(merchant_providers).pay(txnid, 49_999, status="failure", unmapped="userCancelled")
        assert (await client.post(f"{base}/sync", headers=operations_token())).json()[
            "state"
        ] == "created"
        mihpayid = wire(merchant_providers).pay(txnid, 49_999)
        synced = (await client.post(f"{base}/sync", headers=operations_token())).json()
        assert synced["state"] == "captured" and synced["provider_payment_id"] == mihpayid
        refund = await client.post(
            f"{base}/refunds",
            headers=operations_token(),
            json={"amount_minor": 10_001, "reason": "damaged", "idempotency_key": "op-key-1"},
        )
        assert refund.status_code == 200, refund.text
        (made,) = refund.json()["refunds"]
        assert len(made["provider_refund_id"]) == 23 and made["state"] == "created"
        assert wire(merchant_providers).refunds[made["provider_refund_id"]]["Amount"] == "100.01"

    async def test_another_organizations_payu_salt_never_opens_this_account(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        account, response, _ = await opened(client, merchant_providers, org_id)
        await connect_payu(client, org_b, key_secret="dev-only-org-b-salt-01")
        body = payu_webhook(
            response["checkout"]["provider_order_id"], "9", salt="dev-only-org-b-salt-01"
        )
        assert (await deliver(client, account["id"], body)).status_code == 400
        with as_principal(principal_for(org_b, [READ])):
            seen = (await client.get(ACCOUNT)).json()
        assert seen["id"] != account["id"]


async def test_payus_form_post_to_the_return_page_is_a_static_page_from_any_origin(
    client: AsyncClient,
) -> None:
    response = await client.post(
        RETURN,
        content=payu_webhook("0" * 25, "1"),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://test.payu.in",
        },
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert PHONE not in response.text and "Asha" not in response.text
