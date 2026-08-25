"""Writes for billing — the financial record of truth (ADR-0004 §1).

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction: an
internal route takes its session from `get_internal_session` (app/api/internal.py), which opens
the request's one transaction and binds it to the payload's organization first, so the change,
its audit entry and every event it emits all commit or all roll back together.

Two rules this module protects, both enforced under it as well as here (a message a person can
act on, over the raw database error that is the net):

* **A finalized invoice's lines foot exactly to its total** (`domain.assert_foots`,
  `Money.allocate`), and every line agrees on currency (`Money._same`, reached by summing).
* **A finalized invoice is frozen; only `finalized → void` is legal.** The service refuses any
  other change; the freeze trigger and `REVOKE UPDATE, DELETE` are the net (ADR-0000 §9).

The credit ledger is append-only: a grant, a consumption and a reversal are each a *new row*,
never a mutation, and the balance is a query, not a column (ADR-0004 §3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    CREDIT_CONSUME,
    CREDIT_GRANT,
    CREDIT_REVERSAL,
    INVOICE_FINALIZED,
    INVOICE_VOID,
    AuditLog,
    CreditLedgerEntry,
    Invoice,
    InvoiceLine,
    OutboxEvent,
)
from app.schemas import (
    CreditConsumeIn,
    CreditGrantIn,
    CreditReverseIn,
    FinalizeInvoiceIn,
    VoidInvoiceIn,
)
from platform_core.audit import record_audit
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.money import CurrencyMismatch, Money

EVT_INVOICE_FINALIZED = "commerce.invoice.finalized"
EVT_INVOICE_VOIDED = "commerce.invoice.voided"
EVT_CREDIT_GRANTED = "commerce.credit.granted"
EVT_CREDIT_CONSUMED = "commerce.credit.consumed"
EVT_CREDIT_REVERSED = "commerce.credit.reversed"

#: Internal writes are service-to-service (a billing run, an operator delegate), not a person
#: acting in a tenant, so the accountable party recorded on audit and outbox rows is the service.
_ACTOR: dict[str, Any] = {"type": "service", "service": "billing"}


# --------------------------------------------------------------------- invoices


async def finalize_invoice(
    session: AsyncSession, payload: FinalizeInvoiceIn, *, request: Request | None = None
) -> Invoice:
    """Create a finalized invoice from explicit, already-priced lines (ADR-0004 §1).

    The lines come through the `PricingSource` seam — today `ExplicitPricingSource`, tomorrow a
    catalog+metering-backed billing run — so the money path is identical whichever produced them.
    The total is the exact sum of the lines; a currency disagreement or a footing mismatch is
    refused here before a row is written.
    """
    drafts = [
        domain.LineDraft(
            description=line.description,
            quantity=line.quantity,
            amount_minor=line.amount_minor,
            currency=(line.currency or payload.currency).upper(),
            unit_price_micros=line.unit_price_micros,
            plan_version_id=line.plan_version_id,
        )
        for line in payload.lines
    ]

    source = domain.ExplicitPricingSource(drafts)
    period = domain.Period(payload.period_start, payload.period_end)
    assembled = await source.assemble(subscription_id=payload.subscription_id, period=period)

    currency = payload.currency.upper()
    try:
        total = domain.sum_lines(assembled, currency)
    except CurrencyMismatch as exc:
        # A line quoted in a different currency from the invoice. Refused with a message rather
        # than a wrong total (ADR-0000 §8).
        raise BusinessRuleProblem(
            f"every invoice line must be in the invoice currency {currency}: {exc}"
        ) from exc

    if payload.expected_total_minor is not None:
        try:
            domain.assert_foots(Money(payload.expected_total_minor, currency), assembled)
        except domain.LinesDoNotFoot as exc:
            raise BusinessRuleProblem(str(exc)) from exc

    invoice = Invoice(
        organization_id=payload.organization_id,
        subscription_id=payload.subscription_id,
        number=payload.number,
        state=INVOICE_FINALIZED,
        currency=currency,
        total_minor=total.minor,
        period_start=payload.period_start,
        period_end=payload.period_end,
        finalized_at=datetime.now(UTC),
    )
    session.add(invoice)
    try:
        await session.flush()
    except Exception as exc:  # unique (organization_id, number)
        raise ConflictProblem(
            f"an invoice numbered {payload.number!r} already exists for this organization"
        ) from exc

    for draft in assembled:
        session.add(
            InvoiceLine(
                organization_id=payload.organization_id,
                invoice_id=invoice.id,
                description=draft.description,
                quantity=draft.quantity,
                unit_price_micros=draft.unit_price_micros,
                amount_minor=draft.amount_minor,
                plan_version_id=draft.plan_version_id,
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=payload.organization_id,
        actor_id=None,
        action="billing.invoice.finalized",
        entity="invoice",
        entity_id=invoice.id,
        after={
            "number": invoice.number,
            "currency": invoice.currency,
            "total_minor": invoice.total_minor,
            "lines": len(assembled),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=payload.organization_id,
        event_type=EVT_INVOICE_FINALIZED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "invoice_id": str(invoice.id),
            "organization_id": str(invoice.organization_id),
            "subscription_id": str(invoice.subscription_id) if invoice.subscription_id else None,
            "number": invoice.number,
            "currency": invoice.currency,
            "total_minor": invoice.total_minor,
            "period_start": invoice.period_start.astimezone(UTC).isoformat(),
            "period_end": invoice.period_end.astimezone(UTC).isoformat(),
        },
        actor=_ACTOR,
    )
    return invoice


async def require_invoice(session: AsyncSession, invoice_id: Any) -> Invoice:
    invoice = await selectors.get_invoice(session, invoice_id)
    if invoice is None:
        raise NotFoundProblem("No such invoice")
    return invoice


async def void_invoice(
    session: AsyncSession,
    invoice: Invoice,
    payload: VoidInvoiceIn,
    *,
    request: Request | None = None,
) -> Invoice:
    """Mark a finalized invoice void — the one legal post-finalize transition (ADR-0000 §9).

    Voiding does not rewrite what was billed: the money columns and the lines are untouched, the
    row is simply marked uncollectible. The freeze trigger permits exactly this move and refuses
    every other change to a finalized invoice.
    """
    if invoice.state != INVOICE_FINALIZED:
        raise BusinessRuleProblem(
            f"invoice {invoice.number!r} is {invoice.state} and cannot be voided"
        )
    invoice.state = INVOICE_VOID
    invoice.voided_at = datetime.now(UTC)
    invoice.void_reason = payload.reason
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=invoice.organization_id,
        actor_id=None,
        action="billing.invoice.voided",
        entity="invoice",
        entity_id=invoice.id,
        after={"number": invoice.number, "reason": payload.reason},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=invoice.organization_id,
        event_type=EVT_INVOICE_VOIDED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "invoice_id": str(invoice.id),
            "organization_id": str(invoice.organization_id),
            "number": invoice.number,
            "total_minor": invoice.total_minor,
            "currency": invoice.currency,
            "reason": payload.reason,
        },
        actor=_ACTOR,
    )
    return invoice


# --------------------------------------------------------------------- credit ledger


def _publish_credit(session: AsyncSession, entry: CreditLedgerEntry, event_type: str) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=entry.organization_id,
        event_type=event_type,
        aggregate_type="credit_ledger_entry",
        aggregate_id=entry.id,
        payload={
            "entry_id": str(entry.id),
            "organization_id": str(entry.organization_id),
            "entry_type": entry.entry_type,
            "amount_minor": entry.amount_minor,
            "currency": entry.currency,
            "reason": entry.reason,
            "valid_from": entry.valid_from.astimezone(UTC).isoformat(),
            "valid_until": entry.valid_until.astimezone(UTC).isoformat()
            if entry.valid_until
            else None,
            "reversal_of": str(entry.reversal_of) if entry.reversal_of else None,
            "drawn_from": str(entry.drawn_from) if entry.drawn_from else None,
        },
        actor=_ACTOR,
    )


async def grant_credit(
    session: AsyncSession, payload: CreditGrantIn, *, request: Request | None = None
) -> CreditLedgerEntry:
    """A grant, comp-access or goodwill credit: one positive append-only row with a window."""
    entry = CreditLedgerEntry(
        organization_id=payload.organization_id,
        entry_type=CREDIT_GRANT,
        amount_minor=payload.amount_minor,
        currency=payload.currency.upper(),
        reason=payload.reason,
        valid_from=payload.valid_from or datetime.now(UTC),
        valid_until=payload.valid_until,
    )
    session.add(entry)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=payload.organization_id,
        actor_id=None,
        action="billing.credit.granted",
        entity="credit_ledger_entry",
        entity_id=entry.id,
        after={"amount_minor": entry.amount_minor, "currency": entry.currency},
        request=request,
    )
    _publish_credit(session, entry, EVT_CREDIT_GRANTED)
    return entry


async def _resolve_pack(
    session: AsyncSession, payload: CreditConsumeIn, pack_id: Any
) -> CreditLedgerEntry:
    """The grant a draw-down names, checked before a paisa of it is spent.

    Five refusals, and each of them is a way a prepaid pack goes wrong quietly rather than
    loudly (docs/commerce-packaging.md):

    * **Not this tenant's.** Row-level security scopes the lookup, so another organization's pack
      is simply not found — the same 404 an invented id gets.
    * **Not a grant.** Drawing "from" a consumption or a reversal would name a negative row as a
      source of credit, and the remaining-balance sum would then read as though there were more.
    * **Wrong currency.** Money is minor units *and* an ISO code (ADR-0000 §8); spending a rupee
      pack against a dollar invoice is not a conversion, it is a mistake.
    * **Lapsed.** Past its `valid_until` the pack no longer counts toward any balance, so a draw
      against it would net to zero and *look* like it had paid an invoice it did not pay.
    * **Overdrawn.** The draw may not exceed what is left, computed as a `SUM` over the pack and
      everything already drawn from it. Without this a pack can be spent twice over and the
      organization's balance simply goes negative.
    """
    pack = await selectors.get_credit_entry(session, pack_id)
    if pack is None:
        raise NotFoundProblem("No such credit ledger entry to draw down")
    if pack.entry_type != CREDIT_GRANT:
        raise BusinessRuleProblem(
            f"credit can only be drawn from a grant; entry {pack.id} is a {pack.entry_type}"
        )
    if pack.currency != payload.currency.upper():
        raise BusinessRuleProblem(
            f"this pack is in {pack.currency} and the draw-down is in {payload.currency.upper()}"
        )
    if pack.valid_until is not None and pack.valid_until <= datetime.now(UTC):
        raise BusinessRuleProblem(
            f"this pack lapsed at {pack.valid_until.astimezone(UTC).isoformat()} "
            "and can no longer be drawn down"
        )

    remaining = await selectors.pack_remaining(session, pack.id)
    if payload.amount_minor > remaining:
        raise BusinessRuleProblem(
            f"this pack has {remaining} {pack.currency} minor units left; "
            f"{payload.amount_minor} was asked for"
        )
    return pack


async def consume_credit(
    session: AsyncSession, payload: CreditConsumeIn, *, request: Request | None = None
) -> CreditLedgerEntry:
    """Spend credit: a negative append-only row. The grant it draws down is never mutated —
    the balance falls out of the `SUM`, not out of an edited row (ADR-0004 §3).

    With `drawn_from` set the row spends **one named pack** and inherits that pack's validity
    window, so the pair nets to zero at every instant the balance can be asked about. A
    consumption windowed `[now, NULL)` against a time-boxed pack is the bug the reversal path
    already fixed once: the moment the pack lapses the grant leaves the `SUM` while the
    open-ended consumption keeps counting, and the balance reads minus-what-was-spent forever,
    eating credit that has nothing to do with this pack.

    Without `drawn_from` the row spends the general balance and is open-ended, exactly as before.
    """
    pack = (
        None
        if payload.drawn_from is None
        else await _resolve_pack(session, payload, payload.drawn_from)
    )

    entry = CreditLedgerEntry(
        organization_id=payload.organization_id,
        entry_type=CREDIT_CONSUME,
        amount_minor=-payload.amount_minor,
        currency=payload.currency.upper(),
        reason=payload.reason,
        valid_from=datetime.now(UTC) if pack is None else pack.valid_from,
        valid_until=None if pack is None else pack.valid_until,
        drawn_from=None if pack is None else pack.id,
    )
    session.add(entry)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=payload.organization_id,
        actor_id=None,
        action="billing.credit.consumed",
        entity="credit_ledger_entry",
        entity_id=entry.id,
        after={
            "amount_minor": entry.amount_minor,
            "currency": entry.currency,
            "drawn_from": str(entry.drawn_from) if entry.drawn_from else None,
        },
        request=request,
    )
    _publish_credit(session, entry, EVT_CREDIT_CONSUMED)
    return entry


async def reverse_credit(
    session: AsyncSession, payload: CreditReverseIn, *, request: Request | None = None
) -> CreditLedgerEntry:
    """Reverse an earlier entry with a new row that negates it (ADR-0004 §3).

    Revoking a grant, or unwinding a mistaken consumption, is never a mutation or a delete — it
    is an equal-and-opposite append-only row that names the original in `reversal_of`, so the
    history stays intact and the balance reflects the reversal through the same `SUM`.
    """
    original = await selectors.get_credit_entry(session, payload.reversal_of)
    if original is None:
        # Row-level security scopes the lookup, so another tenant's entry is simply not found.
        raise NotFoundProblem("No such credit ledger entry to reverse")
    if original.entry_type == CREDIT_REVERSAL:
        raise BusinessRuleProblem("a reversal cannot itself be reversed")

    existing = await selectors.get_reversal_of(session, original.id)
    if existing is not None:
        # Idempotent, because the caller's delivery contract is at-least-once: a retried
        # reverse must move the balance once, not once per retry. The partial-unique index
        # on `reversal_of` is the net under this check for two racing transactions.
        return existing

    entry = CreditLedgerEntry(
        organization_id=payload.organization_id,
        entry_type=CREDIT_REVERSAL,
        amount_minor=-original.amount_minor,
        currency=original.currency,
        reason=payload.reason,
        # The negating row inherits the window it negates, so the pair sums to zero at
        # every instant the balance can be asked about. A reversal windowed `[now, NULL)`
        # used to outlive a time-boxed grant: once the grant's `valid_until` passed, the
        # grant left the SUM and the open-ended reversal kept counting — the balance
        # permanently understated by the grant amount, eating unrelated credit.
        valid_from=original.valid_from,
        valid_until=original.valid_until,
        # And the pack it was drawn from, for the same reason the window is inherited: a
        # reversal has to be indistinguishable from the row it negates in every dimension a
        # balance is summed over. Reversing a draw-down without carrying `drawn_from` restores
        # the organization's balance but leaves the *pack* still showing the credit as spent —
        # a per-pack remainder that disagrees with the ledger it is computed from.
        drawn_from=original.drawn_from,
        reversal_of=original.id,
    )
    session.add(entry)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The race the pre-check cannot see: another transaction reversed this entry
        # between our SELECT and our INSERT, and the partial-unique index refused ours.
        raise ConflictProblem("this credit ledger entry has already been reversed") from exc

    record_audit(
        session,
        AuditLog,
        organization_id=payload.organization_id,
        actor_id=None,
        action="billing.credit.reversed",
        entity="credit_ledger_entry",
        entity_id=entry.id,
        before={"reverses": str(original.id), "amount_minor": original.amount_minor},
        after={"amount_minor": entry.amount_minor, "currency": entry.currency},
        request=request,
    )
    _publish_credit(session, entry, EVT_CREDIT_REVERSED)
    return entry
