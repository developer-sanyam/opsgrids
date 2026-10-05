"""Disconnecting a merchant account (ADR-0030 §8), through the real routes: what the provider may
still do for the account blocks it — an open payment, a cancelled or expired one whose provider
Order can still be paid, a refund still pending — and `force` disconnects anyway, audited with
what was outstanding. And a checkout made on the stub wire never wedges an account: it can be
cancelled, expired and disconnected from, through the real provider factory, whether or not the
stub still remembers its Order, and whether or not the wire has gone live since.

The merchant's Cashfree is the real `CashfreeCollectionsProvider` over the stateful fake
(`MerchantProviders`); `cashfree_live` makes its Orders count as made live, i.e. payable by a real
customer. No request leaves the machine.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import deps
from app.collections import services as collection_services
from app.collections.cron import CollectionExpirySweep
from app.config import settings
from app.models import Charge
from app.providers import cashfree_codec as codec
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.collections_support import (
    ACCOUNT,
    READ,
    WRITE,
    MerchantProviders,
    as_principal,
    checkout,
    connect,
    internal_base,
    operations_token,
    principal_for,
)
from tests.helpers import audit_rows, rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

APP_ID = "TESTdevonlyguard01"  # pragma: allowlist secret
SECRET = "dev-only-cashfree-guard-secret-0001"  # pragma: allowlist secret
PHONE = "9876501234"
TS = "1759745115000"
BLOCKED = "/disconnect-blocked"


async def _connect_cashfree(client: AsyncClient, org: uuid.UUID) -> dict[str, Any]:
    body = {"provider": "cashfree", "key_id": APP_ID, "key_secret": SECRET, "mode": "sandbox"}
    with as_principal(principal_for(org, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def _open_cashfree(
    client: AsyncClient, org: uuid.UUID, order: uuid.UUID | None = None
) -> tuple[str, uuid.UUID]:
    order = order or uuid.uuid4()
    response = await client.post(
        f"{internal_base(org, order)}/checkout",
        headers=operations_token(),
        json={"amount_minor": 50_000, "currency": "INR", "payer": {"phone": PHONE}},
    )
    assert response.status_code == 200, response.text
    return response.json()["collection"]["id"], order


async def _cancel(client: AsyncClient, org: uuid.UUID, order: uuid.UUID, charge_id: str) -> Any:
    return await client.post(
        f"{internal_base(org, order)}/charges/{charge_id}/cancel",
        headers=operations_token(),
        json={},
    )


async def _disconnect(client: AsyncClient, org: uuid.UUID, *, force: bool = False) -> Any:
    with as_principal(principal_for(org, [READ, WRITE])):
        return await client.delete(ACCOUNT, params={"force": "true"} if force else None)


async def _deliver(client: AsyncClient, account_id: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    headers = {"x-webhook-signature": codec.sign(SECRET, TS, body), "x-webhook-timestamp": TS}
    return await client.post(
        f"/api/v1/webhooks/collections?account={account_id}", content=body, headers=headers
    )


def _later(monkeypatch: pytest.MonkeyPatch, delta: timedelta) -> None:
    moved = datetime.now(UTC) + delta
    monkeypatch.setattr(collection_services, "_now", lambda: moved)


# ============================================================================ closed, still payable


class TestAClosedPaymentTheProviderCanStillTake:
    async def test_a_cancelled_cashfree_checkout_blocks_until_its_order_expires(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelling here does not close Cashfree's Order: it takes payment until its own
        expiry, and only a connected account can verify the webhook that says it was paid."""
        merchant_providers.cashfree_live = True
        await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        assert (await _cancel(client, org_id, order, charge_id)).status_code == 200
        blocked = await _disconnect(client, org_id)
        assert blocked.status_code == 409, blocked.text
        problem = blocked.json()
        assert problem["type"].endswith(BLOCKED)
        assert problem["detail"].startswith(
            "1 cancelled or expired payment can still be paid at Cashfree until"
        )
        assert "Cancel them from their orders, or wait" not in problem["detail"]
        # A day on, the Order has expired at Cashfree: nothing can land any more.
        _later(monkeypatch, timedelta(hours=25))
        assert (await _disconnect(client, org_id)).status_code == 204

    async def test_a_payment_landing_after_the_cancel_is_still_recorded_because_it_held(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
    ) -> None:
        merchant_providers.cashfree_live = True
        account = await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        assert (await _cancel(client, org_id, order, charge_id)).status_code == 200
        assert (await _disconnect(client, org_id)).status_code == 409
        # The customer approves the UPI request on their phone after all.
        paid = await _deliver(
            client,
            account["id"],
            {
                "type": "PAYMENT_SUCCESS_WEBHOOK",
                "event_time": "2026-10-06T10:15:15+05:30",
                "data": {
                    "order": {"order_id": charge_id, "order_amount": 500, "order_currency": "INR"},
                    "payment": {
                        "cf_payment_id": 5001,
                        "payment_status": "SUCCESS",
                        "payment_amount": 500,
                        "payment_currency": "INR",
                    },
                },
            },
        )
        assert paid.status_code == 200 and paid.json()["reconciled"] is True
        synced = await client.post(
            f"{internal_base(org_id, order)}/charges/{charge_id}/sync", headers=operations_token()
        )
        assert synced.json()["state"] == "captured"

    async def test_a_closed_checkout_the_stub_made_never_blocks(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        """No customer can pay an Order the stand-in made, so once cancelled it holds nothing."""
        await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        assert (await _cancel(client, org_id, order, charge_id)).status_code == 200
        assert (await _disconnect(client, org_id)).status_code == 204


# ============================================================================ pending refunds


class TestAPendingRefund:
    async def test_blocks_until_its_webhook_settles_it(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
    ) -> None:
        """A STANDARD Cashfree refund stays pending for days. Disconnected meanwhile, its later
        CANCELLED or FAILED would be refused, and the order would stay refunded for good."""
        merchant_providers.cashfree_live = True
        account = await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        merchant_providers.cashfree_wires[APP_ID].pay(charge_id)
        base = f"{internal_base(org_id, order)}/charges/{charge_id}"
        synced = await client.post(f"{base}/sync", headers=operations_token())
        assert synced.json()["state"] == "captured"
        refunded = await client.post(
            f"{base}/refunds",
            headers=operations_token(),
            json={"amount_minor": 50_000, "reason": "damaged", "idempotency_key": "guard-key-1"},
        )
        (refund,) = refunded.json()["refunds"]
        assert refund["state"] == "created"

        blocked = await _disconnect(client, org_id)
        assert blocked.status_code == 409
        assert blocked.json()["detail"].startswith("1 refund is still pending at Cashfree")

        made = merchant_providers.cashfree_wires[APP_ID].refunds[charge_id][0]
        settled = await _deliver(
            client,
            account["id"],
            {
                "type": "REFUND_STATUS_WEBHOOK",
                "event_time": "2026-10-06T11:00:00+05:30",
                "data": {
                    "refund": {
                        "cf_refund_id": made["cf_refund_id"],
                        "cf_payment_id": made["cf_payment_id"],
                        "refund_id": refund["provider_refund_id"],
                        "order_id": charge_id,
                        "refund_amount": 500,
                        "refund_currency": "INR",
                        "refund_status": "CANCELLED",
                    }
                },
            },
        )
        assert settled.json()["reconciled"] is True
        assert (await _disconnect(client, org_id)).status_code == 204


# ============================================================================ force


class TestForce:
    async def test_disconnects_anyway_and_audits_what_was_outstanding(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        merchant_providers.cashfree_live = True
        await _connect_cashfree(client, org_id)
        await _open_cashfree(client, org_id)
        assert (await _disconnect(client, org_id)).status_code == 409
        assert (await _disconnect(client, org_id, force=True)).status_code == 204
        removed = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.merchant_account.removed"
        ]
        assert len(removed) == 1
        assert removed[0]["after"]["forced"] is True
        assert removed[0]["after"]["open_collections"] == 1
        assert SECRET not in json.dumps(removed, default=str)

    async def test_nothing_outstanding_is_an_ordinary_disconnect(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _connect_cashfree(client, org_id)
        assert (await _disconnect(client, org_id, force=True)).status_code == 204
        (removed,) = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.merchant_account.removed"
        ]
        assert removed["after"] is None

    @pytest.mark.parametrize(
        ("perms", "amr"),
        [([READ], ["pwd", "otp"]), ([READ, WRITE], ["pwd"])],
        ids=["without-the-write-permission", "without-a-one-time-code-session"],
    )
    async def test_needs_the_same_permission_and_session_as_any_disconnect(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        perms: list[str],
        amr: list[str],
    ) -> None:
        await _connect_cashfree(client, org_id)
        await _open_cashfree(client, org_id)
        with as_principal(principal_for(org_id, perms, amr=amr)):
            response = await client.delete(ACCOUNT, params={"force": "true"})
        assert response.status_code == 403
        still = await rows(
            admin_session,
            "SELECT status FROM payment.merchant_accounts WHERE organization_id = :o",
            o=org_id,
        )
        assert still == [{"status": "active"}]

    async def test_another_organization_cannot_force_it(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        await _connect_cashfree(client, org_id)
        await _open_cashfree(client, org_id)
        with as_principal(principal_for(org_b, [READ, WRITE])):
            assert (await client.delete(ACCOUNT, params={"force": "true"})).status_code == 404
        still = await rows(
            admin_session,
            "SELECT status FROM payment.merchant_accounts WHERE organization_id = :o",
            o=org_id,
        )
        assert still == [{"status": "active"}]


# ============================================================================ the stub wire


class TestAStubCheckoutNeverWedgesTheAccount:
    """Through the **real** provider factory (`deps._collections_provider`, no test override),
    which builds a merchant's adapter afresh for every call: the adapter asked about an Order is
    never the one that made it."""

    async def test_razorpay_cancel_then_disconnect(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        order = uuid.uuid4()
        opened = await checkout(client, org_id, order)
        assert opened.status_code == 200, opened.text
        charge_id = opened.json()["collection"]["id"]
        (made,) = await rows(
            admin_session,
            "SELECT order_wire FROM payment.charges WHERE id = :id",
            id=uuid.UUID(charge_id),
        )
        assert made == {"order_wire": "stub"}
        assert (await _disconnect(client, org_id)).status_code == 409
        cancelled = await _cancel(client, org_id, order, charge_id)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"
        assert (await _disconnect(client, org_id)).status_code == 204

    async def test_cashfree_cancel_after_the_stub_forgot_its_order(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """The Cashfree stand-in keeps its Orders in this process: a restart, or the worker
        asking instead of the API, finds none."""
        await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        deps._CASHFREE_STUB_WIRES.clear()
        cancelled = await _cancel(client, org_id, order, charge_id)
        assert cancelled.status_code == 200, cancelled.text
        assert (await _disconnect(client, org_id)).status_code == 204

    async def test_the_expiry_sweep_closes_one_it_cannot_ask_about(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        opened = await checkout(client, org_id, uuid.uuid4())
        charge_id = uuid.UUID(opened.json()["collection"]["id"])
        async with admin_session.begin():
            await admin_session.execute(
                update(Charge)
                .where(Charge.id == charge_id)
                .values(created_at=datetime.now(UTC) - timedelta(days=2))
            )
        sweep = CollectionExpirySweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda org: session_scope(organization_id=org),
            ttl=timedelta(days=1),
        )
        report = await sweep.tick()
        assert (report.expired, report.failed) == (1, 0)
        assert (await _disconnect(client, org_id)).status_code == 204

    async def test_once_the_wire_is_live_it_is_closed_without_asking_and_never_reopened(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cashfree has never heard of the stand-in's Order: asking about it would be refused
        (wedging the collection), and "reopening" it would quietly make a new, real Order."""
        await _connect_cashfree(client, org_id)
        charge_id, order = await _open_cashfree(client, org_id)
        monkeypatch.setattr(settings(), "collections_cashfree_wire", "live")
        fake = merchant_providers.cashfree_wires[APP_ID]
        asked = len(fake.requests)
        reopened = await client.post(
            f"{internal_base(org_id, order)}/checkout",
            headers=operations_token(),
            json={"amount_minor": 50_000, "currency": "INR", "payer": {"phone": PHONE}},
        )
        assert reopened.status_code == 409
        assert "before online payments went live" in reopened.json()["detail"]
        assert (await _cancel(client, org_id, order, charge_id)).status_code == 200
        assert len(fake.requests) == asked, "a provider was asked about the stand-in's Order"
        assert (await _disconnect(client, org_id)).status_code == 204
