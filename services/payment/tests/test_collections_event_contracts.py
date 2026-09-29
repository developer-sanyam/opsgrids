"""The tenant-collection events, as the code actually stages them, match the contracts committed
for them in `libs/contracts/events/` — the operations consumer is written against those."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.events import validate_event_schema
from tests.collections_support import (
    MerchantProviders,
    checkout,
    connect,
    deliver,
    internal_base,
    operations_token,
    payment_webhook,
)
from tests.helpers import rows

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("engines")]

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


async def test_every_collection_event_matches_its_contract(
    client: AsyncClient,
    merchant_providers: MerchantProviders,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    account = await connect(client, org_id)
    order_id = uuid.uuid4()
    opened = (await checkout(client, org_id, order_id)).json()
    provider_order = opened["checkout"]["provider_order_id"]
    await deliver(
        client,
        account["id"],
        payment_webhook(
            event="payment.failed",
            payment_id="pay_1",
            order_id=provider_order,
            error_code="BAD_REQUEST_ERROR",
        ),
    )
    await deliver(
        client,
        account["id"],
        payment_webhook(event="payment.captured", payment_id="pay_2", order_id=provider_order),
    )
    refunded = await client.post(
        f"{internal_base(org_id, order_id)}/charges/{opened['collection']['id']}/refunds",
        headers=operations_token(),
        json={"amount_minor": 20_000, "idempotency_key": "contract-refund-1"},
    )
    assert refunded.status_code == 200, refunded.text

    staged = await rows(
        admin_session,
        "SELECT event_type, event_version, payload FROM payment.outbox "
        "WHERE organization_id = :o ORDER BY created_at",
        o=org_id,
    )
    assert [e["event_type"] for e in staged] == [
        "payment.collections.payment.failed",
        "payment.collections.payment.captured",
        "payment.collections.payment.refunded",
    ]
    for event in staged:
        validate_event_schema(
            event["event_type"],
            event["event_version"],
            event["payload"],
            contracts_root=CONTRACTS_ROOT,
        )
