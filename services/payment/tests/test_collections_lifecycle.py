"""The rest of a collection's life: the server-to-server status check, cancellation, refunds
(including ones the provider reports that this platform never asked for), expiry, and the
read-only reconciliation report.

"The customer paid" is only ever learned from the provider: a status check asks the provider with
the merchant's own keys, and its route takes no body at all — there is nothing a browser could
send that would count.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.collections.cron import CollectionExpirySweep
from app.models import Charge
from app.selectors import due_stuck_charges
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.collections_support import (
    RECON,
    RECONCILIATION,
    MerchantProviders,
    as_principal,
    checkout,
    connect,
    deliver,
    internal_base,
    operations_token,
    principal_for,
    refund_webhook,
)
from tests.helpers import audit_rows, emitted, rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

CAPTURED = "payment.collections.payment.captured"
REFUNDED = "payment.collections.payment.refunded"


async def _open(client: AsyncClient, org_id: uuid.UUID) -> tuple[str, dict[str, Any], uuid.UUID]:
    account = await connect(client, org_id)
    order_id = uuid.uuid4()
    response = await checkout(client, org_id, order_id)
    assert response.status_code == 200, response.text
    return account["id"], response.json(), order_id


async def _sync(
    client: AsyncClient, org_id: uuid.UUID, order_id: uuid.UUID, charge_id: str, **kw: Any
) -> Any:
    return await client.post(
        f"{internal_base(org_id, order_id)}/charges/{charge_id}/sync",
        headers=operations_token(),
        **kw,
    )


async def _refund(
    client: AsyncClient,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    charge_id: str,
    *,
    amount_minor: int,
    key: str,
) -> Any:
    return await client.post(
        f"{internal_base(org_id, order_id)}/charges/{charge_id}/refunds",
        headers=operations_token(),
        json={
            "amount_minor": amount_minor,
            "idempotency_key": key,
            "reason": "customer returned it",
        },
    )


async def _paid(
    client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
) -> tuple[str, dict[str, Any], uuid.UUID]:
    account_id, opened, order_id = await _open(client, org_id)
    merchant_providers.pay(
        opened["checkout"]["provider_order_id"],
        payment_id="pay_1",
        status="captured",
        amount_minor=50_000,
    )
    synced = await _sync(client, org_id, order_id, opened["collection"]["id"])
    assert synced.status_code == 200 and synced.json()["state"] == "captured", synced.text
    return account_id, opened, order_id


class TestStatusCheck:
    async def test_a_status_check_learns_the_capture_from_the_provider(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        charge_id = opened["collection"]["id"]
        # Nothing at the provider yet: nothing changes, whatever the caller might claim.
        body_claiming_paid = {"state": "captured", "razorpay_payment_id": "pay_forged"}
        unchanged = await _sync(client, org_id, order_id, charge_id, json=body_claiming_paid)
        assert unchanged.status_code == 200 and unchanged.json()["state"] == "created"
        assert await emitted(admin_session, org_id) == []

        merchant_providers.pay(
            opened["checkout"]["provider_order_id"],
            payment_id="pay_1",
            status="captured",
            amount_minor=50_000,
        )
        synced = (await _sync(client, org_id, order_id, charge_id)).json()
        assert synced["state"] == "captured" and synced["provider_payment_id"] == "pay_1"
        assert [h["source"] for h in synced["history"]] == ["api", "sync"]
        assert await emitted(admin_session, org_id) == [CAPTURED]
        # A second check is a no-op: still one event.
        await _sync(client, org_id, order_id, charge_id)
        assert await emitted(admin_session, org_id) == [CAPTURED]

    async def test_a_provider_that_reports_another_amount_changes_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        merchant_providers.pay(
            opened["checkout"]["provider_order_id"],
            payment_id="pay_1",
            status="captured",
            amount_minor=1,
        )
        synced = (await _sync(client, org_id, order_id, opened["collection"]["id"])).json()
        assert synced["state"] == "created"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.collection.status_conflict" in actions


class TestCancel:
    async def test_an_unpaid_checkout_is_cancelled_and_history_says_who(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        actor = uuid.uuid4()
        response = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/cancel",
            headers=operations_token(),
            json={"actor_id": str(actor)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "cancelled"
        assert body["history"][-1]["reason"] == "cancelled_by_operator"
        # A new "collect" after a cancel opens a fresh checkout (a new provider Order).
        reopened = (await checkout(client, org_id, order_id)).json()
        assert reopened["collection"]["id"] != opened["collection"]["id"]
        assert reopened["checkout"]["provider_order_id"] != opened["checkout"]["provider_order_id"]

    async def test_a_checkout_the_customer_already_paid_cannot_be_cancelled(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        merchant_providers.pay(
            opened["checkout"]["provider_order_id"],
            payment_id="pay_1",
            status="captured",
            amount_minor=50_000,
        )
        response = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/cancel",
            headers=operations_token(),
            json={},
        )
        assert response.status_code == 409 and "has paid" in response.text
        # The provider's answer was applied first, so the record now says what really happened.
        state = await rows(
            admin_session,
            "SELECT state FROM payment.charges WHERE id = :id",
            id=uuid.UUID(opened["collection"]["id"]),
        )
        assert state == [{"state": "captured"}]
        assert await emitted(admin_session, org_id) == [CAPTURED]


class TestRefunds:
    async def test_a_full_refund_is_recorded_once_and_announced(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, order_id = await _paid(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        first = await _refund(
            client, org_id, order_id, charge_id, amount_minor=50_000, key="refund-key-0001"
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["refunded_minor"] == 50_000
        assert [r["state"] for r in body["refunds"]] == ["processed"]
        # The same key again: the same refund, nothing new anywhere.
        replay = await _refund(
            client, org_id, order_id, charge_id, amount_minor=50_000, key="refund-key-0001"
        )
        assert replay.status_code == 200 and len(replay.json()["refunds"]) == 1
        refunds = await rows(
            admin_session, "SELECT payee FROM payment.refunds WHERE organization_id = :o", o=org_id
        )
        assert refunds == [{"payee": "merchant"}]
        events = await rows(
            admin_session,
            "SELECT event_type, payload FROM payment.outbox WHERE organization_id = :o "
            "ORDER BY created_at",
            o=org_id,
        )
        assert [e["event_type"] for e in events] == [CAPTURED, REFUNDED]
        assert events[1]["payload"]["fully_refunded"] is True
        assert events[1]["payload"]["refunded_total_minor"] == 50_000

    async def test_more_than_was_paid_is_refused(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, opened, order_id = await _paid(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        assert (
            await _refund(
                client, org_id, order_id, charge_id, amount_minor=30_000, key="refund-key-A"
            )
        ).status_code == 200
        too_much = await _refund(
            client, org_id, order_id, charge_id, amount_minor=30_000, key="refund-key-B"
        )
        assert too_much.status_code == 409

    async def test_an_unpaid_collection_cannot_be_refunded(
        self, client: AsyncClient, merchant_providers: MerchantProviders, org_id: uuid.UUID
    ) -> None:
        _, opened, order_id = await _open(client, org_id)
        response = await _refund(
            client, org_id, order_id, opened["collection"]["id"], amount_minor=1, key="refund-key-C"
        )
        assert response.status_code == 409

    async def test_a_refund_the_provider_later_fails_stops_counting(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, opened, order_id = await _paid(client, merchant_providers, org_id)
        charge_id = opened["collection"]["id"]
        refunded = (
            await _refund(
                client, org_id, order_id, charge_id, amount_minor=50_000, key="refund-key-D"
            )
        ).json()
        provider_refund_id = refunded["refunds"][0]["provider_refund_id"]
        body = refund_webhook(
            event="refund.failed",
            refund_id=provider_refund_id,
            payment_id="pay_1",
            amount_minor=50_000,
        )
        assert (await deliver(client, account_id, body)).json()["reconciled"] is True
        # A redelivery of the same outcome is a no-op.
        assert (await deliver(client, account_id, body)).json()["duplicate"] is True
        listed = (
            await client.get(internal_base(org_id, order_id), headers=operations_token())
        ).json()
        (collection,) = listed["collections"]
        assert collection["refunded_minor"] == 0
        assert [r["state"] for r in collection["refunds"]] == ["failed"]
        last = await rows(
            admin_session,
            "SELECT payload FROM payment.outbox WHERE organization_id = :o AND event_type = :t "
            "ORDER BY created_at DESC LIMIT 1",
            o=org_id,
            t=REFUNDED,
        )
        assert last[0]["payload"]["fully_refunded"] is False
        assert last[0]["payload"]["refunded_total_minor"] == 0

    async def test_a_refund_made_in_the_providers_dashboard_is_recorded(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        account_id, _, order_id = await _paid(client, merchant_providers, org_id)
        body = refund_webhook(
            event="refund.processed",
            refund_id="rfnd_dashboard",
            payment_id="pay_1",
            amount_minor=10_000,
        )
        assert (await deliver(client, account_id, body)).json()["reconciled"] is True
        listed = (
            await client.get(internal_base(org_id, order_id), headers=operations_token())
        ).json()
        (collection,) = listed["collections"]
        assert collection["refunded_minor"] == 10_000
        assert collection["refunds"][0]["reason"] == "provider_initiated"


class TestExpiry:
    async def _age(self, admin: AsyncSession, charge_id: str, days: int = 2) -> None:
        async with admin.begin():
            await admin.execute(
                update(Charge)
                .where(Charge.id == uuid.UUID(charge_id))
                .values(created_at=datetime.now(UTC) - timedelta(days=days))
            )

    def _sweep(self, merchant_providers: MerchantProviders) -> CollectionExpirySweep:
        return CollectionExpirySweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda org: session_scope(organization_id=org),
            provider_factory=lambda: merchant_providers,
            ttl=timedelta(days=1),
        )

    async def test_a_stale_unpaid_checkout_expires_after_asking_the_provider(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, _ = await _open(client, org_id)
        _, fresh, _ = await _open(client, org_id)
        await self._age(admin_session, opened["collection"]["id"])
        report = await self._sweep(merchant_providers).tick()
        assert report.expired == 1 and report.considered == 1
        states = await rows(
            admin_session,
            "SELECT id, state FROM payment.charges WHERE organization_id = :o",
            o=org_id,
        )
        by_id = {str(r["id"]): r["state"] for r in states}
        assert by_id[opened["collection"]["id"]] == "expired"
        assert by_id[fresh["collection"]["id"]] == "created"

    async def test_a_stale_checkout_the_customer_paid_is_captured_not_expired(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, _ = await _open(client, org_id)
        merchant_providers.pay(
            opened["checkout"]["provider_order_id"],
            payment_id="pay_1",
            status="captured",
            amount_minor=50_000,
        )
        await self._age(admin_session, opened["collection"]["id"])
        report = await self._sweep(merchant_providers).tick()
        assert report.expired == 0 and report.reconciled == 1
        assert await emitted(admin_session, org_id) == [CAPTURED]

    async def test_the_platform_stuck_charge_sweep_never_sees_a_collection(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, opened, _ = await _open(client, org_id)
        await self._age(admin_session, opened["collection"]["id"])
        async with get_outbox_sessionmaker()() as bypass, bypass.begin():
            due = await due_stuck_charges(
                bypass, now=datetime.now(UTC), older_than=datetime.now(UTC), limit=100
            )
        assert uuid.UUID(opened["collection"]["id"]) not in {charge for _, charge in due}


class TestReconciliation:
    async def test_the_report_finds_disagreements_and_corrects_nothing(
        self,
        client: AsyncClient,
        merchant_providers: MerchantProviders,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        _, agreed, _ = await _paid(client, merchant_providers, org_id)
        _, missed, _ = await _open(client, org_id)
        # Paid at the provider, never heard about here (a lost webhook).
        merchant_providers.pay(
            missed["checkout"]["provider_order_id"],
            payment_id="pay_9",
            status="captured",
            amount_minor=50_000,
        )
        with as_principal(principal_for(org_id, [RECON])):
            report = (await client.get(RECONCILIATION)).json()
        assert report["checked"] == 2 and report["mismatched"] == 1 and report["unchecked"] == 0
        by_charge = {r["charge_id"]: r for r in report["rows"]}
        assert by_charge[agreed["collection"]["id"]]["mismatches"] == []
        assert by_charge[missed["collection"]["id"]]["mismatches"] == ["captured_at_provider_only"]
        # Read-only: the missed capture is still not recorded until a status check or webhook.
        state = await rows(
            admin_session,
            "SELECT state FROM payment.charges WHERE id = :id",
            id=uuid.UUID(missed["collection"]["id"]),
        )
        assert state == [{"state": "created"}]
