"""Reads for payment. Every scoped query runs under a bound tenant (RLS does the filtering).

payment has no customer list endpoint, so there is no `ctx.apply` here — the reads that exist are
service-internal (the charge a webhook reconciles, the charge a create returns) and are already
constrained to one organization by the transaction's tenant GUC. Each still leads with
`organization_id` so it never leans on RLS alone to avoid a full scan.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CHARGE_CREATED,
    MANDATE_ACTIVE,
    REFUND_FAILED,
    Charge,
    Mandate,
    ProviderEvent,
    Refund,
)


async def get_charge(session: AsyncSession, charge_id: uuid.UUID) -> Charge | None:
    """One charge by id, under the bound tenant. Another organization's id is invisible (a None)."""
    return await session.get(Charge, charge_id)


async def lock_charge(
    session: AsyncSession, charge_id: uuid.UUID, *, skip_locked: bool = False
) -> Charge | None:
    """One charge by id, row-locked, **re-read from the database** (`populate_existing`) rather
    than trusted from the session's identity map — the recording step and the sweep both decide
    from what the row says *now*, which a webhook may have changed since this session last looked.

    `skip_locked` returns None for a row somebody else holds instead of waiting behind them —
    what the sweep wants."""
    stmt = (
        select(Charge)
        .where(Charge.id == charge_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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


#: Exactly the columns `due_stuck_charges` reads — and so exactly what the outbox role is granted
#: on `charges` (`migrations/env.py`): identifiers, the state it filters on and the lease/backoff
#: clocks, never an amount, an idempotency key or a failure reason.
SWEEP_READER_CHARGE_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "id",
    "state",
    "provider_payment_id",
    "provider_order_id",
    "created_at",
    "deleted_at",
    "attempts",
    "next_attempt_at",
    "lease_until",
)


async def due_stuck_charges(
    session: AsyncSession, *, now: datetime, older_than: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every charge staged but never resolved and **actionable now**, as
    `(organization_id, charge_id)`.

    **The one cross-tenant read in this service.** It exists to discover *which* organizations
    have a charge stuck between `stage_charge` and `finalize_charge` — a process that died
    between calling the provider and recording what it said — so there is no tenant to bind while
    asking. Run on the BYPASSRLS outbox role, which holds column-scoped `SELECT` on exactly the
    columns read here (`migrations/env.py`); as the NOBYPASSRLS app role it would return nothing,
    and a charge orphaned by a crash would sit `created` forever.

    Only charges the sweep can act on are returned, so the batch is never filled by ones it
    cannot (a head-of-line block across organizations): not soft-deleted, no call in flight
    (`lease_until` passed), not backing off (`next_attempt_at` passed), and — for rows staged
    before the lease columns existed — older than `older_than`. Charges past their attempt budget
    are returned too: the sweep's next act for them is to fail them.
    """
    stmt = (
        select(Charge.organization_id, Charge.id)
        .where(
            Charge.state == CHARGE_CREATED,
            Charge.provider_payment_id.is_(None),
            Charge.deleted_at.is_(None),
            Charge.created_at <= older_than,
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
            or_(Charge.next_attempt_at.is_(None), Charge.next_attempt_at <= now),
        )
        .order_by(func.coalesce(Charge.next_attempt_at, Charge.created_at))
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


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
