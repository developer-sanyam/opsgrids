"""Merchant connect with Cashfree (ADR-0030), through the real routes: connecting, verifying,
disconnecting, the one-time-code rule, the payer a Cashfree checkout needs, the neutral webhook
path, status checks, refunds and the reconciliation report — with negative-authorization and
cross-tenant cases for every admin route.

The merchant's Cashfree is the real `CashfreeCollectionsProvider` over the stateful
`CashfreeFakeWire` (`MerchantProviders.cashfree_wires`): what a test makes "the customer" do there
is what a status check or a webhook then reports. No request leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.collections import services as collection_services
from app.config import settings
from app.providers import cashfree_codec as codec
from app.providers.cashfree_collections import CashfreeCollectionsProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from platform_core.errors import ConflictProblem
from tests.collections_support import (
    ACCOUNT,
    READ,
    RECON,
    RECONCILIATION,
    WRITE,
    MerchantProviders,
    as_principal,
    connect,
    internal_base,
    merchant_signer,
    operations_token,
    payment_webhook,
    principal_for,
)
from tests.helpers import audit_rows, emitted, inbox_rows, rows

pytestmark = pytest.mark.integration

APP_ID = "TESTdevonlymerchant01"  # pragma: allowlist secret
SECRET = "dev-only-cashfree-merchant-secret-0001"  # pragma: allowlist secret
PHONE = "9876501234"
VERIFY = f"{ACCOUNT}/verify"
NEUTRAL = "/api/v1/webhooks/collections"
LEGACY = "/api/v1/webhooks/razorpay/collections"
CAPTURED = "payment.collections.payment.captured"
FAILED = "payment.collections.payment.failed"
REFUNDED = "payment.collections.payment.refunded"
TS = "1759745115000"


async def connect_cashfree(client: AsyncClient, org: uuid.UUID, **over: Any) -> dict[str, Any]:
    body = {
        "provider": "cashfree",
        "key_id": APP_ID,
        "key_secret": SECRET,
        "mode": "sandbox",
        **over,
    }
    with as_principal(principal_for(org, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def cf_checkout(
    client: AsyncClient,
    org: uuid.UUID,
    order: uuid.UUID,
    *,
    payer: dict[str, Any] | None = None,
    amount_minor: int = 50_000,
) -> Any:
    body: dict[str, Any] = {"amount_minor": amount_minor, "currency": "INR"}
    if payer is not None:
        body["payer"] = payer
    return await client.post(
        f"{internal_base(org, order)}/checkout", headers=operations_token(), json=body
    )


def cf_body(
    etype: str,
    *,
    order_id: str,
    status: str = "SUCCESS",
    cf_payment_id: int = 3001,
    amount: str = "500.00",
    event_time: str = "2026-10-06T10:15:15+05:30",
    **extra: Any,
) -> bytes:
    data: dict[str, Any] = {
        "order": {
            "order_id": order_id,
            "order_amount": json.loads(amount),
            "order_currency": "INR",
        },
        "payment": {
            "cf_payment_id": cf_payment_id,
            "payment_status": status,
            "payment_amount": json.loads(amount),
            "payment_currency": "INR",
            "payment_method": {"upi": {"upi_id": "buyer@okbank"}},
        },
        "customer_details": {"customer_phone": PHONE, "customer_email": "buyer@example.com"},
        **extra,
    }
    return json.dumps({"type": etype, "event_time": event_time, "data": data}).encode()


def cf_headers(body: bytes, *, secret: str = SECRET, delivery: str | None = None) -> dict[str, str]:
    headers = {"x-webhook-signature": codec.sign(secret, TS, body), "x-webhook-timestamp": TS}
    if delivery is not None:
        headers["x-idempotency-header"] = delivery
    return headers


async def deliver(
    client: AsyncClient, account_id: str, body: bytes, *, path: str = NEUTRAL, **kw: Any
) -> Any:
    return await client.post(
        f"{path}?account={account_id}", content=body, headers=cf_headers(body, **kw)
    )


def wire(providers: MerchantProviders) -> CashfreeFakeWire:
    return providers.cashfree_wires[APP_ID]


async def _opened(
    client: AsyncClient, providers: MerchantProviders, org: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
    account = await connect_cashfree(client, org)
    order = uuid.uuid4()
    response = await cf_checkout(client, org, order, payer={"name": "Buyer", "phone": PHONE})
    assert response.status_code == 200, response.text
    return account, response.json(), order


# ============================================================================ connecting


class TestConnect:
    async def test_a_cashfree_account_holds_one_sealed_secret_and_starts_unverified(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        saved = await connect_cashfree(client, org_id)
        assert (saved["provider"], saved["key_id"], saved["mode"]) == ("cashfree", APP_ID, "test")
        assert saved["key_secret_masked"] == "••••0001"
        assert saved["webhook_secret_masked"] is None
        assert saved["connection_state"] == "unverified" and saved["verified_at"] is None
        assert saved["webhook_path"] == f"{NEUTRAL}?account={saved['id']}"
        base = settings().app_base_url.rstrip("/")
        assert saved["webhook_url"] == f"{base}{NEUTRAL}?account={saved['id']}"
        assert "PAYMENT_SUCCESS_WEBHOOK" in saved["webhook_events"]
        assert saved["wires"] == {"razorpay": "stub", "cashfree": "stub"}
        assert SECRET not in json.dumps(saved)
        stored = await rows(
            admin_session,
            "SELECT key_secret_enc, webhook_secret_enc, verified_at FROM payment.merchant_accounts "
            "WHERE organization_id = :org",
            org=org_id,
        )
        assert stored[0]["webhook_secret_enc"] is None
        assert SECRET.encode() not in bytes(stored[0]["key_secret_enc"])
        audits = await audit_rows(admin_session, org_id)
        assert [a["action"] for a in audits] == ["payment.merchant_account.connected"]
        assert SECRET not in json.dumps(audits, default=str)

    async def test_production_needs_live_keys_allowed(
        self, client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = {
            "provider": "cashfree",
            "key_id": "APP0001",
            "key_secret": SECRET,
            "mode": "production",
        }
        with as_principal(principal_for(org_id, [WRITE])):
            refused = await client.put(ACCOUNT, json=body)
            assert refused.status_code == 422 and "Production is not enabled" in refused.text
            monkeypatch.setattr(settings(), "collections_allow_live_keys", True)
            allowed = await client.put(ACCOUNT, json=body)
        assert allowed.status_code == 200 and allowed.json()["mode"] == "live"

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ({"provider": "cashfree", "key_id": APP_ID, "key_secret": SECRET}, "mode"),
            (
                {
                    "provider": "cashfree",
                    "key_id": APP_ID,
                    "key_secret": SECRET,
                    "mode": "sandbox",
                    "webhook_secret": "w" * 12,
                },
                "webhook_secret",
            ),
            (
                {"provider": "razorpay", "key_id": "rzp_test_Abc123", "key_secret": SECRET},
                "webhook",
            ),
            (
                {
                    "provider": "razorpay",
                    "key_id": "rzp_test_Abc123",
                    "key_secret": SECRET,
                    "webhook_secret": "w" * 12,
                    "mode": "sandbox",
                },
                "mode",
            ),
        ],
    )
    async def test_each_provider_takes_only_its_own_shape(
        self, client: AsyncClient, org_id: uuid.UUID, body: dict[str, Any], fragment: str
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.put(ACCOUNT, json=body)
        assert response.status_code == 422 and fragment in response.text
        assert SECRET not in response.text

    async def test_switching_provider_is_disconnect_then_connect(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        razorpay = await connect(client, org_id)
        with as_principal(principal_for(org_id, [WRITE])):
            refused = await client.put(
                ACCOUNT,
                json={
                    "provider": "cashfree",
                    "key_id": APP_ID,
                    "key_secret": SECRET,
                    "mode": "sandbox",
                },
            )
            assert refused.status_code == 409 and "cannot be changed" in refused.text
            assert (await client.delete(ACCOUNT)).status_code == 204
        cashfree = await connect_cashfree(client, org_id)
        assert cashfree["id"] != razorpay["id"] and cashfree["provider"] == "cashfree"

    async def test_rotation_keeps_the_row_and_makes_it_unverified_again(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        first = await connect_cashfree(client, org_id)
        with as_principal(principal_for(org_id, [WRITE])):
            assert (await client.post(VERIFY)).json()["connection_state"] == "verified"
        rotated = await connect_cashfree(client, org_id, key_secret="dev-only-rotated-9999")
        assert rotated["id"] == first["id"] and rotated["key_secret_masked"] == "••••9999"
        assert rotated["connection_state"] == "unverified" and rotated["rotated_at"] is not None


# ============================================================================ one-time code


class TestTheOneTimeCodeRule:
    @pytest.mark.parametrize(
        ("method", "path"), [("PUT", ACCOUNT), ("DELETE", ACCOUNT), ("POST", VERIFY)]
    )
    async def test_changing_where_money_goes_needs_an_otp_session(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        method: str,
        path: str,
    ) -> None:
        await connect_cashfree(client, org_id)
        body = (
            {"provider": "cashfree", "key_id": APP_ID, "key_secret": SECRET, "mode": "sandbox"}
            if method == "PUT"
            else None
        )
        with as_principal(principal_for(org_id, [READ, WRITE], amr=["pwd"])):
            response = await client.request(method, path, json=body)
        assert response.status_code == 403
        assert response.json()["detail"] == "Additional authentication required: otp"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions == ["payment.merchant_account.connected"]

    async def test_reading_does_not(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        await connect_cashfree(client, org_id)
        with as_principal(principal_for(org_id, [READ], amr=["pwd"])):
            assert (await client.get(ACCOUNT)).status_code == 200


# ============================================================================ verification


class TestVerify:
    async def test_good_credentials_are_verified_recorded_and_audited(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await connect_cashfree(client, org_id)
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.post(VERIFY)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["connection_state"] == "verified" and body["verified_at"] is not None
        assert body["last_error_code"] is None
        # One read, of an Order nobody made: nothing was created at the provider.
        assert [m for m, _ in wire(merchant_providers).requests] == ["GET"]
        assert ("cashfree", "test") in merchant_providers.built
        audits = await audit_rows(admin_session, org_id)
        verified = [a for a in audits if a["action"] == "payment.merchant_account.verified"]
        assert len(verified) == 1 and verified[0]["after"]["connection_state"] == "verified"
        assert SECRET not in json.dumps(audits, default=str)

    async def test_rejected_credentials_are_recorded_then_a_422_with_a_hint(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await connect_cashfree(client, org_id)
        merchant_providers.cashfree_wires[APP_ID] = refusing = CashfreeFakeWire()
        refusing.reject_credentials = True
        with as_principal(principal_for(org_id, [READ, WRITE])):
            response = await client.post(VERIFY)
            assert response.status_code == 422
            assert response.json()["type"].endswith("/credentials-rejected")
            assert "Sandbox or Production" in response.json()["detail"]
            account = (await client.get(ACCOUNT)).json()
        assert account["connection_state"] == "failed"
        assert account["last_error_code"] == "credentials_rejected"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.merchant_account.verification_failed" in actions

    async def test_an_unreachable_provider_records_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await connect_cashfree(client, org_id)

        def unreachable(self: Any) -> None:
            raise httpx.ConnectError("no route")

        monkeypatch.setattr(CashfreeCollectionsProvider, "probe", unreachable)
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.post(VERIFY)).status_code == 503
            assert (await client.get(ACCOUNT)).json()["connection_state"] == "unverified"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions == ["payment.merchant_account.connected"]

    async def test_the_probe_is_bounded_by_the_collections_call_timeout(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await connect_cashfree(client, org_id)
        monkeypatch.setattr(settings(), "collections_call_timeout_seconds", 0.05)
        monkeypatch.setattr(CashfreeCollectionsProvider, "probe", lambda self: time.sleep(0.4))
        started = time.monotonic()
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.post(VERIFY)
        assert response.status_code == 503 and time.monotonic() - started < 0.35
        await asyncio.sleep(0.4)  # let the abandoned thread finish before the loop closes

    async def test_no_transaction_is_open_while_the_provider_is_probed(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await connect_cashfree(client, org_id)
        seen: list[bool] = []
        original = CashfreeCollectionsProvider.probe

        def spy(self: Any) -> None:
            seen.append(session.in_transaction())
            original(self)

        monkeypatch.setattr(CashfreeCollectionsProvider, "probe", spy)
        with as_principal(principal_for(org_id, [WRITE])):
            assert (await client.post(VERIFY)).status_code == 200
        assert seen == [False], "the provider was probed while a transaction was open"

    async def test_a_verdict_on_old_keys_never_stands_for_new_ones(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await connect_cashfree(client, org_id)
        async with session.begin():
            await collection_services.bind(session, org_id)
            ticket = await collection_services.claim_verification(session, organization_id=org_id)
        await connect_cashfree(client, org_id, key_secret="dev-only-rotated-in-between")
        async with session.begin():
            await collection_services.bind(session, org_id)
            with pytest.raises(ConflictProblem):
                await collection_services.record_verification(
                    session,
                    organization_id=org_id,
                    ticket=ticket,
                    outcome=collection_services.ProbeOutcome(accepted=True),
                    actor_id=None,
                )
        assert "dev-only-rotated" not in repr(ticket) and SECRET not in repr(ticket)

    async def test_razorpay_is_verified_the_same_way(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.post(VERIFY)
        assert response.status_code == 200 and response.json()["connection_state"] == "verified"


# ============================================================================ authorization


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path", "perms"),
        [
            ("GET", ACCOUNT, []),
            ("PUT", ACCOUNT, [READ]),
            ("DELETE", ACCOUNT, [READ]),
            ("POST", VERIFY, [READ]),
            ("POST", VERIFY, [READ, RECON]),
            ("GET", RECONCILIATION, [READ, WRITE]),
            ("POST", VERIFY, ["operations.payments.merchant_account.write:OWN"]),
        ],
    )
    async def test_every_route_refuses_a_caller_without_its_permission(
        self, client: AsyncClient, org_id: uuid.UUID, method: str, path: str, perms: list[str]
    ) -> None:
        await connect_cashfree(client, org_id)
        body = (
            {"provider": "cashfree", "key_id": APP_ID, "key_secret": SECRET, "mode": "sandbox"}
            if method == "PUT"
            else None
        )
        with as_principal(principal_for(org_id, perms)):
            response = await client.request(method, path, json=body)
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize(
        ("method", "path"), [("GET", ACCOUNT), ("POST", VERIFY), ("GET", RECONCILIATION)]
    )
    async def test_without_operations_every_route_is_402(
        self, client: AsyncClient, org_id: uuid.UUID, method: str, path: str
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE, RECON], entitlements=["crm"])):
            assert (await client.request(method, path)).status_code == 402

    async def test_another_organization_can_neither_see_verify_remove_nor_reconcile_it(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        await connect_cashfree(client, org_id)
        with as_principal(principal_for(org_b, [READ, WRITE, RECON])):
            assert (await client.get(ACCOUNT)).json()["configured"] is False
            assert (await client.post(VERIFY)).status_code == 404
            assert (await client.delete(ACCOUNT)).status_code == 404
            assert (await client.get(RECONCILIATION)).status_code == 409
        still = await rows(
            admin_session,
            "SELECT status, verified_at FROM payment.merchant_accounts "
            "WHERE organization_id = :org",
            org=org_id,
        )
        assert still == [{"status": "active", "verified_at": None}]
        assert merchant_providers.built == []  # nothing was ever asked of org A's provider


# ============================================================================ checkout


class TestCheckout:
    async def test_without_a_payer_the_answer_is_payer_required_and_nothing_is_written(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await connect_cashfree(client, org_id)
        response = await cf_checkout(client, org_id, uuid.uuid4())
        assert response.status_code == 422
        assert response.json()["type"].endswith("/payer-required")
        assert (
            await rows(
                admin_session, "SELECT id FROM payment.charges WHERE organization_id = :o", o=org_id
            )
            == []
        )

    @pytest.mark.parametrize("phone", [None, "", "12345", "not a phone"])
    async def test_without_a_usable_phone_the_answer_is_phone_required_never_an_invented_one(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        phone: str | None,
    ) -> None:
        await connect_cashfree(client, org_id)
        response = await cf_checkout(
            client, org_id, uuid.uuid4(), payer={"name": "Buyer", "phone": phone}
        )
        assert response.status_code == 422
        assert response.json()["type"].endswith("/phone-required")
        assert (
            await rows(
                admin_session, "SELECT id FROM payment.charges WHERE organization_id = :o", o=org_id
            )
            == []
        )
        assert (
            APP_ID not in merchant_providers.cashfree_wires or not wire(merchant_providers).orders
        )

    async def test_a_checkout_hands_the_browser_a_session_and_keeps_the_phone_nowhere(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            _, opened, _ = await _opened(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        checkout = opened["checkout"]
        assert checkout["provider"] == "cashfree" and checkout["mode"] == "sandbox"
        assert checkout["payment_session_id"].startswith("session_stub_")
        assert checkout["provider_order_id"] == charge_id  # the Order's id is the collection's
        assert checkout["openable"] is False  # the stub cannot be paid for real
        assert "key_id" not in checkout and SECRET not in json.dumps(opened)
        order = wire(merchant_providers).orders[charge_id]
        assert order["order_amount"] == Decimal("500.00")
        # The customer went to Cashfree, pseudonymously, and nowhere else.
        written = []
        for table in ("charges", "audit_logs", "outbox", "status_history", "merchant_accounts"):
            written += await rows(
                admin_session,
                f"SELECT row_to_json(t)::text AS t FROM payment.{table} t "  # noqa: S608
                "WHERE organization_id = :o",
                o=org_id,
            )
        dumped = json.dumps({"logs": logs, "rows": written}, default=str)
        assert PHONE not in dumped and "Buyer" not in dumped
        assert "session_stub_" not in dumped  # the session token is never stored or logged

    async def test_reopening_asks_cashfree_again_and_creates_nothing(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, opened, order = await _opened(client, merchant_providers, org_id)
        again = await cf_checkout(client, org_id, order, payer={"phone": PHONE})
        assert again.status_code == 200, again.text
        assert again.json()["collection"]["id"] == opened["collection"]["id"]
        assert (
            again.json()["checkout"]["payment_session_id"]
            == opened["checkout"]["payment_session_id"]
        )
        assert wire(merchant_providers).requests.count(("POST", "/orders")) == 1

    async def test_an_order_that_expired_at_cashfree_is_a_409_with_a_way_out(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, opened, order = await _opened(client, merchant_providers, org_id)
        wire(merchant_providers).expire(opened["collection"]["id"])
        again = await cf_checkout(client, org_id, order, payer={"phone": PHONE})
        assert again.status_code == 409 and "cancel it" in again.json()["detail"]

    def test_the_notify_url_is_sent_only_when_the_platform_is_public_https(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models import MerchantAccount

        account = MerchantAccount(
            id=uuid.uuid4(), organization_id=uuid.uuid4(), provider="cashfree"
        )
        monkeypatch.setattr(settings(), "app_base_url", "http://localhost:5273")
        assert collection_services._notify_url(account) is None
        monkeypatch.setattr(settings(), "app_base_url", "https://app.opsgrids.example/")
        assert collection_services._notify_url(account) == (
            f"https://app.opsgrids.example/api/v1/webhooks/collections?account={account.id}"
        )


# ============================================================================ status and webhooks


@pytest.mark.usefixtures("engines")
class TestStatusAndWebhooks:
    async def test_a_status_check_finds_the_payment_and_a_dropped_attempt_moves_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order = await _opened(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        sync = f"{internal_base(org_id, order)}/charges/{charge_id}/sync"
        wire(merchant_providers).pay(charge_id, status="USER_DROPPED")
        assert (await client.post(sync, headers=operations_token())).json()["state"] == "created"
        wire(merchant_providers).pay(charge_id, status="SUCCESS")
        after = (await client.post(sync, headers=operations_token())).json()
        assert after["state"] == "captured" and after["provider"] == "cashfree"
        assert await emitted(admin_session, org_id) == [CAPTURED]

    async def test_a_signed_success_on_the_neutral_path_captures_once(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, opened, _ = await _opened(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        body = cf_body("PAYMENT_SUCCESS_WEBHOOK", order_id=charge_id)
        first = await deliver(client, account["id"], body, delivery="dlv-1")
        assert first.status_code == 200 and first.json()["reconciled"] is True
        again = await deliver(client, account["id"], body, delivery="dlv-1")
        assert again.json()["duplicate"] is True
        assert await emitted(admin_session, org_id) == [CAPTURED]
        inbox = await inbox_rows(admin_session, org_id)
        assert len(inbox) == 1 and inbox[0]["provider_event_id"] == "dlv-1"
        stored = json.dumps(inbox[0]["payload"])
        for pii in (PHONE, "buyer@example.com", "buyer@okbank"):
            assert pii not in stored
        with as_principal(principal_for(org_id, [READ])):
            assert (await client.get(ACCOUNT)).json()["last_webhook_at"] is not None

    async def test_another_integrations_order_keeps_its_buyer_out_of_the_inbox(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The account-wide webhook delivers the merchant's own website's Orders too. One of those
        verifies (same Secret Key), finds no collection, and is recorded as ignored — without the
        buyer its integration put in `order_tags`."""
        account = await connect_cashfree(client, org_id)
        body = json.loads(cf_body("PAYMENT_SUCCESS_WEBHOOK", order_id="shop-order-7781"))
        body["data"]["order"]["order_tags"] = {
            "customer_email": "jane@example.com",
            "ship_to": "12 MG Road Pune 411001",
        }
        raw = json.dumps(body).encode()
        response = await deliver(client, account["id"], raw, delivery="dlv-shop-1")
        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "unknown_charge"
        inbox = await inbox_rows(admin_session, org_id)
        assert len(inbox) == 1
        stored = json.dumps(inbox[0]["payload"])
        for pii in ("jane@example.com", "MG Road", "411001", PHONE, "buyer@example.com"):
            assert pii not in stored

    async def test_the_legacy_path_serves_a_cashfree_account_too_the_provider_is_the_rows(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        account, opened, _ = await _opened(client, merchant_providers, org_id)
        body = cf_body("PAYMENT_SUCCESS_WEBHOOK", order_id=opened["collection"]["id"])
        response = await deliver(client, account["id"], body, path=LEGACY)
        assert response.status_code == 200 and response.json()["reconciled"] is True

    async def test_a_razorpay_signature_never_verifies_for_a_cashfree_account(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        account, opened, _ = await _opened(client, merchant_providers, org_id)
        body = payment_webhook(
            event="payment.captured", payment_id="pay_1", order_id=opened["collection"]["id"]
        )
        for path in (NEUTRAL, LEGACY):
            response = await client.post(
                f"{path}?account={account['id']}",
                content=body,
                headers={"X-Razorpay-Signature": merchant_signer().sign(body)},
            )
            assert response.status_code == 400

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"x-webhook-timestamp": TS},
            {"x-webhook-signature": "AAAA", "x-webhook-timestamp": TS},
        ],
    )
    async def test_an_unsigned_or_forged_delivery_is_400_and_records_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        headers: dict[str, str],
    ) -> None:
        account, opened, _ = await _opened(client, merchant_providers, org_id)
        body = cf_body("PAYMENT_SUCCESS_WEBHOOK", order_id=opened["collection"]["id"])
        response = await client.post(
            f"{NEUTRAL}?account={account['id']}", content=body, headers=headers
        )
        assert response.status_code == 400
        assert await inbox_rows(admin_session, org_id) == []

    async def test_a_failed_attempt_then_a_success_on_the_same_order(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, opened, _ = await _opened(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        failed = cf_body(
            "PAYMENT_FAILED_WEBHOOK",
            order_id=charge_id,
            status="FAILED",
            cf_payment_id=4001,
            error_details={"error_code": "TRANSACTION_DECLINED", "error_description": "no"},
        )
        assert (await deliver(client, account["id"], failed)).json()["reconciled"] is True
        dropped = cf_body(
            "PAYMENT_USER_DROPPED_WEBHOOK",
            order_id=charge_id,
            status="USER_DROPPED",
            cf_payment_id=4002,
            event_time="2026-10-06T10:16:00+05:30",
        )
        ack = (await deliver(client, account["id"], dropped)).json()
        assert ack["ignored_reason"] == "unhandled_event" and ack["reconciled"] is False
        success = cf_body(
            "PAYMENT_SUCCESS_WEBHOOK",
            order_id=charge_id,
            cf_payment_id=4003,
            event_time="2026-10-06T10:17:00+05:30",
        )
        assert (await deliver(client, account["id"], success)).json()["reconciled"] is True
        assert await emitted(admin_session, org_id) == [FAILED, CAPTURED]
        found = await rows(
            admin_session,
            "SELECT state, provider_payment_id FROM payment.charges WHERE id = :id",
            id=uuid.UUID(charge_id),
        )
        assert found == [{"state": "captured", "provider_payment_id": "4003"}]

    async def test_a_razorpay_account_is_served_on_the_neutral_path_too(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        account = await connect(client, org_id)
        body = payment_webhook(event="payment.captured", payment_id="pay_1", order_id="order_none")
        response = await client.post(
            f"{NEUTRAL}?account={account['id']}",
            content=body,
            headers={"X-Razorpay-Signature": merchant_signer().sign(body)},
        )
        assert response.status_code == 200 and response.json()["ignored_reason"] == "unknown_charge"


# ============================================================================ refunds, disconnect


@pytest.mark.usefixtures("engines")
class TestRefundsAndDisconnect:
    async def _paid(
        self, client: AsyncClient, providers: MerchantProviders, org: uuid.UUID
    ) -> tuple[dict[str, Any], str, uuid.UUID]:
        account, opened, order = await _opened(client, providers, org)
        charge_id = opened["collection"]["id"]
        wire(providers).pay(charge_id)
        synced = await client.post(
            f"{internal_base(org, order)}/charges/{charge_id}/sync", headers=operations_token()
        )
        assert synced.json()["state"] == "captured"
        return account, charge_id, order

    async def test_a_refund_is_made_with_our_refund_id_and_its_webhook_settles_it(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account, charge_id, order = await self._paid(client, merchant_providers, org_id)
        refunded = await client.post(
            f"{internal_base(org_id, order)}/charges/{charge_id}/refunds",
            headers=operations_token(),
            json={"amount_minor": 10_000, "reason": "damaged", "idempotency_key": "operator-key-1"},
        )
        assert refunded.status_code == 200, refunded.text
        (refund,) = refunded.json()["refunds"]
        assert refund["state"] == "created" and len(refund["provider_refund_id"]) == 39
        assert refund["provider_refund_id"].startswith("rfd")
        made = wire(merchant_providers).refunds[charge_id]
        assert [r["refund_id"] for r in made] == [refund["provider_refund_id"]]
        # The same operator key again is the same refund — here and at Cashfree.
        again = await client.post(
            f"{internal_base(org_id, order)}/charges/{charge_id}/refunds",
            headers=operations_token(),
            json={"amount_minor": 10_000, "reason": "damaged", "idempotency_key": "operator-key-1"},
        )
        assert (
            len(again.json()["refunds"]) == 1
            and len(wire(merchant_providers).refunds[charge_id]) == 1
        )
        status = json.dumps(
            {
                "type": "REFUND_STATUS_WEBHOOK",
                "event_time": "2026-10-06T11:00:00+05:30",
                "data": {
                    "refund": {
                        "cf_refund_id": made[0]["cf_refund_id"],
                        "cf_payment_id": made[0]["cf_payment_id"],
                        "refund_id": refund["provider_refund_id"],
                        "order_id": charge_id,
                        "refund_amount": 100.00,
                        "refund_currency": "INR",
                        "refund_status": "SUCCESS",
                    }
                },
            }
        ).encode()
        assert (await deliver(client, account["id"], status)).json()["reconciled"] is True
        view = (
            await client.post(
                f"{internal_base(org_id, order)}/charges/{charge_id}/sync",
                headers=operations_token(),
            )
        ).json()
        assert view["refunds"][0]["state"] == "processed" and view["refunded_minor"] == 10_000
        assert (await emitted(admin_session, org_id)).count(REFUNDED) == 2

    async def test_disconnect_is_blocked_with_the_count_while_a_payment_is_open(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order = await _opened(client, merchant_providers, org_id)
        with as_principal(principal_for(org_id, [READ, WRITE])):
            blocked = await client.delete(ACCOUNT)
            assert blocked.status_code == 409
            assert blocked.json()["detail"].startswith("1 online payment is still open")
            assert blocked.json()["type"].endswith("/disconnect-blocked")
        cancelled = await client.post(
            f"{internal_base(org_id, order)}/charges/{opened['collection']['id']}/cancel",
            headers=operations_token(),
            json={},
        )
        assert cancelled.status_code == 200
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.delete(ACCOUNT)).status_code == 204

    async def test_a_refund_after_disconnect_is_a_409(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, charge_id, order = await self._paid(client, merchant_providers, org_id)
        with as_principal(principal_for(org_id, [WRITE])):
            assert (await client.delete(ACCOUNT)).status_code == 204  # captured is not open
        refused = await client.post(
            f"{internal_base(org_id, order)}/charges/{charge_id}/refunds",
            headers=operations_token(),
            json={"amount_minor": 100, "idempotency_key": "operator-key-2"},
        )
        assert refused.status_code == 409 and "dashboard" in refused.json()["detail"]

    async def test_reconciliation_compares_with_cashfree(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        await self._paid(client, merchant_providers, org_id)
        with as_principal(principal_for(org_id, [RECON])):
            report = (await client.get(RECONCILIATION)).json()
        assert report["checked"] == 1 and report["mismatched"] == 0
        assert report["rows"][0]["provider_status"] == "captured"


def test_reference_view_names_the_provider_and_its_wire() -> None:
    """`ReferenceCollectionsOut` carries the connected provider and whether its wire is live, so
    operations can tell the UI without another call."""
    from app.schemas import ReferenceCollectionsOut

    fields = ReferenceCollectionsOut.model_fields
    assert {"provider", "provider_wire"} <= set(fields)


def test_the_payer_never_reaches_a_repr() -> None:
    from app.schemas import CheckoutIn

    payload = CheckoutIn(amount_minor=1, currency="INR", payer={"phone": PHONE, "name": "Buyer"})
    assert PHONE not in repr(payload) and "Buyer" not in repr(payload)
    payer = collection_services.Payer(phone=PHONE, name="Buyer")
    assert PHONE not in repr(payer)
