"""Reads for payment. Every scoped query runs under a bound tenant (RLS does the filtering).

payment has no customer list endpoint, so there is no `ctx.apply` here — the reads that exist are
service-internal (the charge a webhook reconciles, the charge a create returns) and are already
constrained to one organization by the transaction's tenant GUC. Each still leads with
`organization_id` so it never leans on RLS alone to avoid a full scan.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MANDATE_ACTIVE, REFUND_FAILED, Charge, Mandate, ProviderEvent, Refund


async def get_charge(session: AsyncSession, charge_id: uuid.UUID) -> Charge | None:
    """One charge by id, under the bound tenant. Another organization's id is invisible (a None)."""
    return await session.get(Charge, charge_id)


async def get_charge_by_idempotency_key(
    session: AsyncSession, organization_id: uuid.UUID, idempotency_key: str
) -> Charge | None:
    """The charge a platform idempotency key already created, if any — the create path's dedupe."""
    result = await session.execute(
        select(Charge).where(
            Charge.organization_id == organization_id,
            Charge.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def get_charge_by_provider_payment_id(
    session: AsyncSession, organization_id: uuid.UUID, provider_payment_id: str
) -> Charge | None:
    """The charge a webhook reconciles against, matched on the provider's payment id."""
    result = await session.execute(
        select(Charge).where(
            Charge.organization_id == organization_id,
            Charge.provider_payment_id == provider_payment_id,
        )
    )
    return result.scalar_one_or_none()


async def get_provider_event(
    session: AsyncSession, organization_id: uuid.UUID, provider_event_id: str
) -> ProviderEvent | None:
    """An inbox row by the provider's event id — the idempotency lookup for a webhook."""
    result = await session.execute(
        select(ProviderEvent).where(
            ProviderEvent.organization_id == organization_id,
            ProviderEvent.provider_event_id == provider_event_id,
        )
    )
    return result.scalar_one_or_none()


async def get_mandate(
    session: AsyncSession, organization_id: uuid.UUID, mandate_id: uuid.UUID
) -> Mandate | None:
    """A mandate by id, explicitly scoped to the organization (the RLS net notwithstanding)."""
    result = await session.execute(
        select(Mandate).where(
            Mandate.organization_id == organization_id,
            Mandate.id == mandate_id,
        )
    )
    return result.scalar_one_or_none()


async def usable_mandate(
    session: AsyncSession, organization_id: uuid.UUID, *, currency: str, amount_minor: int
) -> Mandate | None:
    """A mandate that could authorize a debit of `amount_minor` in `currency` right now.

    The same four conditions `services._require_usable_mandate` enforces on a charge — `active`,
    bank-confirmed (`activated_at` set), the currency, and a cap that covers the amount — asked
    ahead of time, so the billing run can decide whether a trial that just ended is collectable
    (ADR-0013 §6). The most recently activated one wins when several qualify.
    """
    result = await session.execute(
        select(Mandate)
        .where(
            Mandate.organization_id == organization_id,
            Mandate.state == MANDATE_ACTIVE,
            Mandate.activated_at.is_not(None),
            Mandate.currency == currency.upper(),
            Mandate.max_amount_minor >= amount_minor,
        )
        .order_by(Mandate.activated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def refunded_total_for_charge(session: AsyncSession, charge_id: uuid.UUID) -> int:
    """Minor units already refunded against a charge, failed refunds excluded.

    What the over-refund guard compares against `charge.amount_minor`: the sum of every
    refund still standing, so partial refunds accumulate toward the cap.
    """
    result = await session.execute(
        select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
            Refund.charge_id == charge_id,
            Refund.state != REFUND_FAILED,
        )
    )
    return int(result.scalar_one())
