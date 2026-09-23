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

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    CREDIT_CONSUME,
    CREDIT_GRANT,
    CREDIT_REVERSAL,
    INVOICE_DRAFT,
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

    _stage_finalized(session, invoice, line_count=len(assembled), request=request)
    return invoice


def _stage_finalized(
    session: AsyncSession,
    invoice: Invoice,
    *,
    line_count: int,
    request: Request | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """The audit row and the `commerce.invoice.finalized` event, staged in the invoice's own
    transaction — one path for a hand-finalized invoice and a billing-run one, so payment sees
    the same event whichever produced it."""
    record_audit(
        session,
        AuditLog,
        organization_id=invoice.organization_id,
        actor_id=None,
        action="billing.invoice.finalized",
        entity="invoice",
        entity_id=invoice.id,
        after={
            "number": invoice.number,
            "currency": invoice.currency,
            "total_minor": invoice.total_minor,
            "lines": line_count,
            **({"idempotency_key": invoice.idempotency_key} if invoice.idempotency_key else {}),
        },
        request=request,
    )
    payload: dict[str, Any] = {
        "invoice_id": str(invoice.id),
        "organization_id": str(invoice.organization_id),
        "subscription_id": str(invoice.subscription_id) if invoice.subscription_id else None,
        "number": invoice.number,
        "currency": invoice.currency,
        "total_minor": invoice.total_minor,
        "period_start": invoice.period_start.astimezone(UTC).isoformat(),
        "period_end": invoice.period_end.astimezone(UTC).isoformat(),
    }
    if extra:
        payload.update(extra)
    publish_event(
        session,
        OutboxEvent,
        organization_id=invoice.organization_id,
        event_type=EVT_INVOICE_FINALIZED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload=payload,
        actor=_ACTOR,
    )


#: How many numbers to try when a hand-finalized invoice already holds the derived one.
_NUMBER_ATTEMPTS = 5


async def record_subscription_invoice(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    product_key: str,
    currency: str,
    key: str,
    period: domain.Period,
    lines: list[domain.LineDraft],
    kind: str = "period",
    request: Request | None = None,
) -> tuple[Invoice, bool]:
    """Write the billing run's invoice for one claim, as a **draft** — once. `(invoice, created)`.

    A draft announces nothing: no `commerce.invoice.finalized`, so payment cannot charge it
    (review round 2, fix M5). Subscription records the claim first and only then asks for
    `finalize_subscription_invoice`, so a period superseded in between is a draft voided before
    anyone could act on it. Lines are written now; the draft → finalized move freezes them.

    The **record** step of claim → call → record: every price, usage count and tax rate has
    already been read, outside any transaction, and this function only writes. The insert is
    `ON CONFLICT DO NOTHING` against `uq_invoices_org_idempotency_key`, so the loser of two racing
    requests for the same claim reads the winner's invoice back and stages **no** second event —
    a second `commerce.invoice.finalized` would be a second charge.

    The number is derived from the key; if a hand-finalized invoice already holds it, the next
    suffix is used rather than failing the claim for good (review fix 12) — a number clash is not
    a reason a period can never be invoiced.
    """
    currency = currency.upper()
    try:
        total = domain.sum_lines(lines, currency)
    except CurrencyMismatch as exc:  # pragma: no cover - the pricer already refused it
        raise BusinessRuleProblem(f"every line must be in {currency}: {exc}") from exc

    # The number: the key's derived one, or the first suffix no invoice holds. Checked up front,
    # not by catching the unique violation under a savepoint — the frozen-lines trigger lets
    # lines in only under the transaction (not a subtransaction) that created the invoice. A
    # hand-finalized invoice taking the number between this read and the insert fails this one
    # attempt; the run retries and this read then finds the next suffix.
    taken = await selectors.invoice_numbers_like(
        session, organization_id, domain.subscription_invoice_number(key, period.start)
    )
    number = next(
        (
            candidate
            for attempt in range(_NUMBER_ATTEMPTS)
            if (candidate := domain.subscription_invoice_number(key, period.start, attempt))
            not in taken
        ),
        None,
    )
    if number is None:
        raise ConflictProblem("no free invoice number for this billing-run invoice")

    invoice_id = uuid.uuid4()
    now = datetime.now(UTC)
    stmt = (
        pg_insert(Invoice)
        .values(
            id=invoice_id,
            organization_id=organization_id,
            subscription_id=subscription_id,
            number=number,
            state=INVOICE_DRAFT,
            currency=currency,
            total_minor=total.minor,
            period_start=period.start,
            period_end=period.end,
            finalized_at=None,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(
            index_elements=["organization_id", "idempotency_key"],
            index_where=text("idempotency_key IS NOT NULL"),
        )
        .returning(Invoice.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is None:
        existing = await selectors.get_invoice_by_key(session, organization_id, key)
        if existing is None:  # pragma: no cover - the conflict proves the row exists
            raise ConflictProblem("invoice idempotency conflict with no visible row")
        return existing, False

    for draft in lines:
        session.add(
            InvoiceLine(
                organization_id=organization_id,
                invoice_id=invoice_id,
                description=draft.description,
                quantity=draft.quantity,
                unit_price_micros=draft.unit_price_micros,
                amount_minor=draft.amount_minor,
                plan_version_id=draft.plan_version_id,
            )
        )
    await session.flush()

    invoice = await require_invoice(session, invoice_id)
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        action="billing.invoice.drafted",
        entity="invoice",
        entity_id=invoice.id,
        after={
            "number": invoice.number,
            "total_minor": invoice.total_minor,
            "idempotency_key": key,
            "kind": kind,
            "product_key": product_key,
            "created_at": now.isoformat(),
        },
        request=request,
    )
    await session.flush()
    return invoice, True


async def finalize_subscription_invoice(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    key: str,
    product_key: str | None,
    kind: str,
    request: Request | None = None,
) -> tuple[Invoice, bool]:
    """Finalize the billing run's draft under `key` — once. `(invoice, finalized_by_this_call)`.

    `draft -> finalized` freezes the invoice (the freeze trigger refuses everything but a void from
    here) and stages `commerce.invoice.finalized`, which payment charges. Idempotent: a finalized
    invoice answers `(invoice, False)` and stages nothing, so a crash between this commit and the
    run's record of it re-asks and changes nothing. A void invoice is refused — the run voids only
    a period it has superseded, which it never then finalizes.
    """
    invoice = await selectors.get_invoice_by_key(session, organization_id, key)
    if invoice is None:
        raise NotFoundProblem("No billing-run invoice under that key")
    if invoice.state == INVOICE_FINALIZED:
        return invoice, False
    if invoice.state == INVOICE_VOID:
        raise ConflictProblem("that billing-run invoice is void and cannot be finalized")
    invoice.state = INVOICE_FINALIZED
    invoice.finalized_at = datetime.now(UTC)
    await session.flush()
    _stage_finalized(
        session,
        invoice,
        line_count=len(invoice.lines),
        request=request,
        extra={"product_key": product_key, "source": "billing_run", "kind": kind},
    )
    await session.flush()
    return invoice, True


async def void_subscription_invoice(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    key: str,
    reason: str,
    request: Request | None = None,
) -> tuple[bool, uuid.UUID | None]:
    """Void the billing-run invoice standing under `key`, if there is one. `(voided, id)`.

    For a period the subscription moved past between the run's claim and its record (review fix
    11): the invoice must not stand. Void is the one legal post-finalize transition — a state,
    never a delete, and it stages `commerce.invoice.voided`. Idempotent: an already void invoice
    answers `(True, id)`; no invoice under the key answers `(False, None)`.
    """
    invoice = await selectors.get_invoice_by_key(session, organization_id, key)
    if invoice is None:
        return False, None
    if invoice.state == INVOICE_VOID:
        return True, invoice.id
    if invoice.state == INVOICE_DRAFT:
        # Never announced, so never charged: void it as a state, with an audit row and no event
        # — nothing downstream ever learned it existed (review round 2, fix M5).
        invoice.state = INVOICE_VOID
        invoice.voided_at = datetime.now(UTC)
        invoice.void_reason = reason
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            action="billing.invoice.draft_voided",
            entity="invoice",
            entity_id=invoice.id,
            after={"number": invoice.number, "reason": reason},
            request=request,
        )
        return True, invoice.id
    await void_invoice(
        session,
        invoice,
        VoidInvoiceIn(organization_id=organization_id, reason=reason),
        request=request,
    )
    return True, invoice.id


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
