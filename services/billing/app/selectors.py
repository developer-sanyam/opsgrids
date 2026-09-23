"""Reads for billing. Scoped queries live here, each going through `ctx.apply`.

Isolation is the database's job here — every table is tenant-policied, so a session bound to
one organization cannot see another's rows — but the list still goes through `ctx.apply` at the
one enforcement point a negative-authz test guards (ADR-0003 §3).

The credit balance is the load-bearing read: it is a `SUM` over the ledger rows whose
`[valid_from, valid_until)` window covers a given instant, computed in SQL and **never** stored
(ADR-0004 §3). Ask it at two instants and a grant that lapsed between them drops out on its own,
with nothing having run to update a status column.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import CreditLedgerEntry, Invoice, TaxRate
from platform_core.auth import ScopeContext


def _scoped_invoices(ctx: ScopeContext, stmt: Select[tuple[Invoice]]) -> Select[tuple[Invoice]]:
    """The single scope-enforcement point for invoice lists (ADR-0003 §3).

    Invoices are organization-level documents, so the routes require `ORG` scope; `apply` is
    then a pass-through, but it is called so the enforcement is where the negative-authz test
    looks for it, not skipped on the assumption that RLS alone is enough.
    """
    return ctx.apply(stmt, owner_col=Invoice.organization_id)


async def list_invoices(session: AsyncSession, ctx: ScopeContext) -> list[Invoice]:
    stmt = _scoped_invoices(
        ctx,
        select(Invoice).options(selectinload(Invoice.lines)).order_by(Invoice.created_at.desc()),
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_invoice(session: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    """One invoice with its lines. Row-level security scopes it to the bound tenant, so an id
    belonging to another organization simply resolves to nothing."""
    return (
        await session.execute(
            select(Invoice).options(selectinload(Invoice.lines)).where(Invoice.id == invoice_id)
        )
    ).scalar_one_or_none()


async def credit_balance(session: AsyncSession, *, currency: str, at: datetime) -> int:
    """The credit available in `currency` at instant `at`, as a `SUM` over the covering rows.

    Balance is derived, never stored (ADR-0004 §3): a grant counts only while
    `valid_from <= at < valid_until` (an open `valid_until` counts forever), so a lapsed grant
    leaves the balance automatically. The tenant is scoped by row-level security, which is why
    there is no `organization_id` predicate here — adding one would be redundant, and forgetting
    it is not a leak.
    """
    stmt = (
        select(func.coalesce(func.sum(CreditLedgerEntry.amount_minor), 0))
        .where(CreditLedgerEntry.currency == currency.upper())
        .where(CreditLedgerEntry.valid_from <= at)
        .where(
            or_(
                CreditLedgerEntry.valid_until.is_(None),
                CreditLedgerEntry.valid_until > at,
            )
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def pack_remaining(session: AsyncSession, pack_id: uuid.UUID) -> int:
    """What is left on one prepaid pack: a `SUM` over the grant and everything drawn from it.

    Derived exactly like the organization-wide balance and for the same reason (ADR-0004 §3) — a
    "remaining" column on the grant would be a second source of truth that drifts from the ledger
    the first time two invoices draw on the pack at once. The grant is the positive row; each
    draw-down is a negative row naming it in `drawn_from`; a reversal of either is a further row
    that is picked up here too, because it carries the same `drawn_from`.

    Deliberately **not** windowed: this answers "how much of this pack has been spent", which is
    a fact about the pack and not about the clock. Whether the pack still counts toward the
    organization's balance is `credit_balance`'s question, and it answers it by the window.
    """
    stmt = select(func.coalesce(func.sum(CreditLedgerEntry.amount_minor), 0)).where(
        or_(
            CreditLedgerEntry.id == pack_id,
            CreditLedgerEntry.drawn_from == pack_id,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_credit_entry(session: AsyncSession, entry_id: uuid.UUID) -> CreditLedgerEntry | None:
    return (
        await session.execute(select(CreditLedgerEntry).where(CreditLedgerEntry.id == entry_id))
    ).scalar_one_or_none()


async def get_reversal_of(session: AsyncSession, entry_id: uuid.UUID) -> CreditLedgerEntry | None:
    """The reversal already written against `entry_id`, if any.

    At most one exists — `ix_credit_ledger_org_reversal_of` is partial-unique — so this is
    how `reverse_credit` stays idempotent under at-least-once delivery.
    """
    return (
        await session.execute(
            select(CreditLedgerEntry).where(CreditLedgerEntry.reversal_of == entry_id)
        )
    ).scalar_one_or_none()


async def get_invoice_by_key(session: AsyncSession, idempotency_key: str) -> Invoice | None:
    """The billing run's invoice for one subscription period, if it has been raised.

    Scoped by row-level security to the bound tenant — the key is only unique per organization,
    and another organization's invoice under the same key must be, and is, invisible.
    """
    return (
        await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.lines))
            .where(Invoice.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()


async def effective_tax_rate(
    session: AsyncSession, *, code: str, jurisdiction: str | None, on: date
) -> TaxRate | None:
    """The statutory rate for `code` in force on `on`: the latest row whose `effective_from` is
    not after it. Versioned, never mutated (ADR-0000 §9), so re-running March uses March's rate.

    `tax_rates` is global platform reference data with no tenant policy (models.TaxRate), so this
    reads the same answer under any binding.
    """
    stmt = (
        select(TaxRate)
        .where(TaxRate.code == code, TaxRate.effective_from <= on)
        .order_by(TaxRate.effective_from.desc())
        .limit(1)
    )
    if jurisdiction is None:
        stmt = stmt.where(TaxRate.jurisdiction.is_(None))
    else:
        stmt = stmt.where(TaxRate.jurisdiction == jurisdiction)
    return (await session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "credit_balance",
    "effective_tax_rate",
    "get_credit_entry",
    "get_invoice",
    "get_invoice_by_key",
    "get_reversal_of",
    "list_invoices",
    "pack_remaining",
]
