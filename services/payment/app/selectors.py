"""Reads for payment. Every scoped query runs under a bound tenant (RLS does the filtering).

payment has no customer list endpoint, so there is no `ctx.apply` here — the reads that exist are
service-internal (the charge a webhook reconciles, the charge a create returns) and are already
constrained to one organization by the transaction's tenant GUC. Each still leads with
`organization_id` so it never leans on RLS alone to avoid a full scan.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CHARGE_CREATED, REFUND_FAILED, Charge, Mandate, ProviderEvent, Refund


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


async def due_stuck_charges(
    session: AsyncSession, *, older_than: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every charge staged but never resolved, as `(organization_id, charge_id)`.

    **The one cross-tenant read in this service, and the only one it needs.** It exists to
    discover *which* organizations have a charge stuck between `stage_charge` and
    `finalize_charge` — a process that died between calling the provider and recording what it
    said — so there is no tenant to bind while asking, exactly the outbox relay's and
    subscription's dunning sweep's shape. Run on the BYPASSRLS outbox role
    (`get_outbox_sessionmaker`); as the NOBYPASSRLS app role it would return nothing, log nothing,
    and a charge orphaned by a crash would sit `created` forever with money either never asked for
    or asked for and never recorded.

    `older_than` is the poisoned-step guard: a charge staged moments ago is very likely still
    between `stage_charge`'s commit and `call_provider_charge`'s return in the very request that
    created it, not actually stuck, so the sweep leaves it alone until it has had time to fail on
    its own.
    """
    stmt = (
        select(Charge.organization_id, Charge.id)
        .where(
            Charge.state == CHARGE_CREATED,
            Charge.provider_payment_id.is_(None),
            Charge.created_at <= older_than,
        )
        .order_by(Charge.created_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


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
