"""Writes for insights (`docs/insights/data-model.md` §1.1, §2, §5).

Every write here is one version-guarded upsert, never a plain `INSERT`/`UPDATE`: the event
consumer (`app/consumers.py`), the backfill runner (`app/backfill.py`) and the snapshot sweep
(`app/snapshot.py`) all call these, each inside its own transaction, already bound to the
organization's tenant GUC (`session_scope`/`bulkhead_scope`). Nothing here opens or commits a
transaction, matching the service anatomy every other service follows.

I1 has one fact-shaped table, `org_profile` — every `*_facts` table I2+ adds gets its own
version-guarded upsert here, following `upsert_org_profile`'s shape.

I2 adds finance's three (`docs/insights/data-model.md` §3, §1.1):

- `upsert_invoice_fact_full` — the full-state write, from `finance.billing.invoice.issued` or an
  export page.
- `apply_invoice_paid` — a **partial** event-guarded `UPDATE ... WHERE`, never an `INSERT`:
  `finance.billing.invoice.paid` carries only the fields that changed, not an invoice's full
  state, so a fact that does not already exist is left alone rather than created with guessed
  columns (§1.1's rule for partial events, extended here to a transition the ADR's own
  enumerated list did not name but which is exactly the same shape).
- `upsert_payment_fact`, `upsert_purchase_fact` — full-state writes, append-only at the source.

Every export-page writer additionally records `last_seen_run` **even when the version guard
declines the row** (§1.1: "still records last_seen_run ... so mark-and-sweep does not mistake
'yielded to a newer event' for 'gone at the source'"), via the small unconditional follow-up
`UPDATE` each one issues after its guarded upsert.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DailySnapshot,
    InvoiceFacts,
    MetricCoverage,
    OrgProfile,
    PaymentFacts,
    PurchaseFacts,
)


async def upsert_org_profile(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    timezone: str,
    currency: str,
    fiscal_year_start_month: int,
    source_updated_at: datetime,
) -> bool:
    """Apply one organization's settings, if and only if `source_updated_at` is not older than
    what is stored (data-model.md §1.1: `apply iff incoming.source_updated_at >=
    stored.source_updated_at`). `core.org.created`/`core.org.updated` (`app/consumers.py`) and
    the account export page (`app/backfill.py`) both call this — an event's version is its
    envelope `occurred_at`; the export's is the source row's own `updated_at`. Equal is a
    legitimate replay of the same version, which rewrites identical values rather than being
    dropped, so an event and a re-export of the same state always agree.

    Returns whether the row was written (created, or updated because its version passed the
    gate) — `False` for a stale or malformed-order write, which is the intended, non-error
    outcome under at-least-once delivery.
    """
    stmt = pg_insert(OrgProfile).values(
        organization_id=organization_id,
        timezone=timezone,
        currency=currency,
        fiscal_year_start_month=fiscal_year_start_month,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[OrgProfile.organization_id],
        set_={
            "timezone": stmt.excluded.timezone,
            "currency": stmt.excluded.currency,
            "fiscal_year_start_month": stmt.excluded.fiscal_year_start_month,
            "source_updated_at": stmt.excluded.source_updated_at,
            "ingested_at": func.now(),
        },
        # See `services/approval/app/services.py:upsert_hierarchy_nodes_bulk` for why rowcount,
        # not RETURNING, is what tells a dropped-as-stale row apart from "nothing to do": a
        # conflicting row whose WHERE fails is not counted by PostgreSQL's own command tag.
        where=OrgProfile.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_daily_snapshot(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    metric: str,
    dimension: str,
    day: date,
    value: int,
    currency: str | None,
    captured_at: datetime,
) -> None:
    """One hourly capture (`docs/insights/metrics.md` §8, data-model.md §5): "the last capture
    of a day is the day's value." Unconditional `DO UPDATE`, never version-guarded — unlike a
    fact, a snapshot has no source row to be older or newer than; it *is* the observation, and
    `app/snapshot.py`'s own sweep is the only caller, which only ever calls this for the
    organization's **current local day**. A closed day's row is never touched again because the
    sweep never asks to touch it, not because this function refuses to — see that module's own
    docstring for the guard that makes that true.
    """
    stmt = pg_insert(DailySnapshot).values(
        organization_id=organization_id,
        metric=metric,
        dimension=dimension,
        day=day,
        value=value,
        currency=currency,
        captured_at=captured_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            DailySnapshot.organization_id,
            DailySnapshot.metric,
            DailySnapshot.dimension,
            DailySnapshot.day,
        ],
        set_={
            "value": stmt.excluded.value,
            "currency": stmt.excluded.currency,
            "captured_at": stmt.excluded.captured_at,
        },
    )
    await session.execute(stmt)


async def record_metric_coverage(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    metric: str,
    history_from: date | None,
    basis: str,
) -> None:
    """Recomputed after every completed backfill run for the entity a metric reads, and by the
    sweep when a snapshot metric's first capture lands (`docs/insights/data-model.md` §5)."""
    stmt = pg_insert(MetricCoverage).values(
        organization_id=organization_id,
        metric=metric,
        history_from=history_from,
        basis=basis,
        computed_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MetricCoverage.organization_id, MetricCoverage.metric],
        set_={
            "history_from": stmt.excluded.history_from,
            "basis": stmt.excluded.basis,
            "computed_at": func.now(),
        },
    )
    await session.execute(stmt)


