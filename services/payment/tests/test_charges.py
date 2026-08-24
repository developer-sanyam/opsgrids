"""The internal charge endpoint — mapping, idempotency, and the service-token guard.

The internal API reads and writes across tenants with no user principal, guarded only by a
short-lived service token addressed to payment. So two things are proven: the adapter's stubbed
response lands as a real `charges` row, and a retried create is exactly one charge — a duplicate
charge is a duplicate debit.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Charge
from tests.helpers import service_token

pytestmark = pytest.mark.integration

CHARGES = "/internal/v1/charges"


def _auth() -> dict[str, str]:
    return {"X-Internal-Token": service_token()}


def _payload(org_id: uuid.UUID, *, key: str, **over: object) -> dict:
    body: dict = {
        "organization_id": str(org_id),
        "amount_minor": 1000,
        "currency": "USD",
        "idempotency_key": key,
    }
    body.update(over)
    return body


class TestChargeMapping:
    async def test_a_stubbed_response_becomes_a_charge_row(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The adapter maps the (stubbed, no-network) provider response onto a persisted charge:
        a `provider_payment_id` is assigned and the state is `authorized`, awaiting the webhook."""
        response = await client.post(
            CHARGES, headers=_auth(), json=_payload(org_id, key="chg-1", amount_minor=2500)
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == "authorized"
        assert body["provider"] == "razorpay"
        assert body["provider_payment_id"].startswith("pay_")
        assert body["amount_minor"] == 2500

        async with session.begin():
            stored = (
                await session.execute(select(Charge).where(Charge.idempotency_key == "chg-1"))
            ).scalar_one()
        assert stored.provider_payment_id == body["provider_payment_id"]


class TestIdempotency:
    async def test_the_same_key_raises_exactly_one_charge(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A retried billing run must not raise a second payment. Same key, even a different
        amount, is the same one charge."""
        first = await client.post(CHARGES, headers=_auth(), json=_payload(org_id, key="dup"))
        second = await client.post(
            CHARGES, headers=_auth(), json=_payload(org_id, key="dup", amount_minor=999999)
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"], "a retry created a second charge"
        assert second.json()["amount_minor"] == 1000, (
            "the first write wins; the retry does not overwrite"
        )

        async with session.begin():
            count = (
                await session.execute(
                    select(func.count()).select_from(Charge).where(Charge.idempotency_key == "dup")
                )
            ).scalar_one()
        assert count == 1


class TestServiceTokenGuard:
    async def test_a_charge_without_a_service_token_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Negative authz: no token, no charge. The internal API is not reachable without proof of
        a sibling service."""
        response = await client.post(CHARGES, json=_payload(org_id, key="no-token"))
        assert response.status_code == 401

    async def test_a_token_for_another_service_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """A token minted for a different audience must not open payment's internal API — the
        audience is exactly what stops a token for one service being replayed against another."""
        from app.config import settings
        from platform_core.internal_auth import mint_service_token

        wrong = mint_service_token(settings(), audience="billing", caller="billing")
        response = await client.post(
            CHARGES, headers={"X-Internal-Token": wrong}, json=_payload(org_id, key="wrong-aud")
        )
        assert response.status_code == 401
