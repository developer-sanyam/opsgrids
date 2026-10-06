"""Reads for tenant collections. Every tenant read filters on the organization in the query —
row-level security is the net, not the filter (CLAUDE.md) — and on `payee = 'merchant'`, so a
collection read can never return a platform charge, and the platform's reads (`app/selectors.py`)
never return a collection.

Two reads are **not** tenant-scoped, on purpose, and run on the BYPASSRLS outbox role with a
column-scoped grant (`migrations/env.py`), never the app role:

* `merchant_account_org` — the merchant webhook's pre-tenant lookup (ADR-0024 §8): a callback
  names its merchant account in its URL, and that account's organization has to be known before
  its webhook secret can be read to verify the body. It reads `id`, `organization_id` and
  `deleted_at` and nothing else: the role cannot select a sealed secret even by mistake. ADR-0002's
  "bypass for the lookup only, then narrow" — every read after it runs bound to the tenant.
* `due_expiring_collections` — the expiry sweep's discovery, on the same column grant the
  stuck-charge sweep already reads `charges` through.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collections import domain
from app.models import (
    CHARGE_CANCELLED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_EXPIRED,
    CHARGE_FAILED,
    ORDER_WIRE_STUB,
    PAYEE_MERCHANT,
    REFUND_CREATED,
    REFUND_FAILED,
    Charge,
    MerchantAccount,
    ProviderEvent,
    Refund,
    StatusHistory,
)
from app.schemas import CollectionOut, CollectionRefundOut, StatusHistoryOut

#: Exactly the `merchant_accounts` columns the webhook's pre-tenant lookup reads, and so exactly
#: what the outbox role is granted on that table — never a secret, a key id or a mode.
MERCHANT_ACCOUNT_LOOKUP_COLUMNS: tuple[str, ...] = ("id", "organization_id", "deleted_at")


async def merchant_account_org(
    bypass_session: AsyncSession, account_id: uuid.UUID
) -> uuid.UUID | None:
    """The organization a live merchant account belongs to — **on the bypass session**, before
    any tenant is bound. None for an unknown or removed account."""
    stmt = select(MerchantAccount.organization_id).where(
        MerchantAccount.id == account_id, MerchantAccount.deleted_at.is_(None)
    )
    return (await bypass_session.execute(stmt)).scalar_one_or_none()


async def live_merchant_account(
    session: AsyncSession, organization_id: uuid.UUID
) -> MerchantAccount | None:
    """The organization's live (not removed) merchant account, if one is connected."""
    stmt = select(MerchantAccount).where(
        MerchantAccount.organization_id == organization_id,
        MerchantAccount.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_live_merchant_account(
    session: AsyncSession, organization_id: uuid.UUID
) -> MerchantAccount | None:
    stmt = (
        select(MerchantAccount)
        .where(
            MerchantAccount.organization_id == organization_id,
            MerchantAccount.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def merchant_account_by_id(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID
) -> MerchantAccount | None:
    """A live merchant account by id, within one organization — the webhook's second read,
    bound to the tenant the bypass lookup named."""
    stmt = select(MerchantAccount).where(
        MerchantAccount.organization_id == organization_id,
        MerchantAccount.id == account_id,
        MerchantAccount.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_open_collections(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID
) -> int:
    """How many collections taken into this account a customer could still pay — `created`,
    `authorized` or `failed` (a failed attempt's Order takes a retry)."""
    stmt = select(func.count()).where(
        Charge.organization_id == organization_id,
        Charge.payee == PAYEE_MERCHANT,
        Charge.merchant_account_id == account_id,
        Charge.state.in_(("created", "authorized", "failed")),
    )
    return int((await session.execute(stmt)).scalar_one())


@dataclass(frozen=True, slots=True)
class DisconnectBlockers:
    """What a merchant account could still be told about by its provider — money that may still
    move — and so what removing its keys would leave unrecorded (ADR-0033 §8)."""

    #: `created`, `authorized` or `failed` collections: a customer can still pay them.
    open_collections: int = 0
    #: Collections this platform cancelled or expired whose provider Order can still take a
    #: payment (`domain.payable_until`), and the latest moment one of them can.
    closed_payable: int = 0
    payable_until: datetime | None = None
    #: Refunds still pending at the provider: only its webhook says whether the customer got the
    #: money back, and a failed one gives the order its paid fact back.
    pending_refunds: int = 0

    def __bool__(self) -> bool:
        return bool(self.open_collections or self.closed_payable or self.pending_refunds)


async def disconnect_blockers(
    session: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    provider: str,
    now: datetime,
    checkout_ttl: timedelta,
) -> DisconnectBlockers:
    """Everything that blocks disconnecting this account. A collection whose Order **the stub**
    made (`order_wire = stub`) can never be paid or refunded for real, so once closed it blocks
    nothing; an open one still counts until it is cancelled (which, for a stub Order, needs no
    provider). A collection recorded before the wire was kept counts as possibly real."""
    open_count = await count_open_collections(session, organization_id, account_id)
    spec = domain.spec_for(provider)
    real = or_(Charge.order_wire.is_(None), Charge.order_wire != ORDER_WIRE_STUB)
    horizon = now - max(checkout_ttl, domain.LATE_PAYMENT_GRACE)
    closed = (
        await session.execute(
            select(Charge.created_at, Charge.updated_at).where(
                Charge.organization_id == organization_id,
                Charge.payee == PAYEE_MERCHANT,
                Charge.merchant_account_id == account_id,
                Charge.state.in_((CHARGE_CANCELLED, CHARGE_EXPIRED)),
                Charge.provider_order_id.is_not(None),
                real,
                or_(Charge.created_at > horizon, Charge.updated_at > horizon),
            )
        )
    ).all()
    payable = [
        until
        for until in (
            domain.payable_until(
                spec, created_at=created_at, closed_at=closed_at, checkout_ttl=checkout_ttl
            )
            for created_at, closed_at in closed
        )
        if until > now
    ]
    pending = list(
        (
            await session.execute(
                select(Refund).where(
                    Refund.organization_id == organization_id,
                    Refund.payee == PAYEE_MERCHANT,
                    Refund.state == REFUND_CREATED,
                    Refund.charge_id.in_(
                        select(Charge.id).where(
                            Charge.organization_id == organization_id,
                            Charge.payee == PAYEE_MERCHANT,
                            Charge.merchant_account_id == account_id,
                            real,
                        )
                    ),
                )
            )
        ).scalars()
    )
    states = await effective_refund_states(session, organization_id, pending)
    return DisconnectBlockers(
        open_collections=open_count,
        closed_payable=len(payable),
        payable_until=max(payable) if payable else None,
        pending_refunds=sum(1 for refund in pending if states[refund.id] == REFUND_CREATED),
    )


async def last_webhook_at(
    session: AsyncSession, organization_id: uuid.UUID, account: MerchantAccount
) -> datetime | None:
    """When the inbox last recorded a delivery from this account's provider for this organization
    since the account was connected — "is my webhook reaching you?", derived, never stored."""
    stmt = select(func.max(ProviderEvent.received_at)).where(
        ProviderEvent.organization_id == organization_id,
        ProviderEvent.payee == PAYEE_MERCHANT,
        ProviderEvent.provider == account.provider,
        ProviderEvent.received_at >= account.created_at,
    )
    found: datetime | None = (await session.execute(stmt)).scalar_one_or_none()
    return found


async def lock_collection(
    session: AsyncSession,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    skip_locked: bool = False,
) -> Charge | None:
    """One collection, row-locked and re-read (`populate_existing`) — every writer decides from
    what the row says now, which a webhook may have changed since this session last looked."""
    stmt = (
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.id == charge_id,
            Charge.payee == PAYEE_MERCHANT,
        )
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_collection(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> Charge | None:
    stmt = select(Charge).where(
        Charge.organization_id == organization_id,
        Charge.id == charge_id,
        Charge.payee == PAYEE_MERCHANT,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def collections_for_reference(
    session: AsyncSession, organization_id: uuid.UUID, reference_type: str, reference_id: uuid.UUID
) -> list[Charge]:
    """Every collection taken for one thing (one order), newest first."""
    stmt = (
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.payee == PAYEE_MERCHANT,
            Charge.reference_type == reference_type,
            Charge.reference_id == reference_id,
        )
        .order_by(Charge.created_at.desc(), Charge.id)
    )
    return list((await session.execute(stmt)).scalars())


async def lock_open_collection(
    session: AsyncSession, organization_id: uuid.UUID, reference_type: str, reference_id: uuid.UUID
) -> Charge | None:
    """The one open collection (`created`/`authorized`/`failed`) for a reference, row-locked —
    at most one exists (`uq_charges_open_collection`)."""
    stmt = (
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.payee == PAYEE_MERCHANT,
            Charge.reference_type == reference_type,
            Charge.reference_id == reference_id,
            Charge.state.in_(("created", "authorized", "failed")),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def collection_by_provider_order(
    session: AsyncSession, organization_id: uuid.UUID, provider_order_id: str
) -> Charge | None:
    """The collection a webhook is about, by the provider Order this platform created for it —
    the one identifier on a payment entity no browser can choose (Checkout `notes` can be)."""
    stmt = select(Charge.id).where(
        Charge.organization_id == organization_id,
        Charge.payee == PAYEE_MERCHANT,
        Charge.provider_order_id == provider_order_id,
    )
    found = (await session.execute(stmt)).scalar_one_or_none()
    return await lock_collection(session, organization_id, found) if found else None


async def collection_by_provider_payment(
    session: AsyncSession, organization_id: uuid.UUID, provider_payment_id: str
) -> Charge | None:
    stmt = select(Charge.id).where(
        Charge.organization_id == organization_id,
        Charge.payee == PAYEE_MERCHANT,
        Charge.provider_payment_id == provider_payment_id,
    )
    found = (await session.execute(stmt)).scalar_one_or_none()
    return await lock_collection(session, organization_id, found) if found else None


async def refunds_for_collections(
    session: AsyncSession, organization_id: uuid.UUID, charge_ids: Sequence[uuid.UUID]
) -> list[Refund]:
    if not charge_ids:
        return []
    stmt = (
        select(Refund)
        .where(
            Refund.organization_id == organization_id,
            Refund.payee == PAYEE_MERCHANT,
            Refund.charge_id.in_(list(charge_ids)),
        )
        .order_by(Refund.created_at, Refund.id)
    )
    return list((await session.execute(stmt)).scalars())


async def refund_by_provider_id(
    session: AsyncSession, organization_id: uuid.UUID, provider_refund_id: str
) -> Refund | None:
    stmt = select(Refund).where(
        Refund.organization_id == organization_id,
        Refund.payee == PAYEE_MERCHANT,
        Refund.provider_refund_id == provider_refund_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def refund_by_idempotency_key(
    session: AsyncSession, organization_id: uuid.UUID, idempotency_key: str
) -> Refund | None:
    stmt = select(Refund).where(
        Refund.organization_id == organization_id,
        Refund.idempotency_key == idempotency_key,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def effective_refund_states(
    session: AsyncSession, organization_id: uuid.UUID, refunds: Sequence[Refund]
) -> dict[uuid.UUID, str]:
    """Each refund's **current** state: the latest `status_history` row for it, else the state
    it was recorded in. `refunds` is append-only, so a provider's later outcome lives in the
    history, never as an edit to the row."""
    states = {refund.id: refund.state for refund in refunds}
    if not refunds:
        return states
    latest = (
        select(
            StatusHistory.entity_id,
            StatusHistory.to_state,
            func.row_number()
            .over(
                partition_by=StatusHistory.entity_id,
                order_by=(StatusHistory.at.desc(), StatusHistory.created_at.desc()),
            )
            .label("rank"),
        )
        .where(
            StatusHistory.organization_id == organization_id,
            StatusHistory.entity == "refund",
            StatusHistory.entity_id.in_(list(states)),
        )
        .subquery()
    )
    rows = await session.execute(
        select(latest.c.entity_id, latest.c.to_state).where(latest.c.rank == 1)
    )
    for entity_id, to_state in rows.all():
        states[entity_id] = to_state
    return states


async def refunded_total(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> int:
    """Minor units refunded against one collection that still stand — every refund whose
    *effective* state is not `failed`."""
    refunds = await refunds_for_collections(session, organization_id, [charge_id])
    states = await effective_refund_states(session, organization_id, refunds)
    return sum(r.amount_minor for r in refunds if states[r.id] != REFUND_FAILED)


async def history_for(
    session: AsyncSession, organization_id: uuid.UUID, entity_ids: Sequence[uuid.UUID]
) -> list[StatusHistory]:
    if not entity_ids:
        return []
    stmt = (
        select(StatusHistory)
        .where(
            StatusHistory.organization_id == organization_id,
            StatusHistory.entity_id.in_(list(entity_ids)),
        )
        .order_by(StatusHistory.at, StatusHistory.created_at)
    )
    return list((await session.execute(stmt)).scalars())


async def captured_collections_for_reference(
    session: AsyncSession, organization_id: uuid.UUID, reference_type: str, reference_id: uuid.UUID
) -> list[Charge]:
    stmt = select(Charge).where(
        Charge.organization_id == organization_id,
        Charge.payee == PAYEE_MERCHANT,
        Charge.reference_type == reference_type,
        Charge.reference_id == reference_id,
        Charge.state == CHARGE_CAPTURED,
    )
    return list((await session.execute(stmt)).scalars())


async def recent_collections_at_provider(
    session: AsyncSession, organization_id: uuid.UUID, *, limit: int
) -> list[Charge]:
    """The most recent collections that reached the provider (they have an Order there) — what a
    reconciliation report asks the provider about."""
    stmt = (
        select(Charge)
        .where(
            Charge.organization_id == organization_id,
            Charge.payee == PAYEE_MERCHANT,
            Charge.provider_order_id.is_not(None),
        )
        .order_by(Charge.created_at.desc(), Charge.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def collection_views(
    session: AsyncSession, organization_id: uuid.UUID, charges: Sequence[Charge]
) -> list[CollectionOut]:
    """Collections as the API shows them — each with its refunds (at their *current* state) and
    its status history — in three queries for the whole list, not three per collection."""
    ids = [c.id for c in charges]
    refunds = await refunds_for_collections(session, organization_id, ids)
    refund_states = await effective_refund_states(session, organization_id, refunds)
    history = await history_for(session, organization_id, ids)
    views: list[CollectionOut] = []
    for charge in charges:
        own_refunds = [r for r in refunds if r.charge_id == charge.id]
        assert charge.reference_type is not None and charge.reference_id is not None
        views.append(
            CollectionOut(
                id=charge.id,
                reference_type=charge.reference_type,
                reference_id=charge.reference_id,
                state=charge.state,
                amount_minor=charge.amount_minor,
                currency=charge.currency,
                provider=charge.provider,
                provider_order_id=charge.provider_order_id,
                provider_payment_id=charge.provider_payment_id,
                failure_reason=charge.failure_reason,
                refunded_minor=sum(
                    r.amount_minor for r in own_refunds if refund_states[r.id] != REFUND_FAILED
                ),
                refunds=[
                    CollectionRefundOut(
                        id=r.id,
                        provider_refund_id=r.provider_refund_id,
                        amount_minor=r.amount_minor,
                        currency=r.currency,
                        state=refund_states[r.id],
                        reason=r.reason,
                        created_at=r.created_at,
                    )
                    for r in own_refunds
                ],
                history=[
                    StatusHistoryOut(
                        from_state=h.from_state,
                        to_state=h.to_state,
                        source=h.source,
                        reason=h.reason,
                        at=h.at,
                    )
                    for h in history
                    if h.entity == "charge" and h.entity_id == charge.id
                ],
                created_at=charge.created_at,
                updated_at=charge.updated_at,
            )
        )
    return views


async def due_expiring_collections(
    bypass_session: AsyncSession, *, now: datetime, older_than: datetime, limit: int = 50
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every open, unpaid collection past its checkout lifetime, across organizations, as
    `(organization_id, charge_id)` — the expiry sweep's discovery, on the BYPASSRLS outbox role's
    column grant (`SWEEP_READER_CHARGE_COLUMNS`). `authorized` is not due: money is held and a
    capture is on its way."""
    stmt = (
        select(Charge.organization_id, Charge.id)
        .where(
            Charge.payee == PAYEE_MERCHANT,
            Charge.state.in_((CHARGE_CREATED, CHARGE_FAILED)),
            Charge.deleted_at.is_(None),
            Charge.created_at <= older_than,
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
        )
        .order_by(Charge.created_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await bypass_session.execute(stmt)).all()]