# --------------------------------------------------------------------- finance facts (I2)


async def upsert_invoice_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    number: str,
    document_type: str,
    status: str,
    issued_date: date,
    due_date: date,
    currency: str,
    subtotal_minor: int,
    tax_minor: int,
    total_minor: int,
    customer_ref: uuid.UUID,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`finance.billing.invoice.issued` — full state (data-model.md §1.1's event rule: never let
    an older version overwrite a newer one). `paid_at` is deliberately absent from the `SET`: a
    replayed `issued` must never clear a `paid_at` a later `invoice.paid` fold already set — it
    is set only on first insert, when there is nothing yet to clear.
    """
    stmt = pg_insert(InvoiceFacts).values(
        organization_id=organization_id,
        id=id,
        number=number,
        document_type=document_type,
        status=status,
        issued_date=issued_date,
        due_date=due_date,
        currency=currency,
        subtotal_minor=subtotal_minor,
        tax_minor=tax_minor,
        total_minor=total_minor,
        customer_ref=customer_ref,
        paid_at=None,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[InvoiceFacts.organization_id, InvoiceFacts.id],
        set_={
            "number": stmt.excluded.number,
            "document_type": stmt.excluded.document_type,
            "status": stmt.excluded.status,
            "issued_date": stmt.excluded.issued_date,
            "due_date": stmt.excluded.due_date,
            "currency": stmt.excluded.currency,
            "subtotal_minor": stmt.excluded.subtotal_minor,
            "tax_minor": stmt.excluded.tax_minor,
            "total_minor": stmt.excluded.total_minor,
            "customer_ref": stmt.excluded.customer_ref,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "ingested_at": func.now(),
        },
        where=InvoiceFacts.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def apply_invoice_paid(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    status: str,
    paid_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`finance.billing.invoice.paid` — a **partial** transition, applied as `UPDATE ... WHERE`
    and never an `INSERT` (data-model.md §1.1): the event carries only `status`/`paid_at`/
    `currency`, not an invoice's full state, so a fact that is not already there is left alone
    rather than created with guessed columns. `False` means either a stale replay or an orphan
    transition (the creating `invoice.issued` fold has not landed yet) — `app/consumers.py`
    tells the two apart and logs accordingly; either way the weekly re-export repairs it.
    """
    stmt = (
        update(InvoiceFacts)
        .where(InvoiceFacts.organization_id == organization_id, InvoiceFacts.id == id)
        .where(InvoiceFacts.source_updated_at <= source_updated_at)
        .values(
            status=status,
            paid_at=paid_at,
            source_updated_at=source_updated_at,
            ingested_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def invoice_fact_exists(
    session: AsyncSession, *, organization_id: uuid.UUID, id: uuid.UUID
) -> bool:
    """Whether `id` has a fact row at all — used only to tell an orphan `invoice.paid` transition
    apart from a stale replay for `app/consumers.py`'s own logging (data-model.md §1.1)."""
    return (
        await session.get(InvoiceFacts, {"organization_id": organization_id, "id": id})
    ) is not None


async def upsert_invoice_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    number: str,
    document_type: str,
    status: str,
    issued_date: date,
    due_date: date,
    currency: str,
    subtotal_minor: int,
    tax_minor: int,
    total_minor: int,
    customer_ref: uuid.UUID,
    paid_at: datetime | None,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> None:
    """One export page row — a point-in-time read, so the whole row overwrites the fact **unless
    an event wrote it after this page was requested** (data-model.md §1.1's export rule). The
    export's own `paid_at` is authoritative here, unlike the event fold: an export row is full
    state, never a partial transition. `last_seen_run` is recorded **unconditionally** afterward
    — even when the guarded upsert above declines the row — so mark-and-sweep does not read
    "yielded to a newer event" as "gone at the source" (§1.1).
    """
    stmt = pg_insert(InvoiceFacts).values(
        organization_id=organization_id,
        id=id,
        number=number,
        document_type=document_type,
        status=status,
        issued_date=issued_date,
        due_date=due_date,
        currency=currency,
        subtotal_minor=subtotal_minor,
        tax_minor=tax_minor,
        total_minor=total_minor,
        customer_ref=customer_ref,
        paid_at=paid_at,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[InvoiceFacts.organization_id, InvoiceFacts.id],
        set_={
            "number": stmt.excluded.number,
            "document_type": stmt.excluded.document_type,
            "status": stmt.excluded.status,
            "issued_date": stmt.excluded.issued_date,
            "due_date": stmt.excluded.due_date,
            "currency": stmt.excluded.currency,
            "subtotal_minor": stmt.excluded.subtotal_minor,
            "tax_minor": stmt.excluded.tax_minor,
            "total_minor": stmt.excluded.total_minor,
            "customer_ref": stmt.excluded.customer_ref,
            "paid_at": stmt.excluded.paid_at,
            "deleted_at": stmt.excluded.deleted_at,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=InvoiceFacts.ingested_at < page_requested_at,
    )
    await session.execute(stmt)
    await session.execute(
        update(InvoiceFacts)
        .where(InvoiceFacts.organization_id == organization_id, InvoiceFacts.id == id)
        .values(last_seen_run=run_id)
    )


async def upsert_payment_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    reference: str,
    received_date: date,
    method: str,
    currency: str,
    amount_minor: int,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`finance.billing.payment.received` — full state; payments are append-only at the source,
    so a fact is written once and only a replay touches it again (data-model.md §3)."""
    stmt = pg_insert(PaymentFacts).values(
        organization_id=organization_id,
        id=id,
        reference=reference,
        received_date=received_date,
        method=method,
        currency=currency,
        amount_minor=amount_minor,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PaymentFacts.organization_id, PaymentFacts.id],
        set_={
            "reference": stmt.excluded.reference,
            "received_date": stmt.excluded.received_date,
            "method": stmt.excluded.method,
            "currency": stmt.excluded.currency,
            "amount_minor": stmt.excluded.amount_minor,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "ingested_at": func.now(),
        },
        where=PaymentFacts.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_payment_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    reference: str,
    received_date: date,
    method: str,
    currency: str | None,
    amount_minor: int,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> None:
    """The export-page write rule (data-model.md §1.1), for `payment_facts`. `currency` may be
    `None` on the export's own row shape (finance's export leaves it null rather than guessing
    when a payment somehow holds no allocation); a payment without a currency is not summed into
    `payments_received` until an event or a later export corrects it.
    """
    stmt = pg_insert(PaymentFacts).values(
        organization_id=organization_id,
        id=id,
        reference=reference,
        received_date=received_date,
        method=method,
        currency=currency or "",
        amount_minor=amount_minor,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PaymentFacts.organization_id, PaymentFacts.id],
        set_={
            "reference": stmt.excluded.reference,
            "received_date": stmt.excluded.received_date,
            "method": stmt.excluded.method,
            "currency": stmt.excluded.currency,
            "amount_minor": stmt.excluded.amount_minor,
            "deleted_at": stmt.excluded.deleted_at,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=PaymentFacts.ingested_at < page_requested_at,
    )
    await session.execute(stmt)
    await session.execute(
        update(PaymentFacts)
        .where(PaymentFacts.organization_id == organization_id, PaymentFacts.id == id)
        .values(last_seen_run=run_id)
    )


async def upsert_purchase_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    number: str,
    document_type: str,
    document_date: date,
    currency: str,
    taxable_minor: int,
    tax_minor: int,
    total_minor: int,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`finance.billing.purchase.recorded` — full state (data-model.md §3)."""
    stmt = pg_insert(PurchaseFacts).values(
        organization_id=organization_id,
        id=id,
        number=number,
        document_type=document_type,
        document_date=document_date,
        currency=currency,
        taxable_minor=taxable_minor,
        tax_minor=tax_minor,
        total_minor=total_minor,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PurchaseFacts.organization_id, PurchaseFacts.id],
        set_={
            "number": stmt.excluded.number,
            "document_type": stmt.excluded.document_type,
            "document_date": stmt.excluded.document_date,
            "currency": stmt.excluded.currency,
            "taxable_minor": stmt.excluded.taxable_minor,
            "tax_minor": stmt.excluded.tax_minor,
            "total_minor": stmt.excluded.total_minor,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "ingested_at": func.now(),
        },
        where=PurchaseFacts.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_purchase_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    number: str,
    document_type: str,
    document_date: date,
    currency: str,
    taxable_minor: int,
    tax_minor: int,
    total_minor: int,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> None:
    """The export-page write rule (data-model.md §1.1), for `purchase_facts`."""
    stmt = pg_insert(PurchaseFacts).values(
        organization_id=organization_id,
        id=id,
        number=number,
        document_type=document_type,
        document_date=document_date,
        currency=currency,
        taxable_minor=taxable_minor,
        tax_minor=tax_minor,
        total_minor=total_minor,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PurchaseFacts.organization_id, PurchaseFacts.id],
        set_={
            "number": stmt.excluded.number,
            "document_type": stmt.excluded.document_type,
            "document_date": stmt.excluded.document_date,
            "currency": stmt.excluded.currency,
            "taxable_minor": stmt.excluded.taxable_minor,
            "tax_minor": stmt.excluded.tax_minor,
            "total_minor": stmt.excluded.total_minor,
            "deleted_at": stmt.excluded.deleted_at,
            "source_created_at": stmt.excluded.source_created_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=PurchaseFacts.ingested_at < page_requested_at,
    )
    await session.execute(stmt)
    await session.execute(
        update(PurchaseFacts)
        .where(PurchaseFacts.organization_id == organization_id, PurchaseFacts.id == id)
        .values(last_seen_run=run_id)
    )
