"""Tenant collections through the **real** provider factory (`deps._collections_provider`) — the one
production uses, which builds a merchant's adapter afresh for every call and caches nothing.

`MerchantProviders` (the rest of the collections suite) shares one stub account across calls,
which production never does, so it cannot see two things this file pins:

* On the default stub wire (`COLLECTIONS_WIRE=stub`, every dev box and the edge box until the owner
  says otherwise) the adapter that checks an Order's payments is never the one that minted it. Its
  answer must stay "no payments" — the platform stub's "I cannot answer for an Order I never
  minted" is for the platform's own adapter only — or every sync, cancel and expiry of a collection
  fails with a 502 and an order whose amount changed is wedged for good.
* The live wire raises its transport failures already classified (`ProviderTransient`), so a
  timeout on the Order create must still be "could not be reached" (503) with the call lease
  **kept** — not "refused" (502) with the lease released, which lets a retry race the create.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import deps
from app.collections import services as collection_services
from app.collections.cron import CollectionExpirySweep
from app.main import app
from app.models import Charge
from app.providers.razorpay_collections import (
    RazorpayCollectionsProvider,
    build_collections_provider,
)
from app.providers.razorpay_wire import RazorpayHttpWire
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.collections_support import (
    MERCHANT_KEY_ID,
    MERCHANT_KEY_SECRET,
    checkout,
    connect,
    internal_base,
    operations_token,
)
from tests.helpers import rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]


async def _open(client: AsyncClient, org_id: uuid.UUID) -> tuple[dict[str, Any], uuid.UUID]:
    await connect(client, org_id)
    order_id = uuid.uuid4()
    response = await checkout(client, org_id, order_id)
    assert response.status_code == 200, response.text
    return response.json(), order_id


class TestTheStubWireThroughTheRealFactory:
    async def test_a_status_check_answers_and_changes_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        opened, order_id = await _open(client, org_id)
        response = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/sync",
            headers=operations_token(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "created"

    async def test_an_unpaid_checkout_can_be_cancelled_and_a_new_one_opened(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        opened, order_id = await _open(client, org_id)
        response = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/cancel",
            headers=operations_token(),
            json={},
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "cancelled"
        # The order's total changed: a checkout for the new amount opens.
        again = await checkout(client, org_id, order_id, amount_minor=60_000)
        assert again.status_code == 200, again.text

    async def test_the_expiry_sweep_expires_a_stale_unpaid_checkout(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        opened, _ = await _open(client, org_id)
        charge_id = uuid.UUID(opened["collection"]["id"])
        async with admin_session.begin():
            await admin_session.execute(
                update(Charge)
                .where(Charge.id == charge_id)
                .values(created_at=datetime.now(UTC) - timedelta(days=2))
            )
        sweep = CollectionExpirySweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda org: session_scope(organization_id=org),
            provider_factory=deps.get_collections_provider_factory,
            ttl=timedelta(days=1),
        )

        report = await sweep.tick()

        assert (report.expired, report.failed) == (1, 0)
        state = await rows(
            admin_session, "SELECT state FROM payment.charges WHERE id = :id", id=charge_id
        )
        assert state == [{"state": "expired"}]


def _timing_out_factory(error: Exception) -> Any:
    """A merchant's live Razorpay adapter whose `POST /v1/orders` never answers: the lookup finds
    nothing, the create raises `error` at the transport — exactly what a slow Razorpay does."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/orders":
            return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})
        raise error

    def factory(
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        *,
        provider: str = "razorpay",
        mode: str = "test",
        config: Any = None,
    ) -> RazorpayCollectionsProvider:
        assert provider == "razorpay"
        wire = RazorpayHttpWire(
            key_id=key_id, key_secret=key_secret, transport=httpx.MockTransport(handle)
        )
        return build_collections_provider(
            key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
        )

    return factory


class TestALiveWireThatDoesNotAnswer:
    @pytest.mark.parametrize(
        "error",
        [
            httpx.ReadTimeout("slow"),
            httpx.ConnectTimeout("slow"),
            httpx.ConnectError("down"),
        ],
    )
    async def test_the_order_create_is_unreachable_not_refused(self, error: Exception) -> None:
        ticket = collection_services.ProviderTicket(
            organization_id=uuid.uuid4(),
            charge_id=uuid.uuid4(),
            key_id=MERCHANT_KEY_ID,
            key_secret=MERCHANT_KEY_SECRET,
            webhook_secret="hook-secret-0001",  # pragma: allowlist secret
            amount_minor=50_000,
            currency="INR",
            provider_order_id=None,
        )
        with pytest.raises(collection_services.ProviderUnreachableProblem):
            await collection_services.create_provider_order(_timing_out_factory(error), ticket)

    async def test_the_checkout_route_answers_503_and_keeps_the_call_lease(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        app.dependency_overrides[deps.get_collections_provider_factory] = lambda: (
            _timing_out_factory(httpx.ReadTimeout("slow"))
        )
        order_id = uuid.uuid4()

        response = await checkout(client, org_id, order_id)

        assert response.status_code == 503, response.text
        (row,) = await rows(
            admin_session,
            "SELECT state, lease_until FROM payment.charges WHERE organization_id = :o",
            o=org_id,
        )
        # The create may still land: the lease is held, so a retry cannot race it.
        assert row["state"] == "created" and row["lease_until"] is not None
