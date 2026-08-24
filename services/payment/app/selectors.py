"""Reads for payment. Every scoped query runs under a bound tenant (RLS does the filtering).

payment has no customer list endpoint, so there is no `ctx.apply` here — the reads that exist are
service-internal (the charge a webhook reconciles, the charge a create returns) and are already
constrained to one organization by the transaction's tenant GUC. Each still leads with
`organization_id` so it never leans on RLS alone to avoid a full scan.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Charge, ProviderEvent


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
