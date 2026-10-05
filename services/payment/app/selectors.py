"""Reads for payment. Every scoped query runs under a bound tenant (RLS does the filtering).

payment has no customer list endpoint, so there is no `ctx.apply` here — the reads that exist are
service-internal (the charge a webhook reconciles, the charge a create returns) and are already
constrained to one organization by the transaction's tenant GUC. **Every by-id read also takes the
organization and filters on it**: RLS is the net, not the filter (CLAUDE.md), and the webhook path
takes its organization from an external body — so a charge, a refund total or a lock is looked up
*within* an organization, never by primary key alone, and a test runs the path on a BYPASSRLS
session to prove the predicate is in the query.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
    FLOW_CHECKOUT,
    FLOW_DEBIT,
    MANDATE_ACTIVE,
    PAYEE_PLATFORM,
    REFUND_FAILED,
    Charge,
    Mandate,
    ProviderEvent,
    Refund,
)


async def get_charge(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> Charge | None:
    """One charge by id **within one organization**. Another organization's id is a None.

    The organization is a predicate of the query, not left to row-level security: RLS is the net
    (CLAUDE.md), and on the webhook path the organization comes from an external body — the query
    itself must refuse a charge that is not that organization's, on a BYPASSRLS session as on the
    app role."""
    result = await session.execute(
        select(Charge).where(Charge.organization_id == organization_id, Charge.id == charge_id)
    )
    return result.scalar_one_or_none()


async def lock_charge(
    session: AsyncSession,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    skip_locked: bool = False,
) -> Charge | None:
    """One charge by id within one organization, row-locked, **re-read from the database**
    (`populate_existing`) rather than trusted from the session's identity map — the recording step
    and the sweep both decide from what the row says *now*, which a webhook may have changed since
    this session last looked.

    `organization_id` is required and filters the query (see `get_charge`): a webhook names its
    tenant in a body, so a charge id from another organization must be a None here whatever the
    session's role can see.

    `skip_locked` returns None for a row somebody else holds instead of waiting behind them —
    what the sweep wants."""
    stmt = (
        select(Charge)
        .where(Charge.organization_id == organization_id, Charge.id == charge_id)
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
    session: AsyncSession,
    organization_id: uuid.UUID,
    provider_payment_id: str,
    *,
    payee: str = PAYEE_PLATFORM,
) -> Charge | None:
    """The charge a webhook reconciles against, matched on the provider's payment id — within one
    payee. The platform webhook (signed with OpsGrids' own secret) must never find a tenant's
    collection charge, and the reverse (ADR-0024 §8)."""
    result = await session.execute(
        select(Charge).where(
            Charge.organization_id == organization_id,
            Charge.provider_payment_id == provider_payment_id,
            Charge.payee == payee,
        )
    )
    return result.scalar_one_or_none()


async def captured_charges_for_invoice(
    session: AsyncSession, organization_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[Charge]:
    """Every charge already `captured` for `invoice_id`, within `organization_id`.

    Two callers, one question — "has this invoice already been collected?" — asked at two moments:
    a webhook that just moved a charge to `captured` checks whether *another* charge of the
    invoice is captured too (a double collection, whichever order the two captures arrived in);
    a dunning retry about to raise a new charge checks *before* staging it, so a settled invoice is
    never debited again. Leads with `organization_id` (`ix_charges_org_invoice`), never RLS alone.
    """
    result = await session.execute(
        select(Charge).where(
            Charge.organization_id == organization_id,
            Charge.invoice_id == invoice_id,
            Charge.state == CHARGE_CAPTURED,
            Charge.payee == PAYEE_PLATFORM,
        )
    )
    return list(result.scalars())


async def get_provider_event(
    session: AsyncSession,
    organization_id: uuid.UUID,
    provider_event_id: str,
    *,
    provider: str,
) -> ProviderEvent | None:
    """An inbox row by the provider's event id — the idempotency lookup for a webhook. The key is
    `(organization, provider, event id)`: an event id is only unique within the provider that
    issued it (ADR-0029)."""
    result = await session.execute(
        select(ProviderEvent).where(
            ProviderEvent.organization_id == organization_id,
            ProviderEvent.provider == provider,
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
    # Both sweeps filter on it: the stuck-charge sweep reads platform charges only, the
    # collection expiry sweep (`app/collections/cron.py`) merchant ones only.
    "payee",
    # The stuck-charge sweep resumes `debit` charges only; the poller (`due_pollable_charges`) reads
    # `checkout` ones and authorized debits (ADR-0029).
    "flow",
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

    **Platform charges only.** A merchant (collection) charge is also `created` with no provider
    payment until its customer pays — by design, not because a call was lost — and resuming it
    here would call *the platform's* provider to debit somebody for a tenant's order, then fail
    it into `commerce.payment.failed` and the subscription dunning ladder (ADR-0024 §8).
    """
    stmt = (
        select(Charge.organization_id, Charge.id)
        .where(
            Charge.payee == PAYEE_PLATFORM,
            # Never a checkout: that charge is `created` with no provider payment until the
            # customer pays, and "resuming" it would fail it as `attempts_exhausted` and suspend a
            # customer who has not yet paid (ADR-0029).
            Charge.flow == FLOW_DEBIT,
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
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    currency: str,
    amount_minor: int,
    provider: str | None = None,
) -> Mandate | None:
    """A mandate that could authorize a debit of `amount_minor` in `currency` right now.

    The same four conditions `services._require_usable_mandate` enforces on a charge — `active`,
    bank-confirmed (`activated_at` set), the currency, and a cap that covers the amount — asked
    ahead of time, so the billing run can decide whether a trial that just ended is collectable
    (ADR-0013 §6). The most recently activated one wins when several qualify.

    `provider` restricts the answer to mandates held at that provider: a Razorpay token cannot be
    debited through Cashfree, so once the default provider is Cashfree a Razorpay mandate does not
    count and the customer authorises afresh (ADR-0029). None keeps the older, unfiltered reading.
    """
    stmt = (
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
    if provider is not None:
        stmt = stmt.where(Mandate.provider == provider)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def refunded_total_for_charge(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> int:
    """Minor units already refunded against a charge, failed refunds excluded.

    What the over-refund guard compares against `charge.amount_minor`: the sum of every
    refund still standing, so partial refunds accumulate toward the cap.
    """
    result = await session.execute(
        select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
            Refund.organization_id == organization_id,
            Refund.charge_id == charge_id,
            Refund.state != REFUND_FAILED,
        )
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------- checkout (ADR-0029)


async def platform_charges_for_invoice(
    session: AsyncSession, organization_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[Charge]:
    """Every platform charge of one invoice **within one organization**, oldest first — what a
    "pay now" reads its amount from (the invoice's own `debit`-flow charge, raised when billing
    finalized it), never the request body. Another organization's invoice is an empty list."""
    result = await session.execute(
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.invoice_id == invoice_id,
            Charge.payee == PAYEE_PLATFORM,
            Charge.deleted_at.is_(None),
        )
        .order_by(Charge.created_at, Charge.id)
    )
    return list(result.scalars())


async def get_checkout_charge(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> Charge | None:
    """One checkout charge by id within one organization, or None — for a charge that is another
    organization's, is not a checkout, or does not exist alike (a 404, never a disclosure)."""
    result = await session.execute(
        select(Charge).where(
            Charge.organization_id == organization_id,
            Charge.id == charge_id,
            Charge.payee == PAYEE_PLATFORM,
            Charge.flow == FLOW_CHECKOUT,
        )
    )
    return result.scalar_one_or_none()


async def lock_checkout_charge(
    session: AsyncSession,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    skip_locked: bool = False,
) -> Charge | None:
    """`get_checkout_charge`, row-locked and re-read from the database (see `lock_charge`)."""
    stmt = (
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.id == charge_id,
            Charge.payee == PAYEE_PLATFORM,
            Charge.flow == FLOW_CHECKOUT,
        )
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def due_pollable_charges(
    session: AsyncSession, *, now: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every platform charge that has a provider Order and no terminal state, and is **due to be
    asked about now**, as `(organization_id, charge_id)` — the poller's discovery (ADR-0029).

    Two kinds, both of which nothing else will ever resolve while the webhook is private:

    * a **checkout** the customer may have paid (`flow = 'checkout'`, `created` or `authorized`);
    * a **debit** the provider authorised and has not yet reported captured (`flow = 'debit'`,
      `authorized`). The stuck-charge sweep never looks at an `authorized` charge;
    * a **closed checkout** that may still be paid (`flow = 'checkout'`, `failed` with no payment,
      and a `next_attempt_at` still set): `expire_checkout_charge` leaves it set, and
      `checkout.services.claim_poll` clears it once the late-capture window is over — which is what
      retires it from here. The window itself is judged there, on the app role: this role is not
      granted `checkout_expires_at` or `failure_reason`, and does not need them.

    A `debit` charge that is merely `created` with an Order is the stuck-charge sweep's, not
    this: its payment may never have been made, and asking about it is the sweep's lookup, with
    its lease and its budget.

    **The second cross-tenant read in this service**: run on the BYPASSRLS outbox role, through
    the same column-scoped grant as `due_stuck_charges` (`migrations/env.py`) — every column read
    here is in `SWEEP_READER_CHARGE_COLUMNS`.
    """
    stmt = (
        select(Charge.organization_id, Charge.id)
        .where(
            Charge.payee == PAYEE_PLATFORM,
            Charge.deleted_at.is_(None),
            Charge.provider_order_id.is_not(None),
            or_(
                (Charge.flow == FLOW_CHECKOUT)
                & Charge.state.in_((CHARGE_CREATED, CHARGE_AUTHORIZED)),
                (Charge.flow == FLOW_DEBIT) & (Charge.state == CHARGE_AUTHORIZED),
                (Charge.flow == FLOW_CHECKOUT)
                & (Charge.state == CHARGE_FAILED)
                & Charge.provider_payment_id.is_(None)
                & Charge.next_attempt_at.is_not(None),
            ),
            or_(Charge.next_attempt_at.is_(None), Charge.next_attempt_at <= now),
        )
        .order_by(func.coalesce(Charge.next_attempt_at, Charge.created_at))
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]
