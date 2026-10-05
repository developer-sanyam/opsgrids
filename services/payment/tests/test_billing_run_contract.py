"""payment's two contracts with the billing run (ADR-0005 addendum, 2026-09-23).

1. **The invoice a billing run finalizes is charged exactly once.** The consumer is driven with the
   envelope billing's `record_subscription_invoice` stages, field for field: redelivery of the same
   event is one charge, a second event for the same invoice is still one charge (the key is the
   invoice), and a zero invoice raises no debit at all.
2. **The trial-end mandate question** — `GET /internal/v1/organizations/{org}/mandates:usable`
   answers from the same four conditions the charge guard enforces, only to subscription, and
   never about another organization's mandates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.consumers import charge_on_invoice_finalized
from app.models import MANDATE_ACTIVE, MANDATE_PENDING, Charge, Mandate
from platform_core.db import dispose_engine, init_engine
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    init_engine(settings())
    yield
    await dispose_engine()


def _finalized(
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    total_minor: int = 149900,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The envelope billing stages for a billing-run invoice."""
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "commerce.invoice.finalized",
        "organization_id": str(org_id),
        "payload": {
            "invoice_id": str(invoice_id),
            "organization_id": str(org_id),
            "subscription_id": str(uuid.UUID("99999999-8888-7777-6666-555555555555")),
            "number": "INV-20261001-ABCDEF012345",
            "currency": "INR",
            "total_minor": total_minor,
            "period_start": "2026-10-01T00:00:00+00:00",
            "period_end": "2026-10-31T00:00:00+00:00",
            "product_key": "hrms",
            "source": "billing_run",
        },
    }


async def _charges(session: AsyncSession) -> list[Charge]:
    session.expunge_all()
    async with session.begin():
        return list((await session.execute(select(Charge))).scalars().all())


class TestTheRunInvoiceIsChargedOnce:
    async def test_a_finalized_run_invoice_raises_one_charge(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id = uuid.uuid4()
        event = _finalized(org_id, invoice_id)
        await charge_on_invoice_finalized(event)
        await charge_on_invoice_finalized(event)  # redelivered
        await charge_on_invoice_finalized(_finalized(org_id, invoice_id))  # a second event

        (charge,) = await _charges(session)
        assert charge.invoice_id == invoice_id
        assert charge.amount_minor == 149900
        assert charge.currency == "INR"
        assert charge.product_key == "hrms"
        assert charge.idempotency_key == f"invoice:{invoice_id}"
        assert charge.provider_payment_id is not None

    async def test_a_zero_invoice_raises_no_debit(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await charge_on_invoice_finalized(_finalized(org_id, uuid.uuid4(), total_minor=0))
        assert await _charges(session) == []


# --------------------------------------------------------------------- the mandate question


def _headers(caller: str = "subscription", audience: str = "payment") -> dict[str, str]:
    return {"X-Internal-Token": mint_service_token(settings(), audience=audience, caller=caller)}


async def _mandate(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    state: str = MANDATE_ACTIVE,
    cap: int = 500000,
    currency: str = "INR",
    activated: bool = True,
) -> uuid.UUID:
    async with session.begin():
        mandate = Mandate(
            organization_id=org_id,
            provider="razorpay",
            provider_mandate_id=f"mandate_{uuid.uuid4().hex[:14]}",
            state=state,
            max_amount_minor=cap,
            currency=currency,
            activated_at=datetime.now(UTC) if activated else None,
        )
        session.add(mandate)
        await session.flush()
        return mandate.id


def _url(org_id: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org_id}/mandates:usable"


class TestTheMandateQuestion:
    async def test_an_active_mandate_that_covers_the_amount_is_usable(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        mandate_id = await _mandate(session, org_id)
        r = await client.get(
            _url(org_id), params={"currency": "INR", "amount_minor": 149900}, headers=_headers()
        )
        assert r.status_code == 200, r.text
        assert r.json()["usable"] is True
        assert r.json()["mandate_id"] == str(mandate_id)

    @pytest.mark.parametrize(
        ("kwargs", "amount"),
        [
            ({"state": MANDATE_PENDING, "activated": False}, 1000),  # the bank has not confirmed
            ({"cap": 1000}, 1001),  # over the cap: the customer must authenticate by hand
            ({"currency": "USD"}, 1000),  # another currency
        ],
    )
    async def test_a_mandate_that_could_not_debit_is_not_usable(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
        kwargs: dict[str, Any],
        amount: int,
    ) -> None:
        await _mandate(session, org_id, **kwargs)
        r = await client.get(
            _url(org_id), params={"currency": "INR", "amount_minor": amount}, headers=_headers()
        )
        assert r.status_code == 200
        assert r.json() == {
            "organization_id": str(org_id),
            "currency": "INR",
            "amount_minor": amount,
            "usable": False,
            "mandate_id": None,
            # Additive (ADR-0029): the provider the question was asked of — the configured default.
            "provider": "razorpay",
        }

    async def test_another_organizations_mandate_is_invisible(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        async with admin_session.begin():
            admin_session.add(
                Mandate(
                    organization_id=org_b,
                    provider="razorpay",
                    state=MANDATE_ACTIVE,
                    max_amount_minor=10**9,
                    currency="INR",
                    activated_at=datetime.now(UTC),
                )
            )
        r = await client.get(
            _url(org_id), params={"currency": "INR", "amount_minor": 1}, headers=_headers()
        )
        assert r.status_code == 200
        assert r.json()["usable"] is False

    async def test_no_token_is_401(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        r = await client.get(_url(org_id), params={"currency": "INR", "amount_minor": 1})
        assert r.status_code == 401

    async def test_a_token_for_another_service_is_401(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        r = await client.get(
            _url(org_id),
            params={"currency": "INR", "amount_minor": 1},
            headers=_headers(audience="billing"),
        )
        assert r.status_code == 401

    async def test_a_caller_other_than_subscription_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        r = await client.get(
            _url(org_id),
            params={"currency": "INR", "amount_minor": 1},
            headers=_headers(caller="billing"),
        )
        assert r.status_code == 403
