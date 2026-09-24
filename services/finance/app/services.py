"""Writes for finance.

Business rules, audit rows and outbox rows — all inside the caller's one transaction. Nothing
here opens or commits: a route with a principal takes its session from `get_tenant_session`,
which opens the request's transaction and binds it to the caller's organization first
(ADR-0002 §1), so a state change, its audit entry and any event it emits all commit or all roll
back together. The demo seed drives these same functions inside its own `session.begin()`.

The invoice and payment writes are internal: the finance SPA raises nothing, so they exist to
(a) finalize the append-only documents the reads serve and (b) let the demo seed build the org's
book through the real path rather than raw inserts. An invoice is frozen at finalize: it foots
exactly here, and a correction is a new credit-note document, never an edit.

Two write paths are **not** driven by a person, and they are shaped for that:

* **Auto-filing.** Finalizing a reportable document stages a filing request on the outbox, so the
  attempt is sent only once the invoice has committed and never inside the request that raised it.
  The consumer calls `autofile_invoice`, which answers with named outcomes rather than exceptions
  — a queue has nobody to raise at, and a handler that raised on "already filed" would turn every
  redelivery into a dead letter.
* **IMS reconciliation.** Ingest, reconcile and decide are three separate writes on purpose. The
  first two are automatic and change no document's status; only `record_ims_decision` does, and it
  needs a principal, because accepting a supplier's document claims the input tax credit on it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, case, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from starlette.requests import Request

from app import domain, selectors
from app.config import settings
from app.eway.base import (
    EwayAck,
    EwayAlreadyCancelled,
    EwayAlreadyGenerated,
    EwayCancelAck,
    EwayClient,
    EwayLine,
    EwayPayload,
    EwayRateLimited,
    EwayRejected,
    EwayUnavailable,
)
from app.ims.base import (
    ImsClient,
    ImsPushItem,
    ImsPushRejected,
    ImsPushResult,
    ImsRateLimited,
    ImsSupplierDocument,
    ImsUnavailable,
)
from app.irp.base import (
    IrpAck,
    IrpAlreadyCancelled,
    IrpCancelAck,
    IrpClient,
    IrpDuplicate,
    IrpInvoicePayload,
    IrpLine,
    IrpRejected,
    IrpUnavailable,
)
from app.models import (
    AuditLog,
    Customer,
    EInvoiceFiling,
    EwayBill,
    GstRegistration,
    GstReturn,
    GstReturnTransition,
    ImsDecision,
    ImsDocument,
    ImsPush,
    ImsReconciliation,
    Invoice,
    InvoiceLine,
    InvoiceNumberSeries,
    InvoiceWithholding,
    ItcLedgerEntry,
    OutboxEvent,
    Payment,
    PaymentAllocation,
    PurchaseDocument,
)
from app.schemas import (
    CancelEwayIn,
    CancelFilingIn,
    CustomerCreate,
    CustomerUpdate,
    FileInvoiceIn,
    FileReturnIn,
    GenerateEwayIn,
    GenerateReturnIn,
    Gstr1Sections,
    Gstr3bPaymentOut,
    Gstr3bSections,
    GstRegistrationUpsert,
    ImsBulkDecisionItem,
    ImsDecisionCreate,
    ImsPushRequestOut,
    InvoiceCreate,
    PaymentCreate,
    PurchaseDocumentCreate,
    ReturnDiffOut,
    SectionDeltaOut,
    TaxHeadAmountsOut,
)
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
)
from platform_core.events import publish_event, schedule_event

# Event names. `finance.*` because these are the product's own, not the platform's; the second
# segment is the module, matching the permission grammar (ADR-0000 §5).
EVT_CUSTOMER_CREATED = "finance.billing.customer.created"
EVT_CUSTOMER_UPDATED = "finance.billing.customer.updated"
EVT_CUSTOMER_ARCHIVED = "finance.billing.customer.archived"
EVT_INVOICE_ISSUED = "finance.billing.invoice.issued"
EVT_INVOICE_PAID = "finance.billing.invoice.paid"
EVT_PAYMENT_RECEIVED = "finance.billing.payment.received"
#: The compliance events. `filed` is the one the auto-filing consumer both produces the trigger
#: for and confirms against — it carries the IRN, so a consumer never has to read back to learn
#: whether the attempt succeeded.
EVT_INVOICE_FILED = "finance.compliance.invoice.filed"
EVT_INVOICE_FILING_FAILED = "finance.compliance.invoice.filing_failed"
EVT_INVOICE_FILING_CANCELLED = "finance.compliance.invoice.filing_cancelled"
#: **The auto-filing trigger.** Staged on the outbox in the same transaction as the invoice, so it
#: is sent only once the document is durable, and re-published by the consumer to schedule a
#: bounded retry after a portal outage. Never handled inline: the reporting window is days, and a
#: filing attempt made inside the request that raised the invoice would tie a customer's ability
#: to bill to a government portal's uptime.
EVT_INVOICE_FILING_REQUESTED = "finance.compliance.invoice.filing_requested"
EVT_GST_REGISTRATION_SET = "finance.compliance.gst_registration.set"
#: The IMS events. `ingested` is what a supplier filed about this business arriving; `decided` is
#: what this business chose to do about it — the row that decides whether a credit is claimed.
EVT_IMS_DOCUMENT_INGESTED = "finance.compliance.ims_document.ingested"
EVT_IMS_DOCUMENT_DECIDED = "finance.compliance.ims_document.decided"
#: Telling GSTN what was decided (C2). `requested` is a **command** to this service's own worker — a
#: person with the push permission asked for one period's decisions to be sent — and `completed` /
#: `drift_detected` are announcements: what one portal call did, and that a sync found the portal
#: disagreeing with a decision that had been pushed.
EVT_IMS_PUSH_REQUESTED = "finance.compliance.ims_push.requested"
EVT_IMS_PUSH_COMPLETED = "finance.compliance.ims_push.completed"
EVT_IMS_PUSH_DRIFT_DETECTED = "finance.compliance.ims_push.drift_detected"
#: The return events. `generated` announces a snapshot of a period; `filed` announces that the
#: snapshot went to the portal. Two events rather than one because they are two claims — one
#: about this platform's book, one to a government — and a consumer may reasonably care about
#: only the second.
EVT_RETURN_GENERATED = "finance.compliance.gst_return.generated"
EVT_RETURN_FILED = "finance.compliance.gst_return.filed"
#: The e-Way events (C4). Three are announcements a person or a notification rule acts on:
#: `generated` (a bill exists), `retry_scheduled` (the portal did not answer and the same
#: consignment will be sent again at `retry_at` — the goods are loaded, so this is worth hearing),
#: and `failed`, which since C4 is **terminal only** — a refusal, an exhausted budget or a chain
#: that went stale — and carries the remedy. `retryable` stays on it (always false now) so a
#: consumer written against the earlier contract keeps parsing.
EVT_EWAY_GENERATED = "finance.compliance.eway_bill.generated"
EVT_EWAY_FAILED = "finance.compliance.eway_bill.failed"
EVT_EWAY_RETRY_SCHEDULED = "finance.compliance.eway_bill.retry_scheduled"
EVT_EWAY_CANCELLED = "finance.compliance.eway_bill.cancelled"
#: **The retry trigger** — a command to this service's own worker, not an announcement. Published
#: directly for a person's "retry now" and handed to the scheduler (`schedule_event`, ADR-0015) for
#: an automatic retry, exactly as `EVT_INVOICE_FILING_REQUESTED` is for a filing.
EVT_EWAY_GENERATION_REQUESTED = "finance.compliance.eway_bill.generation_requested"
#: A withholding assessed on a finalized document. Announced because the money it names never
#: arrives: a downstream collections or cash-application consumer that expects the gross will
#: chase a receivable that was settled net.
EVT_WITHHOLDING_RECORDED = "finance.compliance.withholding.recorded"
#: The ITC ledger events. `accrued` and `reversed` are posted inside `record_ims_decision`, in the
#: same transaction as the decision that caused them; `utilised` inside `file_return`, in the same
#: transaction as filing a `gstr3b`. Each carries the tax head and the signed amount, so a
#: downstream consumer can watch the credit balance move without re-reading the ledger.
EVT_ITC_ACCRUED = "finance.compliance.itc.accrued"
EVT_ITC_REVERSED = "finance.compliance.itc.reversed"
EVT_ITC_UTILISED = "finance.compliance.itc.utilised"

#: The permission an automated filing runs under. Named rather than omitted: the audit and outbox
#: rows every filing writes need an actor and a scope, and a service acting on an organization's
#: behalf must be as identifiable in the trail as a person would be.
FILING_PERMISSION = "finance.compliance.einvoice.read"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """The scope context an automated filing runs under.

    `sub` is a stable, all-zero UUID rather than a random one, so every audit row the platform's
    own filing writes is attributable to "the platform" and not to twenty ids nobody can join.

    Lives here rather than in `app/api/internal.py` because both the internal route and the event
    consumer need it, and a consumer that imported a router would have grown a second HTTP surface
    by accident — which is exactly what the import-linter contract refuses.
    """
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{FILING_PERMISSION}:ORG"],
        entitlements=["finance"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=FILING_PERMISSION)


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _line_amount_minor(quantity: float, unit_price_minor: int) -> int:
    """A line's amount in minor units, rounded half-up once. Kept exact via Decimal so a
    fractional quantity cannot introduce a float error that stops the invoice footing."""
    exact = Decimal(str(quantity)) * Decimal(unit_price_minor)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def _checked_gstin(raw: str | None, *, whose: str) -> str | None:
    """Parse a GSTIN or refuse the write, naming what is wrong with it.

    None passes through: an unregistered buyer legitimately has none. What does **not** pass is a
    number that is merely 15 characters long. A GSTIN with a bad check digit is accepted by every
    text field in the world and rejected by the one system that matters, months later, as a return
    mismatch — so it is refused here, at the only moment a person is still looking at it.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return domain.parse_gstin(raw).value
    except domain.GstinError as exc:
        raise BusinessRuleProblem(f"{whose} GSTIN is not valid: {exc}") from exc


def _checked_rate(rate: float | Decimal, *, where: str) -> Decimal:
    """A GST rate, checked against the statutory bands.

    A rate that is not a band is either a typo or a jurisdiction this service does not model, and
    both are better refused than filed. Cess and the 0.25/3 % bands are in `GST_RATE_BANDS`.
    """
    value = Decimal(str(rate))
    if value not in domain.GST_RATE_BANDS:
        bands = ", ".join(str(b) for b in sorted(domain.GST_RATE_BANDS))
        raise BusinessRuleProblem(f"{where}: {value} % is not a GST rate band ({bands})")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def _today(today: date | None) -> date:
    """The real date this write reasons against, unless a test injected one.

    Kept as a tiny function rather than inlined `today or _now().date()` everywhere so every
    call site reads the same way and a later per-organization timezone is one place to change.
    """
    return today if today is not None else _now().date()


def _check_withholding_exclusion(deducts_tds_194q: bool, tcs_applicable: bool) -> None:
    """194Q and 206C(1H) are mutually exclusive by statute (`models.py:111-116`) — a buyer is
    never liable to deduct income tax on a supply and also have TCS collected from them on it."""
    if deducts_tds_194q and tcs_applicable:
        raise BusinessRuleProblem(
            "A customer cannot both deduct TDS under 194Q and be charged TCS under 206C(1H) — "
            "the two are mutually exclusive by statute"
        )


# --------------------------------------------------------------------- customers


async def create_customer(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CustomerCreate,
    *,
    request: Request | None = None,
) -> Customer:
    """Add a billed party. A GSTIN, if given, is parsed and checksum-verified before it is
    stored — this service holds no unverified GSTIN anywhere."""
    gstin = _checked_gstin(payload.gstin, whose="The customer's")
    _check_withholding_exclusion(payload.deducts_tds_194q, payload.tcs_applicable)
    payload = payload.model_copy(update={"gstin": gstin})
    customer = Customer(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=payload.name,
        contact_name=payload.contact_name,
        email=payload.email,
        gstin=payload.gstin,
        city=payload.city,
        state=payload.state,
        # Facts about the buyer that decide whether tax is withheld on what this organization
        # bills them. All three default false, which is what keeps withholding opt-in.
        is_tds_deductor=payload.is_tds_deductor,
        deducts_tds_194q=payload.deducts_tds_194q,
        tcs_applicable=payload.tcs_applicable,
    )
    session.add(customer)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="customer.created",
        entity="customer",
        entity_id=customer.id,
        after={"name": customer.name, "gstin": customer.gstin},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CUSTOMER_CREATED,
        aggregate_type="customer",
        aggregate_id=customer.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "customer_id": str(customer.id),
            "name": customer.name,
        },
        actor=_actor(ctx),
    )
    return customer


async def update_customer(
    session: AsyncSession,
    ctx: ScopeContext,
    customer: Customer,
    payload: CustomerUpdate,
    *,
    request: Request | None = None,
) -> Customer:
    """Edit a billed party. Only what was sent is applied; a GSTIN, if sent, is re-verified.

    The only mutable billing entity by design (`finance.md`): invoices snapshot `buyer_gstin` and
    `place_of_supply` at finalize, so editing a customer never rewrites an already-issued
    document — the withholding flags in particular affect **future** invoices only.
    """
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return customer

    before = {
        "name": customer.name,
        "contact_name": customer.contact_name,
        "email": customer.email,
        "gstin": customer.gstin,
        "city": customer.city,
        "state": customer.state,
        "is_tds_deductor": customer.is_tds_deductor,
        "deducts_tds_194q": customer.deducts_tds_194q,
        "tcs_applicable": customer.tcs_applicable,
    }

    if "gstin" in changes:
        changes["gstin"] = _checked_gstin(changes["gstin"], whose="The customer's")

    deducts_194q = changes.get("deducts_tds_194q", customer.deducts_tds_194q)
    tcs = changes.get("tcs_applicable", customer.tcs_applicable)
    _check_withholding_exclusion(deducts_194q, tcs)

    for field, value in changes.items():
        setattr(customer, field, value)
    customer.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "name": customer.name,
        "contact_name": customer.contact_name,
        "email": customer.email,
        "gstin": customer.gstin,
        "city": customer.city,
        "state": customer.state,
        "is_tds_deductor": customer.is_tds_deductor,
        "deducts_tds_194q": customer.deducts_tds_194q,
        "tcs_applicable": customer.tcs_applicable,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="customer.updated",
        entity="customer",
        entity_id=customer.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CUSTOMER_UPDATED,
        aggregate_type="customer",
        aggregate_id=customer.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "customer_id": str(customer.id),
            "before": before,
            "after": after,
        },
        actor=_actor(ctx),
    )
    return customer


async def archive_customer(
    session: AsyncSession,
    ctx: ScopeContext,
    customer: Customer,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a customer. Refused with 409 while any invoice or payment names them.

    Invoices are append-only and can never be reassigned to another customer, so a customer with
    billing history can never be safely detached from it — archiving them would leave that
    history pointing at a party the directory no longer lists. The refusal is unconditional
    (any invoice or payment, not only an unpaid one): a *paid* invoice is still evidence naming
    this customer, and the receivable book's own history is exactly what an auditor asks the
    directory to still explain.
    """
    if await selectors.customer_has_references(session, ctx.organization_id, customer.id):
        raise ConflictProblem(
            "This customer cannot be archived: at least one invoice or payment still names them"
        )
    customer.deleted_at = _now()
    customer.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="customer.archived",
        entity="customer",
        entity_id=customer.id,
        before={"deleted_at": None},
        after={"deleted_at": customer.deleted_at.isoformat()},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CUSTOMER_ARCHIVED,
        aggregate_type="customer",
        aggregate_id=customer.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "customer_id": str(customer.id),
        },
        actor=_actor(ctx),
    )


# --------------------------------------------------------------------- invoices


async def create_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: InvoiceCreate,
    *,
    request: Request | None = None,
    today: date | None = None,
) -> Invoice:
    """Finalize an invoice or credit note. Totals foot exactly; the document is frozen after.

    GST is decided here, once, and then frozen with everything else:

    * the **place of supply** and **supply type** are derived from the buyer when the caller does
      not state them — the buyer's GSTIN state, else their recorded state, else the seller's own;
    * the **split** is computed per line, at each line's own rate, and summed upward. It is never
      apportioned downward from a document total, because a GSTR-1 return and an e-invoice both
      declare the line-level figures and the portal recomputes them;
    * both **GSTINs are snapshotted**, so the document keeps naming the numbers it was raised
      under even after a party re-registers.

    A credit note must name what it corrects, correct an `invoice` (never another credit note)
    belonging to the **same customer**, and not exceed what remains correctable on it — the sum
    of every credit note already issued against that invoice, plus this one, capped at the
    original total. An ordinary invoice must total more than zero; a credit note, less.
    """
    if payload.document_type not in domain.DOCUMENT_TYPES:
        raise BusinessRuleProblem(f"Unknown document type {payload.document_type!r}")
    if payload.status is not None and payload.status not in domain.INVOICE_STATUSES:
        raise BusinessRuleProblem(f"Unknown invoice status {payload.status!r}")
    if payload.supply_type is not None and payload.supply_type not in domain.SUPPLY_TYPES:
        raise BusinessRuleProblem(f"Unknown supply type {payload.supply_type!r}")
    if payload.due_date < payload.issued_date:
        raise BusinessRuleProblem("The due date cannot be before the issued date")
    if await selectors.number_exists(session, ctx.organization_id, payload.number):
        raise ConflictProblem(f"An invoice numbered {payload.number!r} already exists")
    customer = await selectors.get_customer(
        session, ctx.organization_id, payload.customer_id, for_update=True
    )
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")

    corrected: Invoice | None = None
    if payload.document_type == "credit_note":
        if payload.corrects_invoice_id is None:
            raise BusinessRuleProblem("A credit note must state which invoice it corrects")
        # Locked before `already_credited` is read below: see `get_invoice`.
        corrected = await selectors.get_invoice(
            session, ctx.organization_id, payload.corrects_invoice_id, for_update=True
        )
        if corrected is None:
            raise NotFoundProblem("The invoice this credit note corrects was not found")
        if corrected.document_type != "invoice":
            raise BusinessRuleProblem(
                "A credit note may only correct an invoice, not another credit note"
            )
        if corrected.customer_id != payload.customer_id:
            raise BusinessRuleProblem(
                "A credit note must correct an invoice belonging to the same customer"
            )
    elif payload.corrects_invoice_id is not None:
        raise BusinessRuleProblem("Only a credit note may state what it corrects")

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    seller_gstin = registration.gstin if registration else None
    seller_state = registration.state_code if registration else ""
    buyer_gstin = customer.gstin

    supply_type = payload.supply_type or ("b2b" if buyer_gstin else "b2c")
    place_of_supply = _place_of_supply(payload, customer, seller_state)
    if place_of_supply is not None and place_of_supply not in domain.GST_STATE_CODES:
        raise BusinessRuleProblem(f"{place_of_supply!r} is not a GST state code")

    intra_state = place_of_supply is not None and domain.is_intra_state(
        supply_type=supply_type,
        seller_state_code=seller_state,
        place_of_supply=place_of_supply,
    )

    document_rate = _checked_rate(payload.tax_rate_pct, where="Document tax rate")

    lines: list[InvoiceLine] = []
    subtotal = 0
    tax_split = domain.ZERO_TAX
    for i, line in enumerate(payload.lines, start=1):
        amount = _line_amount_minor(line.quantity, line.unit_price_minor)
        subtotal += amount
        rate = (
            document_rate
            if line.tax_rate_pct is None
            else _checked_rate(line.tax_rate_pct, where=f"Line {i} tax rate")
        )
        try:
            hsn = domain.normalise_hsn(line.hsn_sac)
        except ValueError as exc:
            raise BusinessRuleProblem(f"Line {i}: {exc}") from exc
        # Reverse charge moves the liability to the recipient: the supplier states the rate and
        # charges nothing, so the split is zero rather than absent.
        split = (
            domain.ZERO_TAX
            if payload.reverse_charge
            else domain.split_gst(amount, rate, intra_state=intra_state)
        )
        tax_split = tax_split + split
        lines.append(
            InvoiceLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                line_no=i,
                description=line.description,
                quantity=Decimal(str(line.quantity)),
                unit_price_minor=line.unit_price_minor,
                amount_minor=amount,
                hsn_sac=hsn,
                tax_rate_pct=rate,
                cgst_minor=split.cgst_minor,
                sgst_minor=split.sgst_minor,
                igst_minor=split.igst_minor,
            )
        )

    tax_rate = document_rate
    tax = tax_split.total_minor
    total = subtotal + tax

    if payload.document_type == "credit_note":
        if total >= 0:
            raise BusinessRuleProblem("A credit note must total less than zero")
        assert corrected is not None  # narrowed above: credit_note always resolves `corrected`
        already_credited = await selectors.credited_total_for_invoice(
            session, ctx.organization_id, corrected.id
        )
        remaining = corrected.total_minor - already_credited
        if -total > remaining:
            raise BusinessRuleProblem(
                f"This credit note ({-total} minor units) exceeds what remains correctable on "
                f"{corrected.number} ({remaining} minor units already uncredited)"
            )
    elif total <= 0:
        raise BusinessRuleProblem("An invoice must total more than zero")

    status = payload.status or domain.derive_invoice_status(
        document_type=payload.document_type,
        status_override=None,
        due_date=payload.due_date,
        total_minor=total,
        paid_minor=0,
        today=_today(today),
    )

    invoice = Invoice(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=payload.number,
        document_type=payload.document_type,
        customer_id=payload.customer_id,
        issued_date=payload.issued_date,
        due_date=payload.due_date,
        status=status,
        currency=payload.currency,
        subtotal_minor=subtotal,
        tax_rate_pct=tax_rate,
        tax_minor=tax,
        total_minor=total,
        notes=payload.notes,
        corrects_invoice_id=payload.corrects_invoice_id,
        place_of_supply=place_of_supply,
        supply_type=supply_type,
        reverse_charge=payload.reverse_charge,
        seller_gstin=seller_gstin,
        buyer_gstin=buyer_gstin,
        cgst_minor=tax_split.cgst_minor,
        sgst_minor=tax_split.sgst_minor,
        igst_minor=tax_split.igst_minor,
        lines=lines,
    )
    session.add(invoice)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="invoice.issued",
        entity="invoice",
        entity_id=invoice.id,
        after={
            "number": invoice.number,
            "document_type": invoice.document_type,
            "status": invoice.status,
            "total_minor": invoice.total_minor,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_ISSUED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "document_type": invoice.document_type,
            "customer_id": str(invoice.customer_id),
            "status": invoice.status,
            "total_minor": invoice.total_minor,
            "currency": invoice.currency,
            # Carried so an auto-filing agent can decide from the event alone whether this
            # document needs an IRN, without reading the invoice back.
            "supply_type": invoice.supply_type,
            "place_of_supply": invoice.place_of_supply,
            "requires_einvoice": domain.requires_einvoice(
                supply_type=invoice.supply_type, document_type=invoice.document_type
            ),
        },
        actor=_actor(ctx),
    )

    # Auto-filing starts here, and it starts as a *row*. Staged on the outbox inside this same
    # transaction, the request is sent by the relay only after the invoice has committed — so
    # there is no window in which the platform is filing a document that does not yet exist, and
    # none in which a rolled-back invoice has left a filing request behind. That property is the
    # entire reason this is not a call.
    if domain.should_auto_file(
        supply_type=invoice.supply_type,
        document_type=invoice.document_type,
        status=invoice.status,
    ):
        request_filing(session, ctx, invoice, trigger="finalized")

    # Withholding is assessed inline rather than on the outbox, and the difference from filing is
    # the point: filing calls a government portal, so it must not run inside the request that
    # raised the invoice, while this is pure arithmetic over rows already in this transaction. It
    # belongs to the document — the figure a customer will pay *net of* is part of finalizing it,
    # not something that arrives later and changes what was already sent.
    await assess_withholdings(session, ctx, invoice, customer, request=request)
    return invoice


#: `invoice` -> `INV`, `credit_note` -> `CN`. Two independent series per organization per
#: financial year, so a credit note never contends with an invoice for the next number.
_NUMBER_PREFIXES: dict[str, str] = {"invoice": "INV", "credit_note": "CN"}


async def allocate_invoice_number(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    document_type: str,
    on: date,
) -> str:
    """Mint the next number in this organization's own series — race-safe, server-owned.

    The principal `POST /finance/invoices` route never accepts a client-supplied number (that
    field stays on `InvoiceCreate`, the internal contract the demo seed drives with exact
    historical numbers). A real caller gets this instead: one row per
    (organization, series, financial year), incremented with a single atomic
    `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` rather than a read-then-write — two requests
    racing for the same organization serialize on that row's own lock rather than on an advisory
    lock this function would otherwise have to remember to take and release.
    """
    series = _NUMBER_PREFIXES.get(document_type)
    if series is None:
        raise BusinessRuleProblem(f"Unknown document type {document_type!r}")
    financial_year = domain.financial_year(on)
    stmt = (
        pg_insert(InvoiceNumberSeries)
        .values(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            series=document_type,
            financial_year=financial_year,
            next_seq=2,
        )
        .on_conflict_do_update(
            index_elements=[
                InvoiceNumberSeries.organization_id,
                InvoiceNumberSeries.series,
                InvoiceNumberSeries.financial_year,
            ],
            set_={
                "next_seq": InvoiceNumberSeries.next_seq + 1,
                "updated_by": str(ctx.principal.sub),
            },
        )
        .returning(InvoiceNumberSeries.next_seq)
    )
    next_after = (await session.execute(stmt)).scalar_one()
    seq = int(next_after) - 1
    return f"{series}-{financial_year}-{seq:05d}"


def _place_of_supply(payload: InvoiceCreate, customer: Customer, seller_state: str) -> str | None:
    """Where the supply is taxed, in order of authority.

    An explicit value from the caller wins. Otherwise the buyer's GSTIN names their state
    unambiguously, so it is preferred over the free-text `state` on the customer row — which is
    the fallback, and is mapped rather than guessed. Last, the seller's own state, which makes an
    otherwise unclassifiable supply intra-state: the conservative direction, because CGST+SGST
    wrongly charged is corrected by a credit note, while IGST wrongly charged is a refund claim.

    None only when the org has configured no registration and nothing else resolves.
    """
    if payload.place_of_supply:
        return payload.place_of_supply
    if customer.gstin:
        try:
            return domain.parse_gstin(customer.gstin).state_code
        except domain.GstinError:
            # Stored GSTINs are validated on write, so this is unreachable for rows this
            # service created. It is caught rather than raised because a document must still be
            # raisable against a customer imported before validation existed.
            pass
    from_name = domain.state_code_for_name(customer.state)
    if from_name:
        return from_name
    return seller_state or None


# --------------------------------------------------------------------- payments


async def record_payment(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: PaymentCreate,
    *,
    request: Request | None = None,
    today: date | None = None,
) -> Payment:
    """Record a payment received, split across one or more invoices — an append-only fact.

    Every allocation is checked against the invoice it names, not just the payload's own
    arithmetic: the invoice must be an `invoice` (never a credit note), open (`sent`/`overdue`),
    belong to the *paying* customer, appear at most once in this payment, and take no more than
    what is still outstanding on it — its total less every allocation already recorded against it,
    batched in one query rather than one per allocation.

    `invoices.status` is moved to `paid` here, in this same transaction, whenever an invoice's
    allocations reach its total — a projection of the payment history kept as a column for the
    same reason `ims_documents.status` is (`services.record_ims_decision`): the queue's access
    path needs it indexed, not derived by a lateral join on every list page. Nothing else ever
    writes it back down from `paid`.
    """
    if payload.method not in domain.PAYMENT_METHODS:
        raise BusinessRuleProblem(f"Unknown payment method {payload.method!r}")
    resolved_today = _today(today)
    if payload.received_date > resolved_today:
        raise BusinessRuleProblem("A payment's received date cannot be in the future")
    if await selectors.reference_exists(session, ctx.organization_id, payload.reference):
        raise ConflictProblem(f"A payment referenced {payload.reference!r} already exists")
    customer = await selectors.get_customer(
        session, ctx.organization_id, payload.customer_id, for_update=True
    )
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")

    invoice_ids = [alloc.invoice_id for alloc in payload.allocations]
    if len(set(invoice_ids)) != len(invoice_ids):
        raise BusinessRuleProblem("An invoice may not be allocated twice in the same payment")

    # Row-locked, in id order, before the already-applied figure is read: see `get_invoices`.
    invoices = await selectors.get_invoices(
        session, ctx.organization_id, invoice_ids, for_update=True
    )
    already_paid = await selectors.paid_by_invoice(session, ctx.organization_id, invoice_ids)

    allocations: list[PaymentAllocation] = []
    amount = 0
    # (invoice, its paid-so-far figure *after* this payment) — resolved after every allocation
    # validates, so a rejection on line three of five leaves nothing half-applied.
    settled: list[tuple[Invoice, int]] = []
    running_paid: dict[uuid.UUID, int] = dict(already_paid)
    for alloc in payload.allocations:
        if alloc.amount_minor <= 0:
            raise BusinessRuleProblem("An allocation must be a positive amount")
        invoice = invoices.get(alloc.invoice_id)
        if invoice is None:
            raise NotFoundProblem("An allocated invoice is not part of this organization")
        if invoice.document_type != "invoice":
            raise BusinessRuleProblem(
                f"{invoice.number} is a credit note; a payment may only be allocated to an invoice"
            )
        if invoice.customer_id != payload.customer_id:
            raise BusinessRuleProblem(
                f"{invoice.number} does not belong to the customer this payment names"
            )
        if invoice.status not in ("sent", "overdue"):
            raise BusinessRuleProblem(
                f"{invoice.number} is not open for payment (status {invoice.status!r})"
            )
        outstanding = invoice.total_minor - running_paid.get(invoice.id, 0)
        if alloc.amount_minor > outstanding:
            raise BusinessRuleProblem(
                f"{invoice.number} has only {outstanding} minor units outstanding; "
                f"{alloc.amount_minor} was allocated"
            )
        running_paid[invoice.id] = running_paid.get(invoice.id, 0) + alloc.amount_minor
        amount += alloc.amount_minor
        allocations.append(
            PaymentAllocation(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                invoice_id=alloc.invoice_id,
                amount_minor=alloc.amount_minor,
            )
        )
        settled.append((invoice, running_paid[invoice.id]))

    payment = Payment(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        reference=payload.reference,
        customer_id=payload.customer_id,
        received_date=payload.received_date,
        method=payload.method,
        amount_minor=amount,
        note=payload.note,
        allocations=allocations,
    )
    session.add(payment)
    await session.flush()

    for invoice, new_paid in settled:
        if new_paid >= invoice.total_minor and invoice.status != "paid":
            before_status = invoice.status
            invoice.status = "paid"
            invoice.updated_by = str(ctx.principal.sub)
            record_audit(
                session,
                AuditLog,
                organization_id=ctx.organization_id,
                actor_id=ctx.principal.sub,
                action="invoice.paid",
                entity="invoice",
                entity_id=invoice.id,
                before={"status": before_status},
                after={"status": "paid", "amount_paid_minor": new_paid},
                request=request,
            )
            publish_event(
                session,
                OutboxEvent,
                organization_id=ctx.organization_id,
                event_type=EVT_INVOICE_PAID,
                aggregate_type="invoice",
                aggregate_id=invoice.id,
                payload={
                    "organization_id": str(ctx.organization_id),
                    "invoice_id": str(invoice.id),
                    "number": invoice.number,
                    "payment_id": str(payment.id),
                    "amount_paid_minor": new_paid,
                },
                actor=_actor(ctx),
            )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="payment.received",
        entity="payment",
        entity_id=payment.id,
        after={
            "reference": payment.reference,
            "amount_minor": payment.amount_minor,
            "allocations": len(allocations),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PAYMENT_RECEIVED,
        aggregate_type="payment",
        aggregate_id=payment.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "payment_id": str(payment.id),
            "reference": payment.reference,
            "customer_id": str(payment.customer_id),
            "amount_minor": payment.amount_minor,
            "allocations": [
                {"invoice_id": str(a.invoice_id), "amount_minor": a.amount_minor}
                for a in allocations
            ],
        },
        actor=_actor(ctx),
    )
    return payment


# --------------------------------------------------------------------- GST registration


async def set_gst_registration(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: GstRegistrationUpsert,
    *,
    request: Request | None = None,
) -> GstRegistration:
    """Register (or correct) this organization's own GSTIN.

    The number is parsed before anything is written, so the row's `state_code` is *derived from
    the GSTIN itself* rather than entered alongside it — the two can never disagree, which they
    otherwise eventually do, and the tax split reads `state_code` on every invoice.

    Marking a registration primary demotes any other, in the same transaction: the partial unique
    index means the demotion is not optional, it is the only way the insert succeeds.
    """
    try:
        parsed = domain.parse_gstin(payload.gstin)
    except domain.GstinError as exc:
        raise BusinessRuleProblem(f"That is not a valid GSTIN: {exc}") from exc
    if payload.registration_type not in ("regular", "composition", "casual"):
        raise BusinessRuleProblem(f"Unknown registration type {payload.registration_type!r}")

    existing = await selectors.gst_registration_by_gstin(session, ctx.organization_id, parsed.value)
    if payload.is_primary:
        for other in await selectors.all_gst_registrations(session, ctx.organization_id):
            if other.gstin != parsed.value and other.is_primary:
                other.is_primary = False
        await session.flush()

    if existing is not None:
        before = {"legal_name": existing.legal_name, "is_primary": existing.is_primary}
        existing.legal_name = payload.legal_name
        existing.trade_name = payload.trade_name
        existing.registration_type = payload.registration_type
        existing.is_primary = payload.is_primary
        existing.updated_by = str(ctx.principal.sub)
        registration = existing
    else:
        before = {}
        registration = GstRegistration(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            gstin=parsed.value,
            state_code=parsed.state_code,
            legal_name=payload.legal_name,
            trade_name=payload.trade_name,
            registration_type=payload.registration_type,
            is_primary=payload.is_primary,
        )
        session.add(registration)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="gst_registration.set",
        entity="gst_registration",
        entity_id=registration.id,
        before=before or None,
        after={
            "gstin": registration.gstin,
            "state_code": registration.state_code,
            "legal_name": registration.legal_name,
            "is_primary": registration.is_primary,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GST_REGISTRATION_SET,
        aggregate_type="gst_registration",
        aggregate_id=registration.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "gst_registration_id": str(registration.id),
            "gstin": registration.gstin,
            "state_code": registration.state_code,
            "is_primary": registration.is_primary,
        },
        actor=_actor(ctx),
    )
    return registration


# --------------------------------------------------------------------- portal calls, off the loop
# Every portal client is synchronous (a `Protocol` of plain methods, `app/irp/base.py` and
# siblings), and every caller is a coroutine. Calling one directly from a route, the consumer or
# the sweep would block the event loop for as long as the portal took — every other request on
# the process with it. So the second act of every claim/call/record split runs on a worker thread,
# bounded by `Settings.portal_call_timeout_seconds` (D3).


async def off_loop[T](fn: Callable[..., T], *args: Any, on_timeout: Callable[[float], T]) -> T:
    """Run `fn(*args)` on a worker thread and wait at most `portal_call_timeout_seconds` for it.

    A call that outlives the timeout is answered by `on_timeout(seconds)` — an outage outcome for
    a filing, an upstream failure for an IMS fetch — and the thread is abandoned rather than
    cancelled (a thread cannot be). That is safe for exactly the reason the claim/call/record
    split is: the claim stands, the provider call is idempotent, and whatever the abandoned call
    eventually achieved is found again by the next attempt.
    """
    timeout = settings().portal_call_timeout_seconds
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError:
        return on_timeout(timeout)


def _timed_out(seconds: float) -> str:
    return f"the portal did not answer within {seconds:g} seconds"


# --------------------------------------------------------------------- e-invoicing
# The state machine, and the only place `einvoice_filings` is written. Every path through here
# ends in exactly one appended row: a success, a refusal, or an outage. There is no path that
# calls the portal and records nothing — that is the failure mode this table exists to prevent,
# because an attempt nobody recorded is an attempt nobody can prove was made.


def build_irp_payload(
    invoice: Invoice, registration: GstRegistration, *, buyer_legal_name: str, at: datetime
) -> IrpInvoicePayload:
    """Assemble the document the portal reasons about, from a frozen invoice.

    A pure mapping: everything it needs was snapshotted onto the invoice at finalize, so the
    payload for a document filed today and the payload for the same document re-filed in three
    years are identical — which is what makes the IRN stable and a retry safe.
    """
    return IrpInvoicePayload(
        document_type=domain.IRP_DOCUMENT_TYPES[invoice.document_type],
        document_number=invoice.number,
        document_date=invoice.issued_date,
        supply_type=invoice.supply_type,
        reverse_charge=invoice.reverse_charge,
        seller_gstin=invoice.seller_gstin or registration.gstin,
        seller_legal_name=registration.legal_name,
        seller_state_code=registration.state_code,
        buyer_gstin=invoice.buyer_gstin,
        buyer_legal_name=buyer_legal_name,
        place_of_supply=invoice.place_of_supply or "",
        taxable_minor=invoice.subtotal_minor,
        cgst_minor=invoice.cgst_minor,
        sgst_minor=invoice.sgst_minor,
        igst_minor=invoice.igst_minor,
        total_minor=invoice.total_minor,
        financial_year=domain.financial_year(invoice.issued_date),
        generated_at=at,
        lines=[
            IrpLine(
                serial_no=line.line_no,
                description=line.description,
                hsn_sac=line.hsn_sac,
                quantity=line.quantity,
                unit_price_minor=line.unit_price_minor,
                taxable_minor=line.amount_minor,
                rate_pct=line.tax_rate_pct,
                cgst_minor=line.cgst_minor,
                sgst_minor=line.sgst_minor,
                igst_minor=line.igst_minor,
            )
            for line in invoice.lines
        ],
    )


def _append_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    *,
    status: str,
    at: datetime,
    irn: str | None = None,
    ack_no: str | None = None,
    ack_date: datetime | None = None,
    qr_payload: str | None = None,
    error_code: str | None = None,
    error: str | None = None,
) -> EInvoiceFiling:
    """Append one attempt. The only writer of this table, and it only ever inserts."""
    if status not in domain.FILING_STATUSES:
        raise BusinessRuleProblem(f"Unknown filing status {status!r}")
    filing = EInvoiceFiling(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        invoice_id=invoice.id,
        status=status,
        irn=irn,
        ack_no=ack_no,
        ack_date=ack_date,
        qr_payload=qr_payload,
        error_code=error_code,
        error=error,
        attempted_at=at,
    )
    session.add(filing)
    return filing


def live_registration(filings: list[EInvoiceFiling]) -> EInvoiceFiling | None:
    """The registration that currently stands for a document: its newest `filed` attempt.

    **The idempotency guard, in one place.** A document already carrying an IRN must never be sent
    again — the portal answers a second registration of the same document with `2150 Duplicate
    IRN`, and an integration that discovers that by asking is an integration that files every
    invoice twice under load. Both the manual route and the automatic consumer ask this before
    they call, so the two can never disagree about what "already filed" means.

    A cancellation deliberately does **not** clear it. Once an IRN has been issued for a document
    number in a financial year it cannot be issued again — the IRN is a hash of exactly those
    fields — so a withdrawn document is corrected by raising a new one, not by re-filing this one.
    `filings` is newest-first, as `selectors.filings_for_invoice` returns it.
    """
    return next((f for f in filings if f.status == "filed"), None)


def _after_newest(at: datetime, rows: list[Any]) -> datetime:
    """The instant to stamp an outcome row with: the attempt's own clock, but never earlier than
    the newest row already on the ledger. A late answer — a request whose portal call outlived the
    sweep's lease — must not sort *behind* a row the sweep wrote meanwhile (`rows` is newest
    first), or the read model would report the older fact as current."""
    return max(at, rows[0].attempted_at) if rows else at


def _filing_idempotency_key(
    invoice_id: uuid.UUID, attempt: int, *, suffix: str | None = None
) -> str:
    base = f"einvoice-filing:{invoice_id}:{attempt}"
    return f"{base}:{suffix}" if suffix else base


def _dispatch_filing_request(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    invoice_id: uuid.UUID,
    attempt: int,
    not_before: datetime | None,
    payload: dict[str, Any],
    now: datetime | None = None,
    idempotency_suffix: str | None = None,
) -> None:
    """Publish the filing-request payload now, or ask the scheduler to hold it (ADR-0015).

    `not_before` in the past or absent publishes directly — byte-identical to what this function
    did before the scheduler existed. `not_before` in the future stages
    `platform.schedule.requested` instead, on this same outbox, keyed so a redelivered schedule
    request can never produce two deliveries of the same retry.
    """
    now = now or _now()
    if not_before is not None and not_before > now:
        schedule_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            due_at=not_before,
            idempotency_key=_filing_idempotency_key(invoice_id, attempt, suffix=idempotency_suffix),
            target_event_type=EVT_INVOICE_FILING_REQUESTED,
            target_aggregate_type="invoice",
            target_aggregate_id=invoice_id,
            target_payload=payload,
            target_actor=_actor(ctx),
        )
        return
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_FILING_REQUESTED,
        aggregate_type="invoice",
        aggregate_id=invoice_id,
        payload=payload,
        actor=_actor(ctx),
    )


def request_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    *,
    attempt: int = 1,
    not_before: datetime | None = None,
    trigger: str,
    now: datetime | None = None,
) -> None:
    """Stage a filing attempt — inside the caller's transaction, never as a call.

    Used twice: once when a document is finalized, and again by the consumer to schedule a bounded
    retry after a portal outage. `attempt` is 1-based and rides on the payload rather than being
    counted from the filing ledger, because the ledger records what happened to the *document* and
    the budget belongs to this *chain of attempts* — a manual re-file should not consume the
    automation's remaining tries, and reading a count back would make it do exactly that.

    `not_before` says when the retry becomes due. A null or already-past `not_before` publishes
    `EVT_INVOICE_FILING_REQUESTED` directly, exactly as before the scheduler existed — the
    finalize trigger never sets it, and a retry whose backoff has already elapsed by the time this
    transaction runs gains nothing from waiting further. A future `not_before` instead asks the
    scheduler to hold it (`_dispatch_filing_request`, ADR-0015) — the delivery change the retry's
    own backoff was always waiting for.
    """
    payload = {
        "organization_id": str(ctx.organization_id),
        "invoice_id": str(invoice.id),
        "number": invoice.number,
        "document_type": invoice.document_type,
        "supply_type": invoice.supply_type,
        "attempt": attempt,
        "max_attempts": domain.MAX_FILING_ATTEMPTS,
        "not_before": not_before.isoformat() if not_before else None,
        #: What caused this request — `finalized` for the first, `retry` for the rest.
        "trigger": trigger,
    }
    _dispatch_filing_request(
        session,
        ctx,
        invoice_id=invoice.id,
        attempt=attempt,
        not_before=not_before,
        payload=payload,
        now=now,
    )


def rearm_early_filing_request(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    invoice_id: uuid.UUID,
    event_id: uuid.UUID,
    attempt: int,
    not_before: datetime,
    payload: dict[str, Any],
) -> None:
    """Defence in depth for `app/consumers.py:file_requested_invoice` (ADR-0015 §5): a
    `filing_requested` message that names a `not_before` still in the future, arriving anyway —
    this should never happen in normal operation, since the scheduler is what is supposed to hold
    it, so this only exists to guard against a hand-redelivered message, a DLQ replay, or a
    scheduler that fired on a misconfigured clock.

    Re-arms the wait for what remains rather than acting on it, silently dropping it (loses the
    retry) or nacking it (RabbitMQ has no delay of its own, so this hot-loops the exact bug the
    scheduler exists to fix). Keyed off **this message's own event id** rather than
    `(invoice, attempt)` — that key was already spent by whichever schedule produced this very
    message (this firing *is* that schedule), and reusing it would collide with a job that has
    already fired instead of arming a fresh wait.

    Takes `invoice_id` and the arriving message's own `payload` rather than the `Invoice` row and
    rebuilding one: this message already carries everything a fresh request needs, and refetching
    the invoice here would be a query this defensive, hopefully-never-taken path does not need.
    """
    _dispatch_filing_request(
        session,
        ctx,
        invoice_id=invoice_id,
        attempt=attempt,
        not_before=not_before,
        payload=payload,
        idempotency_suffix=f"early-arrival:{event_id}",
    )


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One trip to the portal and the ledger row it left behind.

    `outcome` is `filed`, `rejected` or `unavailable` — the three things a portal can do to a
    well-formed request. The event each one deserves differs between the manual and automatic
    paths, so the events are published by the callers; what must not differ is the row, so the row
    is written here and only here.
    """

    filing: EInvoiceFiling
    outcome: str
    ack: IrpAck | None = None
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _RegisterCallOutcome:
    """What `call_irp_register` decided, entirely from the portal's answer — no session, no
    database. `record_registration_result` is the only thing that turns this into a row."""

    kind: str  # "filed" | "rejected" | "unavailable"
    ack: IrpAck | None = None
    error_code: str | None = None
    error: str | None = None


def call_irp_register(client: IrpClient, irp_payload: IrpInvoicePayload) -> _RegisterCallOutcome:
    """The second of three acts filing a document takes, and the only one that touches the
    portal. Called with **no session and no transaction open** (D3): `stage_registration_attempt`
    has already committed everything the call needs to be answered from, and
    `record_registration_result` opens a fresh transaction only after this returns — so a slow or
    hanging portal holds nothing from this service's connection pool.

    `IrpDuplicate` is folded into `"filed"` rather than kept as its own outcome: it is the
    portal's honest answer to a registration this platform already made — the resumed half of a
    call whose first attempt crashed before recording it, or a genuine race with a manual re-file
    — and the ack it carries is exactly as good as one from a fresh registration.
    """
    try:
        ack = client.register(irp_payload)
    except IrpDuplicate as duplicate:
        return _RegisterCallOutcome(kind="filed", ack=duplicate.ack)
    except IrpRejected as rejected:
        return _RegisterCallOutcome(
            kind="rejected", error_code=rejected.code, error=rejected.message
        )
    except IrpUnavailable as outage:
        return _RegisterCallOutcome(kind="unavailable", error=str(outage))
    return _RegisterCallOutcome(kind="filed", ack=ack)


async def call_irp_register_off_loop(
    client: IrpClient, irp_payload: IrpInvoicePayload
) -> _RegisterCallOutcome:
    """`call_irp_register` on a worker thread, bounded (`off_loop`); a timeout is an outage."""
    return await off_loop(
        call_irp_register,
        client,
        irp_payload,
        on_timeout=lambda s: _RegisterCallOutcome(kind="unavailable", error=_timed_out(s)),
    )


async def stage_registration_attempt(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    registration: GstRegistration,
    *,
    at: datetime,
) -> IrpInvoicePayload:
    """The first of three acts: assemble the document the portal will see, and write **nothing**.

    Deliberately not a claim row (contrast payment's `stage_charge`, which does write one):
    registering a document is itself idempotent — the IRN is a pure function of the document,
    `IrpDuplicate` is how a real portal says so — so there is nothing a claim row would need to
    protect a retry from. A process that dies before `record_registration_result` runs leaves no
    row behind at all, and a resumed attempt (the retry queue, or `app/cron.py`'s filing-reclaim
    sweep) simply calls the portal again from scratch. See `app/domain.py`'s `FILING_STATUSES`.
    """
    customer = await selectors.get_customer(session, ctx.organization_id, invoice.customer_id)
    return build_irp_payload(
        invoice,
        registration,
        buyer_legal_name=customer.name if customer else "",
        at=at,
    )


async def record_registration_result(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    outcome: _RegisterCallOutcome,
    *,
    at: datetime,
    request: Request | None,
    retryable_outage: bool = True,
) -> _Attempt:
    """The third act: append exactly one row describing what the portal said, and audit it.

    There is no path through here that leaves the call unrecorded — that is the failure mode the
    ledger exists to prevent, because an attempt nobody recorded is an attempt nobody can prove
    was made. Never called with the claim's own transaction still open; see the callers in
    `app/consumers.py` and `app/api/internal.py`.

    `retryable_outage` is what the caller knows and this function does not: whether another try is
    coming. When it is, an outage is a `pending` attempt awaiting that retry; when the budget is
    spent, the same outage is a terminal `failed` row carrying `retry_exhausted`, because "we
    stopped trying" is a different fact from "we are still trying" and an operator has to be able
    to tell them apart at a glance.
    """
    if outcome.kind == "rejected":
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="failed",
            at=at,
            error_code=outcome.error_code,
            error=outcome.error,
        )
        await session.flush()
        _audit_filing(
            session, ctx, invoice, filing, action="einvoice.filing_failed", request=request
        )
        return _Attempt(
            filing=filing,
            outcome="rejected",
            error_code=outcome.error_code,
            error=outcome.error,
        )

    if outcome.kind == "unavailable":
        # The opposite of a rejection: the document is fine, the portal is not.
        exhausted = not retryable_outage
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="failed" if exhausted else "pending",
            at=at,
            error_code=(domain.ERR_RETRY_EXHAUSTED if exhausted else domain.ERR_PORTAL_UNAVAILABLE),
            error=(
                f"gave up after {domain.MAX_FILING_ATTEMPTS} attempts; the portal was last "
                f"unreachable with: {outcome.error}"
                if exhausted
                else outcome.error
            ),
        )
        await session.flush()
        _audit_filing(
            session,
            ctx,
            invoice,
            filing,
            action="einvoice.filing_failed" if exhausted else "einvoice.filing_pending",
            request=request,
        )
        return _Attempt(
            filing=filing,
            outcome="unavailable",
            error_code=filing.error_code,
            error=filing.error,
        )

    if outcome.kind != "filed" or outcome.ack is None:  # pragma: no cover - exhaustiveness
        raise BusinessRuleProblem(f"unknown IRP call outcome {outcome.kind!r}")
    ack = outcome.ack
    filing = _append_filing(
        session,
        ctx,
        invoice,
        status="filed",
        at=at,
        irn=ack.irn,
        ack_no=ack.ack_no,
        ack_date=ack.ack_date,
        qr_payload=ack.qr_payload,
    )
    await session.flush()
    _audit_filing(session, ctx, invoice, filing, action="einvoice.filed", request=request)
    return _Attempt(filing=filing, outcome="filed", ack=ack)


def _publish_filed(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    filing: EInvoiceFiling,
    ack: IrpAck,
    *,
    portal: str,
    **extra: Any,
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_FILED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "filing_id": str(filing.id),
            "number": invoice.number,
            "document_type": invoice.document_type,
            "irn": ack.irn,
            "ack_no": ack.ack_no,
            "ack_date": ack.ack_date.isoformat(),
            "portal": portal,
            **extra,
        },
        actor=_actor(ctx),
    )


def _publish_filing_failed(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    *,
    filing_id: uuid.UUID | None,
    error_code: str | None,
    error: str | None,
    retryable: bool,
    **extra: Any,
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_FILING_FAILED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "filing_id": str(filing_id) if filing_id else None,
            "number": invoice.number,
            "error_code": error_code,
            "error": error,
            #: Whether the identical document is worth sending again. A rejection is final until
            #: the document changes — retrying identical bytes is how an integration goes quiet
            #: and a deadline passes — while an outage is retryable and must be retried.
            "retryable": retryable,
            **extra,
        },
        actor=_actor(ctx),
    )


@dataclass(frozen=True, slots=True)
class ManualFilingClaim:
    """What `claim_manual_filing` commits before the portal is asked: the invoice this attempt is
    against and the payload built from it. `call_irp_register` and `record_manual_filing` are the
    second and third acts; see `stage_registration_attempt` for why the claim itself writes
    nothing."""

    invoice: Invoice
    irp_payload: IrpInvoicePayload
    #: The clock reading this claim was made against — resolved once, reused by
    #: `record_manual_filing` so the payload and the appended row agree on when the attempt
    #: happened, the same reasoning `AutofileClaim.at` documents.
    at: datetime


async def claim_manual_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: FileInvoiceIn,
    *,
    at: datetime | None = None,
) -> ManualFilingClaim:
    """The first of three acts the manual filing route takes. Every refusal that does not need
    the portal at all — an unknown invoice, a document that is not reportable, no registration
    configured, one already registered — is raised here, exactly as `file_invoice` has always
    raised them; a caller who asked for something impossible deserves to be told, not handed a
    row.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, payload.invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")

    if not domain.requires_einvoice(
        supply_type=invoice.supply_type, document_type=invoice.document_type
    ):
        raise BusinessRuleProblem(
            f"A {invoice.supply_type} {invoice.document_type} does not require an e-invoice"
        )

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so it cannot file"
        )

    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is not None:
        raise ConflictProblem(
            f"{invoice.number} is already registered under IRN {live.irn}; a correction is a "
            "credit note, or cancel the IRN within 24 hours"
        )

    irp_payload = await stage_registration_attempt(session, ctx, invoice, registration, at=at)
    return ManualFilingClaim(invoice=invoice, irp_payload=irp_payload, at=at)


async def record_manual_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    client_name: str,
    invoice_id: uuid.UUID,
    outcome: _RegisterCallOutcome,
    *,
    at: datetime,
    request: Request | None = None,
) -> EInvoiceFiling:
    """The third act of a manual filing: append the row the portal's answer decided, publish, and
    return it. Re-reads the invoice rather than trusting the claim's instance — the same
    discipline every "claim, call, record" split in this platform applies, because nothing handed
    across a network call in between can be assumed still current.
    """
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id, for_update=True)
    if invoice is None:  # pragma: no cover - defensive; existed moments ago in the claim
        raise NotFoundProblem("That invoice is not part of this organization")
    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is not None:
        # Somebody else's attempt — the consumer, a redelivery, a racing request — registered it
        # between our claim and now. The same IRN (the portal answered both of us) or not, the
        # registration that stands is theirs: appending ours would collide on
        # `uq_einvoice_filings_org_irn`, and a late outage `pending` would sit on top of a `filed`.
        return live

    attempt = await record_registration_result(
        session,
        ctx,
        invoice,
        outcome,
        at=_after_newest(at, filings),
        request=request,
        retryable_outage=True,
    )

    if attempt.outcome == "filed" and attempt.ack is not None:
        _publish_filed(session, ctx, invoice, attempt.filing, attempt.ack, portal=client_name)
    else:
        _publish_filing_failed(
            session,
            ctx,
            invoice,
            filing_id=attempt.filing.id,
            error_code=attempt.error_code,
            error=attempt.error,
            retryable=attempt.outcome == "unavailable",
        )
    return attempt.filing


async def file_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    payload: FileInvoiceIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EInvoiceFiling:
    """All three acts run back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that exercise the manual filing rules against a hand-built session
    and do not care about the transaction boundary. The real caller —
    `POST /internal/v1/einvoice/filings` — calls `claim_manual_filing`, `call_irp_register` with
    **no session and no transaction open**, and `record_manual_filing`; see `app/api/internal.py`.

    The automatic path (`autofile_invoice`) answers the same situations with outcomes instead of
    exceptions, because nobody is waiting on the other end of a queue — but both go through
    `live_registration` and `record_registration_result`, so what "already filed" means and what
    gets written can never diverge between them.
    """
    at = at or _now()
    claim = await claim_manual_filing(session, ctx, payload, at=at)
    outcome = call_irp_register(client, claim.irp_payload)
    return await record_manual_filing(
        session, ctx, client.name, claim.invoice.id, outcome, at=at, request=request
    )


# --------------------------------------------------------------------- auto-filing
# The same state machine, driven by the queue instead of by a person. What changes is not the
# rules but the *answers*: an event consumer has nobody to raise an exception at, so every
# situation the manual path calls an error is a named outcome here, logged and — where it matters
# to somebody — published. A handler that raised on "already filed" would dead-letter every
# redelivery, which is to say it would turn at-least-once delivery into an incident.


@dataclass(frozen=True, slots=True)
class FilingOutcome:
    """What one automatic attempt did. `outcome` is the vocabulary a log and a test both read."""

    outcome: str
    filing: EInvoiceFiling | None = None
    #: The attempt number that ran; 0 when nothing was attempted at all.
    attempt: int = 0
    #: The attempt scheduled to follow, on an outage inside the budget.
    next_attempt: int | None = None


#: Every value `FilingOutcome.outcome` can take, so a caller matching on it can be checked.
FILING_OUTCOMES: frozenset[str] = frozenset(
    {
        "filed",
        "already_filed",
        "not_required",
        "unknown_invoice",
        "no_registration",
        "rejected",
        "retry_scheduled",
        "exhausted",
    }
)


@dataclass(frozen=True, slots=True)
class AutofileClaim:
    """What `claim_autofile_attempt` decided.

    `outcome` is set when nothing needs to call the portal at all — the guard's own answer is
    already final (an unknown invoice, a document no longer reportable, one already registered,
    no registration configured) and, where it matters, already published. `invoice`/`irp_payload`
    are set instead when the call is still to come; `retryable` is what `record_autofile_result`
    needs to know but the call itself does not.
    """

    #: The clock reading this claim was made against — resolved once, here, and reused by
    #: `record_autofile_result` so the payload's `generated_at`, the appended row's
    #: `attempted_at` and any scheduled retry's `not_before` are all the same instant rather than
    #: three separate reads of a clock that may have moved between them (the consumer's own
    #: `datetime.now(UTC)` in particular is a *different* clock, read only for the early-arrival
    #: guard — see `app/consumers.py`). First, and without a default, only so the fields after it
    #: may have one — every construction site names it explicitly regardless.
    at: datetime
    outcome: FilingOutcome | None = None
    invoice: Invoice | None = None
    irp_payload: IrpInvoicePayload | None = None
    retryable: bool = True


async def claim_autofile_attempt(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice_id: uuid.UUID,
    *,
    attempt: int,
    at: datetime | None = None,
) -> AutofileClaim:
    """The first of three acts the automatic path takes.

    The order of the guards is the design. **`live_registration` is consulted before the client
    is touched**, so a redelivered request, a manual filing that beat the consumer to it, and a
    second finalize event for the same document all cost nothing and change nothing — the portal
    is never asked to register a document it has already registered, because the portal's answer
    to that is an error and an error is not how a well-behaved integration discovers a fact it
    already had.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        # A request for a document that is no longer there is not an error worth dead-lettering:
        # the outbox row outlived its subject, and the only correct action is to stop.
        return AutofileClaim(at=at, outcome=FilingOutcome("unknown_invoice"))

    # Asked again here, not only at finalize: the request may have been queued minutes ago, and a
    # document that is no longer reportable — or never was — must not be filed because a stale
    # message said so.
    if not domain.should_auto_file(
        supply_type=invoice.supply_type,
        document_type=invoice.document_type,
        status=invoice.status,
    ):
        return AutofileClaim(at=at, outcome=FilingOutcome("not_required"))

    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is not None:
        return AutofileClaim(at=at, outcome=FilingOutcome("already_filed", filing=live))

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        # Nothing was attempted, so nothing goes in the ledger — the same rule the manual path
        # follows. But silence here would be worse than in the manual path, because nobody asked
        # and nobody is waiting, so the obligation is announced on the outbox instead. The
        # document still reports `required` with no status on the read model, which is what makes
        # an unmet obligation visible rather than absent.
        _publish_filing_failed(
            session,
            ctx,
            invoice,
            filing_id=None,
            error_code=domain.ERR_NO_REGISTRATION,
            error=(
                "this organization has no GST registration configured, so its documents "
                "cannot be filed"
            ),
            retryable=False,
            attempt=attempt,
        )
        return AutofileClaim(at=at, outcome=FilingOutcome("no_registration", attempt=attempt))

    retryable = domain.may_retry_filing(attempt)
    irp_payload = await stage_registration_attempt(session, ctx, invoice, registration, at=at)
    return AutofileClaim(invoice=invoice, irp_payload=irp_payload, retryable=retryable, at=at)


async def record_autofile_result(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice_id: uuid.UUID,
    outcome: _RegisterCallOutcome,
    *,
    attempt: int,
    retryable: bool,
    at: datetime,
    client_name: str,
    request: Request | None = None,
) -> FilingOutcome:
    """The third act of an automatic attempt: turn the portal's answer into a row, a published
    event where one is owed, and — on a retryable outage — the next attempt's schedule.

    After the call there are exactly three futures. A registration is done. A rejection is
    **terminal**: the portal read the document and refused it, and the same bytes will be refused
    again, so the row is `failed`, the event says `retryable: false`, and a person has to change
    the document. An outage is retried, bounded by `domain.MAX_FILING_ATTEMPTS`; when that budget
    is spent the outage becomes terminal too, and it says so in its own error code rather than
    disappearing into a queue that never drains.

    Re-reads the invoice rather than trusting the claim's instance, the same discipline
    `record_manual_filing` applies for the same reason.
    """
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id, for_update=True)
    if invoice is None:  # pragma: no cover - defensive; existed moments ago in the claim
        return FilingOutcome("unknown_invoice")
    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is not None:
        # Registered by somebody else since the claim — see `record_manual_filing`. Nothing is
        # appended and nothing is scheduled: the obligation is met.
        return FilingOutcome("already_filed", filing=live, attempt=attempt)

    result = await record_registration_result(
        session,
        ctx,
        invoice,
        outcome,
        at=_after_newest(at, filings),
        request=request,
        retryable_outage=retryable,
    )

    if result.outcome == "filed" and result.ack is not None:
        _publish_filed(
            session,
            ctx,
            invoice,
            result.filing,
            result.ack,
            portal=client_name,
            attempt=attempt,
            trigger="auto",
        )
        return FilingOutcome("filed", filing=result.filing, attempt=attempt)

    if result.outcome == "rejected":
        _publish_filing_failed(
            session,
            ctx,
            invoice,
            filing_id=result.filing.id,
            error_code=result.error_code,
            error=result.error,
            retryable=False,
            attempt=attempt,
            terminal=True,
        )
        return FilingOutcome("rejected", filing=result.filing, attempt=attempt)

    if retryable:
        next_attempt = attempt + 1
        request_filing(
            session,
            ctx,
            invoice,
            attempt=next_attempt,
            not_before=at + domain.filing_retry_delay(attempt),
            trigger="retry",
            now=at,
        )
        return FilingOutcome(
            "retry_scheduled",
            filing=result.filing,
            attempt=attempt,
            next_attempt=next_attempt,
        )

    _publish_filing_failed(
        session,
        ctx,
        invoice,
        filing_id=result.filing.id,
        error_code=result.error_code,
        error=result.error,
        retryable=False,
        attempt=attempt,
        terminal=True,
    )
    return FilingOutcome("exhausted", filing=result.filing, attempt=attempt)


async def autofile_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    *,
    invoice_id: uuid.UUID,
    attempt: int = 1,
    at: datetime | None = None,
    request: Request | None = None,
) -> FilingOutcome:
    """All three acts run back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that drive the retry/backoff state machine against a hand-built
    session and do not care about the transaction boundary. The real caller —
    `app/consumers.py:file_requested_invoice` — calls `claim_autofile_attempt`,
    `call_irp_register` with **no session and no transaction open**, and `record_autofile_result`
    as three separate acts, each in its own `session_scope`.
    """
    at = at or _now()
    claim = await claim_autofile_attempt(session, ctx, invoice_id, attempt=attempt, at=at)
    if claim.outcome is not None:
        return claim.outcome
    if claim.invoice is None or claim.irp_payload is None:  # pragma: no cover - exhaustiveness
        raise BusinessRuleProblem("a claim that did not resolve to an outcome named no invoice")

    call_outcome = call_irp_register(client, claim.irp_payload)
    return await record_autofile_result(
        session,
        ctx,
        claim.invoice.id,
        call_outcome,
        attempt=attempt,
        retryable=claim.retryable,
        at=at,
        client_name=client.name,
        request=request,
    )


# --------------------------------------------------------------------- withdrawing a filing
# The one compliance action that stays deliberately manual. Cancelling an IRN is a statement that
# a registered document should never have been registered, and the portal gives 24 hours to make
# it; past that the remedy is a credit note. Nothing automatic makes that *decision* — but the
# reclaim sweep does finish one somebody already made (D3, ADR-0015 addendum,
# `docs/io-outside-transactions.md`).
#
# Unlike registration, cancellation is **not idempotent by document**: it is time-boxed, it needs
# a reason and an actor, and it happens once. So it is claimed first — a `cancelling` row that
# carries the reason, the remark and the acknowledgement being withdrawn — then the portal is
# called with no transaction open, then the outcome is recorded. A crash between the call and the
# record leaves the `cancelling` row standing; `app/cron.py` resumes it, and a portal that has
# already done the work answers "already cancelled" (`IrpAlreadyCancelled`) — success-by-fetch.


@dataclass(frozen=True, slots=True)
class CancelFacts:
    """Everything the second and third acts of a cancellation need, read off the claim (or, on a
    resume, off the standing `filed` row and the `cancelling` claim) — plain values, so nothing
    crosses the network call holding an ORM instance from a transaction that has closed."""

    invoice_id: uuid.UUID
    irn: str
    ack_no: str | None
    ack_date: datetime | None
    reason_code: str
    remark: str
    #: The clock reading the claim row was written at, and what the portal call is stamped with.
    at: datetime


@dataclass(frozen=True, slots=True)
class _CancelCallOutcome:
    kind: str  # "cancelled" | "rejected" | "unavailable"
    ack: IrpCancelAck | None = None
    error_code: str | None = None
    error: str | None = None


def call_irp_cancel(client: IrpClient, facts: CancelFacts) -> _CancelCallOutcome:
    """The second act of a cancellation, and the only one that touches the portal. Called with
    **no session and no transaction open** (D3).

    `IrpAlreadyCancelled` is folded into `"cancelled"`: the portal's honest answer to a withdrawal
    this platform already asked for, carrying the cancellation it holds. An outage is *not* an
    error here — it is `"unavailable"`, and the claim row standing is the record that the
    withdrawal is owed."""
    try:
        ack = client.cancel(
            irn=facts.irn, reason_code=facts.reason_code, remark=facts.remark, at=facts.at
        )
    except IrpAlreadyCancelled as already:
        return _CancelCallOutcome(kind="cancelled", ack=already.ack)
    except IrpRejected as rejected:
        return _CancelCallOutcome(kind="rejected", error_code=rejected.code, error=rejected.message)
    except IrpUnavailable as outage:
        return _CancelCallOutcome(kind="unavailable", error=str(outage))
    return _CancelCallOutcome(kind="cancelled", ack=ack)


async def call_irp_cancel_off_loop(client: IrpClient, facts: CancelFacts) -> _CancelCallOutcome:
    """`call_irp_cancel` on a worker thread, bounded (`off_loop`); a timeout is an outage, and
    the `cancelling` claim standing is the record that the withdrawal is owed."""
    return await off_loop(
        call_irp_cancel,
        client,
        facts,
        on_timeout=lambda s: _CancelCallOutcome(kind="unavailable", error=_timed_out(s)),
    )


def _cancel_remark(reason_code: str, remark: str) -> str:
    return remark or domain.IRN_CANCELLATION_REASONS[reason_code]


@dataclass(frozen=True, slots=True)
class CancelClaim:
    """What `claim_irn_cancellation` committed: the claim row, and the facts to drive it with."""

    claim: EInvoiceFiling
    facts: CancelFacts


async def claim_irn_cancellation(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CancelFilingIn,
    *,
    at: datetime | None = None,
    request: Request | None = None,
) -> CancelClaim:
    """The first act: refuse everything that does not need the portal, then commit a
    `cancelling` row carrying the reason and the acknowledgement it withdraws, audited as
    `einvoice.cancel_requested` (who asked, and why).

    The 24-hour window is checked here, against the `filed` row's acknowledgement date, rather
    than left to the portal to refuse — the answer is knowable locally, and knowing it is what
    lets a screen offer "cancel" or "raise a credit note" correctly instead of offering both. A
    cancellation already in flight, or already done, is a conflict: the newest row says so.

    Row-locks the invoice first, so two requests racing to withdraw the same IRN serialize here
    and the second one reads the first one's claim instead of writing a second.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, payload.invoice_id, for_update=True
    )
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    if payload.reason_code not in domain.IRN_CANCELLATION_REASONS:
        reasons = ", ".join(f"{k} {v}" for k, v in domain.IRN_CANCELLATION_REASONS.items())
        raise BusinessRuleProblem(f"Cancellation reason must be one of: {reasons}")

    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is None or live.irn is None:
        raise BusinessRuleProblem(f"{invoice.number} carries no registered IRN to cancel")
    newest = filings[0]
    if newest.status == "cancelling":
        raise ConflictProblem(
            f"A cancellation of IRN {live.irn} is already in progress; the reclaim sweep "
            "finishes it if the portal was unreachable"
        )
    if newest.status == "cancelled":
        raise ConflictProblem(f"IRN {live.irn} has already been cancelled")
    if live.ack_date is not None and not domain.cancellation_window_open(live.ack_date, at):
        # The window is computed from the acknowledgement the portal returned and this platform
        # stored, not from anything read at request time — so the deadline the read model shows
        # (`einvoice.cancellable_until`) and the deadline enforced here are the same arithmetic
        # over the same timestamp, and a screen that offered "cancel" was telling the truth.
        deadline = domain.cancellation_deadline(live.ack_date)
        raise BusinessRuleProblem(
            f"IRN {live.irn} was registered more than 24 hours ago (the window closed at "
            f"{deadline.isoformat()}); the correction is a credit note, not a cancellation"
        )

    claim = _append_filing(
        session,
        ctx,
        invoice,
        status="cancelling",
        at=at,
        irn=None,
        ack_no=live.ack_no,
        ack_date=live.ack_date,
        error_code=payload.reason_code,
        error=_cancel_remark(payload.reason_code, payload.remark),
    )
    await session.flush()
    _audit_filing(
        session,
        ctx,
        invoice,
        claim,
        action="einvoice.cancel_requested",
        request=request,
        detail={"irn": live.irn, "reason_code": payload.reason_code},
    )
    facts = CancelFacts(
        invoice_id=invoice.id,
        irn=live.irn,
        ack_no=live.ack_no,
        ack_date=live.ack_date,
        reason_code=payload.reason_code,
        remark=payload.remark,
        at=at,
    )
    return CancelClaim(claim=claim, facts=facts)


async def record_irn_cancellation(
    session: AsyncSession,
    ctx: ScopeContext,
    facts: CancelFacts,
    outcome: _CancelCallOutcome,
    *,
    at: datetime,
    request: Request | None = None,
) -> EInvoiceFiling:
    """The third act: append what the portal decided, and return the newest row.

    * **cancelled** — a *new* row carrying the acknowledgement, the registration row left exactly
      as it was so "registered, then withdrawn" reads off the history in order; audit + event.
    * **rejected** — a `failed` row carrying the portal's code (the window closed, an unknown
      IRN); terminal for this request.
    * **unavailable** — nothing is appended. The `cancelling` claim already standing *is* the
      record that a withdrawal is owed, and the reclaim sweep re-drives it; the caller is handed
      that claim back.

    **Idempotent against a second recorder.** A request whose portal call outlived the sweep's
    lease, and the sweep that resumed the same claim, can both arrive here; the invoice is
    row-locked and the newest row re-read, so whichever records second finds the withdrawal
    already written (or the claim already resolved) and returns that instead of appending a
    duplicate. Re-reads the invoice rather than trusting the claim's instance: nothing handed
    across a network call can be assumed still current.
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, facts.invoice_id, for_update=True
    )
    if invoice is None:  # pragma: no cover - defensive; existed moments ago in the claim
        raise NotFoundProblem("That invoice is not part of this organization")
    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    newest = filings[0]

    if outcome.kind == "unavailable":
        return newest
    if outcome.kind == "cancelled" and newest.status == "cancelled":
        return newest  # somebody else recorded this very withdrawal first
    if outcome.kind == "rejected" and newest.status != "cancelling":
        return newest  # the claim was already resolved; a late refusal changes nothing
    at = _after_newest(at, filings)

    if outcome.kind == "rejected":
        # Carries the acknowledgement it failed to withdraw: the registration still stands, and
        # this is how the read model knows (`to_einvoice_state`).
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="failed",
            at=at,
            ack_no=facts.ack_no,
            ack_date=facts.ack_date,
            error_code=outcome.error_code,
            error=outcome.error,
        )
        await session.flush()
        _audit_filing(
            session, ctx, invoice, filing, action="einvoice.cancel_failed", request=request
        )
        return filing

    if outcome.ack is None:  # pragma: no cover - exhaustiveness
        raise BusinessRuleProblem(f"unknown IRP cancel outcome {outcome.kind!r}")
    # The partial unique index on (org, irn) is why the withdrawn row carries no IRN itself: it
    # cannot claim the number twice.
    filing = _append_filing(
        session,
        ctx,
        invoice,
        status="cancelled",
        at=at,
        irn=None,
        ack_no=facts.ack_no,
        ack_date=facts.ack_date,
        error_code=facts.reason_code,
        error=_cancel_remark(facts.reason_code, facts.remark),
    )
    await session.flush()
    _audit_filing(session, ctx, invoice, filing, action="einvoice.cancelled", request=request)
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_FILING_CANCELLED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "filing_id": str(filing.id),
            "number": invoice.number,
            "irn": outcome.ack.irn,
            "reason_code": facts.reason_code,
            "cancelled_at": outcome.ack.cancel_date.isoformat(),
        },
        actor=_actor(ctx),
    )
    return filing


async def cancel_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    payload: CancelFilingIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EInvoiceFiling:
    """All three acts run back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that exercise the cancellation rules against a hand-built session and
    do not care about the transaction boundary. The real caller — `POST /internal/v1/einvoice/
    filings:cancel` — runs `claim_irn_cancellation`, `call_irp_cancel` with **no transaction
    open**, and `record_irn_cancellation`; see `app/api/internal.py`.
    """
    at = at or _now()
    claimed = await claim_irn_cancellation(session, ctx, payload, at=at, request=request)
    outcome = call_irp_cancel(client, claimed.facts)
    return await record_irn_cancellation(
        session, ctx, claimed.facts, outcome, at=at, request=request
    )


# --------------------------------------------------------------------- re-driving a stale filing
# What `app/cron.py`'s reclaim sweep does with an e-invoice whose filing the platform has lost
# track of: an invoice with no row at all is re-queued (`claim_lost_filing`); a `pending` outage
# whose scheduled retry was lost is re-queued too; a `cancelling` claim whose process died (or
# whose portal was down) is re-driven through the same three acts (`claim_filing_redrive`).
# Discovery is cross-tenant (`selectors.due_filing_reclaims`, `due_stale_filings`); everything
# here runs under the tenant binding, on the app role, with the invoice taken `FOR UPDATE SKIP
# LOCKED` so a request or another replica driving it right now is left alone this tick.
#
# Every path is bounded. A re-queue is counted by the `filing_requested` outbox rows it stages
# (`selectors.filing_requests_since`): each waits `domain.resume_after(n)` after the last, and at
# `domain.MAX_FILING_ATTEMPTS` unanswered requests the invoice gets a terminal `failed` row
# carrying `retry_exhausted` — which is also what takes it out of discovery. A claim is bounded
# by `domain.MAX_COMPLIANCE_RESUMES` drives and by the statutory window.


#: Every answer `claim_lost_filing` / `claim_filing_redrive` / `claim_eway_redrive` can give, so
#: the sweep's report and a test can both match on it.
REDRIVE_OUTCOMES: frozenset[str] = frozenset(
    {"not_due", "requeued", "exhausted", "cancel", "generate"}
)


@dataclass(frozen=True, slots=True)
class FilingRedrive:
    """What the sweep's first act decided for one stale e-invoice filing. `cancel` is set only
    when the outcome is `"cancel"` — the portal call is still to come, with no transaction."""

    outcome: str
    cancel: CancelFacts | None = None


async def _finish_exhausted_requeues(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    *,
    requests: int,
    now: datetime,
) -> None:
    """The terminal row for an invoice the automatic pipeline was asked to file `requests` times
    without ever recording an answer — a consumer that keeps failing on it, or a broker that keeps
    losing the message. A person has to act: the read model says `failed`, `terminal`, and a manual
    filing (`POST /internal/v1/einvoice/filings`) is what is left. Announced like any other
    terminal failure."""
    filing = _append_filing(
        session,
        ctx,
        invoice,
        status="failed",
        at=now,
        error_code=domain.ERR_RETRY_EXHAUSTED,
        error=(
            f"the automatic filing pipeline was asked {requests} times and never recorded an "
            "answer; file this document manually"
        ),
    )
    await session.flush()
    _audit_filing(session, ctx, invoice, filing, action="einvoice.filing_failed", request=None)
    _publish_filing_failed(
        session,
        ctx,
        invoice,
        filing_id=filing.id,
        error_code=filing.error_code,
        error=filing.error,
        retryable=False,
        terminal=True,
    )


async def claim_lost_filing(
    session: AsyncSession, ctx: ScopeContext, invoice_id: uuid.UUID, *, now: datetime
) -> str:
    """The sweep's act for a reportable invoice with **no filing row at all**: re-queue it, or
    stop.

    Re-checked under the tenant binding because discovery ran in another transaction: the invoice
    still has no row, is still auto-fileable, its organization still has a registration (without
    one the consumer answers `no_registration` and writes nothing — re-queueing would loop), and
    no request staged since it was created is still inside its backoff. Then either the request
    budget is spent — a terminal `failed` row, `retry_exhausted` — or `filing_requested` is staged
    at the next attempt number (`n + 1`, never always 1: the consumer's own retry budget has to
    see how far along this chain already is).
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, invoice_id, for_update=True, skip_locked=True
    )
    if invoice is None:
        return "not_due"
    if await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id):
        return "not_due"  # a manual filing, or the pipeline itself, got there first
    if not domain.should_auto_file(
        supply_type=invoice.supply_type,
        document_type=invoice.document_type,
        status=invoice.status,
    ):
        return "not_due"
    if await selectors.get_gst_registration(session, ctx.organization_id) is None:
        return "not_due"
    history = await selectors.filing_requests_since(
        session, ctx.organization_id, invoice.id, after=invoice.created_at
    )
    if history.count >= domain.MAX_FILING_ATTEMPTS:
        await _finish_exhausted_requeues(session, ctx, invoice, requests=history.count, now=now)
        return "exhausted"
    if history.backing_off():
        return "not_due"
    request_filing(
        session,
        ctx,
        invoice,
        attempt=min(history.count + 1, domain.MAX_FILING_ATTEMPTS),
        trigger="reclaim",
        now=now,
    )
    return "requeued"


async def claim_filing_redrive(
    session: AsyncSession, ctx: ScopeContext, invoice_id: uuid.UUID, *, now: datetime
) -> FilingRedrive:
    """The sweep's first act for one invoice whose newest filing row is a stale `pending` or
    `cancelling`.

    * **pending** — an outage whose retry was owed. The scheduler's own table is not this
      service's to read, so "no live scheduler job" is inferred: the row has outlived the delay
      its retry was scheduled for plus grace (`domain.pending_redrive_after`), and no request
      staged since it was written is still backing off. Same bounds as `claim_lost_filing`: the
      organization must still have a registration, and `domain.MAX_FILING_ATTEMPTS` unanswered
      re-queues end in a terminal `retry_exhausted` row. Otherwise the pipeline is re-armed at the
      next attempt number.
    * **cancelling** — re-checked against its own deadline (`domain.resume_after`, growing with
      every drive) and against the **statutory window**: past 24 hours from the acknowledgement
      the portal will refuse a withdrawal, so the claim becomes a terminal `failed` row carrying
      `window_closed` with no portal call. Past `domain.MAX_COMPLIANCE_RESUMES` drives it becomes
      `retry_exhausted` (the poisoned-step guard). Otherwise a **fresh `cancelling` row** is
      appended — the lease: nobody can pass the staleness test again until it is itself stale —
      and the facts to call the portal with are handed back.

    Every terminal row for a withdrawal carries the acknowledgement it was withdrawing
    (`ack_no`/`ack_date`), which is how the read model knows the registration still stands.
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, invoice_id, for_update=True, skip_locked=True
    )
    if invoice is None:
        return FilingRedrive("not_due")
    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    if not filings:
        return FilingRedrive("not_due")
    newest = filings[0]
    statuses = [f.status for f in filings]

    if newest.status == "pending":
        attempts = domain.leading_claim_attempts(
            statuses, claim="pending", chain=frozenset({"pending"})
        )
        if newest.attempted_at > now - domain.pending_redrive_after(attempts):
            return FilingRedrive("not_due")
        if not domain.should_auto_file(
            supply_type=invoice.supply_type,
            document_type=invoice.document_type,
            status=invoice.status,
        ):
            return FilingRedrive("not_due")
        if await selectors.get_gst_registration(session, ctx.organization_id) is None:
            return FilingRedrive("not_due")
        history = await selectors.filing_requests_since(
            session, ctx.organization_id, invoice.id, after=newest.created_at
        )
        if history.count >= domain.MAX_FILING_ATTEMPTS:
            await _finish_exhausted_requeues(
                session, ctx, invoice, requests=history.count, now=max(now, newest.attempted_at)
            )
            return FilingRedrive("exhausted")
        if history.backing_off():
            return FilingRedrive("not_due")
        request_filing(
            session,
            ctx,
            invoice,
            attempt=min(attempts + 1, domain.MAX_FILING_ATTEMPTS),
            trigger="reclaim",
            now=now,
        )
        return FilingRedrive("requeued")

    if newest.status != "cancelling":
        return FilingRedrive("not_due")

    attempts = domain.leading_claim_attempts(
        statuses, claim="cancelling", chain=frozenset({"cancelling"})
    )
    if newest.attempted_at > now - domain.resume_after(attempts):
        return FilingRedrive("not_due")
    live = live_registration(filings)
    if live is None or live.irn is None or newest.error_code is None:  # pragma: no cover
        return FilingRedrive("not_due")  # a claim is only ever appended over a live registration

    reason_code = newest.error_code
    remark = newest.error or ""
    at = max(now, newest.attempted_at)
    window_closed = live.ack_date is not None and not domain.cancellation_window_open(
        live.ack_date, now
    )
    if window_closed or attempts >= domain.MAX_COMPLIANCE_RESUMES:
        failed = _append_filing(
            session,
            ctx,
            invoice,
            status="failed",
            at=at,
            ack_no=live.ack_no,
            ack_date=live.ack_date,
            error_code=domain.ERR_WINDOW_CLOSED if window_closed else domain.ERR_RETRY_EXHAUSTED,
            error=(
                f"the 24-hour window to cancel IRN {live.irn} closed before the portal confirmed "
                "the withdrawal; check the portal, and raise a credit note if the IRN stands"
                if window_closed
                else f"gave up cancelling IRN {live.irn} after {attempts} attempts; the portal "
                "never confirmed the withdrawal"
            ),
        )
        await session.flush()
        _audit_filing(session, ctx, invoice, failed, action="einvoice.cancel_failed", request=None)
        return FilingRedrive("exhausted")

    renewal = _append_filing(
        session,
        ctx,
        invoice,
        status="cancelling",
        at=at,
        irn=None,
        ack_no=live.ack_no,
        ack_date=live.ack_date,
        error_code=reason_code,
        error=remark,
    )
    await session.flush()
    _audit_filing(
        session,
        ctx,
        invoice,
        renewal,
        action="einvoice.cancel_resumed",
        request=None,
        detail={"irn": live.irn, "reason_code": reason_code, "attempt": attempts + 1},
    )
    return FilingRedrive(
        "cancel",
        cancel=CancelFacts(
            invoice_id=invoice.id,
            irn=live.irn,
            ack_no=live.ack_no,
            ack_date=live.ack_date,
            reason_code=reason_code,
            remark=remark,
            at=at,
        ),
    )


def _audit_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    filing: EInvoiceFiling,
    *,
    action: str,
    request: Request | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Every filing attempt leaves an audit row as well as a ledger row.

    Not redundant: `einvoice_filings` records what the portal said, `audit_logs` records who asked
    and from where. A compliance question needs both — "was it filed" and "who filed it". `detail`
    adds what a claim row cannot say in its own columns — which IRN a cancellation withdraws, and
    the remark it gave.
    """
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="einvoice_filing",
        entity_id=filing.id,
        after={
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "status": filing.status,
            "irn": filing.irn,
            "error_code": filing.error_code,
            **(detail or {}),
        },
        request=request,
    )


# --------------------------------------------------------------------- purchases


async def record_purchase_document(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: PurchaseDocumentCreate,
    *,
    request: Request | None = None,
) -> PurchaseDocument:
    """Record what a supplier billed this organization.

    The tax split is **computed here**, by the same `domain.split_gst` an invoice uses, from the
    taxable value and the rate. Accepting the heads from the caller would put a second
    implementation of the platform's one exactly-footing calculation into the purchase book, and
    the reconciler would eventually be right about a document the book was wrong about.

    Whether the supply is intra- or inter-state is decided the way it always is: the supplier's
    state against the place of supply, which for a purchase is *this* organization's own state.
    That is why a registration is required — without one there is no recipient state, and a guess
    would pick a tax head, which is a refund claim rather than a rounding difference.
    """
    if payload.document_type not in domain.IMS_DOCUMENT_TYPES:
        kinds = ", ".join(sorted(domain.IMS_DOCUMENT_TYPES))
        raise BusinessRuleProblem(f"Unknown document type {payload.document_type!r} ({kinds})")

    supplier_gstin = _checked_gstin(payload.supplier_gstin, whose="The supplier's")
    if supplier_gstin is None:
        raise BusinessRuleProblem(
            "A purchase document needs the supplier's GSTIN: it is what a supplier's own filing "
            "is matched against, and a document without one can never be reconciled"
        )

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so a purchase cannot be "
            "classified as intra- or inter-state"
        )

    if await selectors.purchase_exists(
        session,
        ctx.organization_id,
        supplier_gstin=supplier_gstin,
        document_type=payload.document_type,
        number=payload.number,
    ):
        raise ConflictProblem(
            f"A {payload.document_type} numbered {payload.number!r} from {supplier_gstin} is "
            "already recorded"
        )

    rate = _checked_rate(payload.tax_rate_pct, where="Purchase document tax rate")
    intra = domain.is_intra_state(
        supply_type="b2b",
        seller_state_code=domain.parse_gstin(supplier_gstin).state_code,
        place_of_supply=registration.state_code,
    )
    split = domain.split_gst(payload.taxable_minor, rate, intra_state=intra)

    purchase = PurchaseDocument(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        supplier_gstin=supplier_gstin,
        supplier_name=payload.supplier_name,
        number=payload.number,
        document_type=payload.document_type,
        document_date=payload.document_date,
        place_of_supply=registration.state_code,
        currency=payload.currency,
        taxable_minor=payload.taxable_minor,
        tax_rate_pct=rate,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
        total_minor=payload.taxable_minor + split.total_minor,
        notes=payload.notes,
    )
    session.add(purchase)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_document.recorded",
        entity="purchase_document",
        entity_id=purchase.id,
        after={
            "supplier_gstin": purchase.supplier_gstin,
            "number": purchase.number,
            "total_minor": purchase.total_minor,
        },
        request=request,
    )
    return purchase


# --------------------------------------------------------------------- IMS ingest
# Reading somebody else's filings about this business. Two steps, deliberately separable: pull
# what the portal holds, then work out what it means. Keeping them apart is what lets the
# reconciliation be re-run after the purchase book changes without re-fetching anything, which is
# the ordinary case — the supplier's document did not change, our record of it did.
#
# And the pull itself is three acts (D3): `claim_ims_sync` (a short read — is the period real,
# whose ledger is it), `fetch_ims_period` (the portal, with **no transaction open**), then
# `record_ims_fetch` + `reconcile_ims_documents` in one short transaction. Nothing is written
# before the fetch, so there is nothing for a crash in between to leave half-done: the caller
# syncs again, and the ingest is idempotent on the portal's own record key.


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one pull from the portal produced. `fetched` and `created` differ on a re-sync, which
    is how the ingest's idempotency shows up as a number rather than as a claim."""

    fetched: int
    created: int
    documents: list[ImsDocument]
    #: Pushed decisions this sync found the portal disagreeing with, and ones agreeing again (C2).
    drift_detected: int = 0
    drift_cleared: int = 0


@dataclass(frozen=True, slots=True)
class ImsSyncTarget:
    """Whose supplier ledger to fetch, for which period — everything `fetch_ims_period` needs,
    as plain values, so the call holds nothing from the transaction that resolved them."""

    gstin: str
    period: str


async def claim_ims_sync(session: AsyncSession, ctx: ScopeContext, *, period: str) -> ImsSyncTarget:
    """The first act: refuse a period that does not exist and an organization with no
    registration — both answerable without the portal — and resolve the GSTIN to fetch for.
    Writes nothing."""
    try:
        domain.parse_period(period)
    except ValueError as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so there is no supplier "
            "ledger to fetch"
        )
    return ImsSyncTarget(gstin=registration.gstin, period=period)


def fetch_ims_period(client: ImsClient, target: ImsSyncTarget) -> list[ImsSupplierDocument]:
    """The second act, and the only one that touches the portal. Called with **no session and no
    transaction open** (D3), so a slow IMS holds nothing from this service's connection pool.

    Unlike a filing, a failed fetch changes nothing and leaves nothing behind: there is no
    attempt to record, because nothing was asserted to anybody. Answered as an upstream failure
    so the caller retries rather than concluding the period is empty.
    """
    try:
        return list(client.fetch(gstin=target.gstin, period=target.period))
    except ImsUnavailable as outage:
        raise UpstreamUnavailableProblem(
            f"The IMS portal could not be reached: {outage}"
        ) from outage


async def fetch_ims_period_off_loop(
    client: ImsClient, target: ImsSyncTarget
) -> list[ImsSupplierDocument]:
    """`fetch_ims_period` on a worker thread, bounded (`off_loop`); a timeout is the same
    upstream failure an unreachable portal is — nothing was written, so the caller retries."""

    def _gave_up(seconds: float) -> list[ImsSupplierDocument]:
        raise UpstreamUnavailableProblem(
            f"The IMS portal could not be reached: {_timed_out(seconds)}"
        )

    return await off_loop(fetch_ims_period, client, target, on_timeout=_gave_up)


#: Rows per `INSERT ... ON CONFLICT` statement. A period can hold thousands of supplier documents,
#: and one statement per period would run into the driver's bind-parameter ceiling long before it
#: ran into anything else.
_IMS_INSERT_CHUNK = 500


async def record_ims_fetch(
    session: AsyncSession,
    ctx: ScopeContext,
    fetched: list[ImsSupplierDocument],
    *,
    period: str,
    portal: str,
    at: datetime | None = None,
    request: Request | None = None,
) -> IngestResult:
    """The third act: store the documents not already held, announce each new one, audit the sync.

    Idempotent on `source_ref`, the portal's own reference for its row — *not* on the supplier's
    document number. The distinction is the whole reconciliation: re-syncing August must not
    duplicate anything, and a supplier who filed the same invoice twice must still show up as two
    rows, because that duplicate is precisely what an operator needs to see and reject.

    Enforced by the database, not by a read-then-write: `INSERT ... ON CONFLICT (organization_id,
    source_ref) DO NOTHING RETURNING`, so two syncs of the same period racing each other — which
    the fetch now happening outside any transaction makes an ordinary thing rather than a
    theoretical one — both succeed, and only the rows that were genuinely new come back to be
    announced. Every row is validated (`_ims_row`) before anything is inserted; a malformed one
    refuses the whole sync.
    """
    at = at or _now()
    values = [_ims_row(ctx, doc, period=period, at=at) for doc in fetched]

    created: list[ImsDocument] = []
    for start in range(0, len(values), _IMS_INSERT_CHUNK):
        chunk = values[start : start + _IMS_INSERT_CHUNK]
        stmt = (
            pg_insert(ImsDocument)
            .values(chunk)
            .on_conflict_do_nothing(constraint="uq_ims_documents_org_source_ref")
            .returning(ImsDocument)
        )
        created.extend((await session.scalars(stmt)).all())
    # `RETURNING` carries no ordering promise; the events go out in the portal's own order.
    order: dict[str, int] = {}
    for index, doc in enumerate(fetched):
        order.setdefault(doc.source_ref, index)
    created.sort(key=lambda row: order.get(row.source_ref, len(order)))

    for row in created:
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_IMS_DOCUMENT_INGESTED,
            aggregate_type="ims_document",
            aggregate_id=row.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "ims_document_id": str(row.id),
                "source_ref": row.source_ref,
                "supplier_gstin": row.supplier_gstin,
                "document_number": row.document_number,
                "period": row.period,
                "total_minor": row.total_minor,
            },
            actor=_actor(ctx),
        )

    # Does the portal now hold what was pushed? Compared in this same transaction, against the
    # action each fetched row reports for this taxpayer (C2).
    drift = await flag_ims_drift(session, ctx, fetched, at=at)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ims.synced",
        entity="ims_sync",
        entity_id=None,
        after={
            "period": period,
            "portal": portal,
            "fetched": len(fetched),
            "created": len(created),
            "drift_detected": drift.detected,
            "drift_cleared": drift.cleared,
        },
        request=request,
    )
    return IngestResult(
        fetched=len(fetched),
        created=len(created),
        documents=created,
        drift_detected=drift.detected,
        drift_cleared=drift.cleared,
    )


async def ingest_ims_documents(
    session: AsyncSession,
    ctx: ScopeContext,
    client: ImsClient,
    *,
    period: str,
    at: datetime | None = None,
    request: Request | None = None,
) -> IngestResult:
    """All three acts back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that exercise the ingest rules against a hand-built session and do not
    care about the transaction boundary. The real caller — `POST /internal/v1/ims/sync` — runs
    `claim_ims_sync`, `fetch_ims_period` with **no transaction open**, then `record_ims_fetch`
    and the reconciliation in one short transaction; see `app/api/internal.py`.
    """
    target = await claim_ims_sync(session, ctx, period=period)
    fetched = fetch_ims_period(client, target)
    return await record_ims_fetch(
        session, ctx, fetched, period=period, portal=client.name, at=at, request=request
    )


def _ims_row(
    ctx: ScopeContext,
    doc: ImsSupplierDocument,
    *,
    period: str,
    at: datetime,
) -> dict[str, Any]:
    """Validate one inbound document and return the row to insert.

    A malformed supplier GSTIN refuses the **whole sync**, loudly, naming the row. That is the
    harsher of the two available choices — the other being to skip the row — and it is the right
    one here: this service stores no unverified GSTIN anywhere, a portal returning one is a real
    problem rather than a data-quality nuisance, and silently dropping documents from a
    compliance queue is how a credit goes unclaimed with nothing on any screen to show for it.
    """
    if doc.document_type not in domain.IMS_DOCUMENT_TYPES:
        raise BusinessRuleProblem(
            f"The portal returned {doc.source_ref!r} with document type "
            f"{doc.document_type!r}, which is not one IMS carries"
        )
    try:
        supplier = domain.parse_gstin(doc.supplier_gstin)
    except domain.GstinError as exc:
        raise BusinessRuleProblem(
            f"The portal returned {doc.source_ref!r} with an unusable supplier GSTIN: {exc}"
        ) from exc

    return {
        "id": uuid.uuid4(),
        "organization_id": ctx.organization_id,
        "created_by": str(ctx.principal.sub),
        "source_ref": doc.source_ref,
        "source": "ims",
        "supplier_gstin": supplier.value,
        "supplier_name": doc.supplier_name,
        "document_number": doc.document_number,
        "document_type": doc.document_type,
        "document_date": doc.document_date,
        "period": doc.period or period,
        "taxable_minor": doc.taxable_minor,
        "cgst_minor": doc.cgst_minor,
        "sgst_minor": doc.sgst_minor,
        "igst_minor": doc.igst_minor,
        "total_minor": doc.total_minor,
        "irn": doc.irn,
        # Every document starts undecided. Nothing this service does moves it — only a person.
        "status": "pending",
        "fetched_at": at,
    }


# --------------------------------------------------------------------- IMS reconciliation


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """One reconciliation pass. `run_id` groups every suggestion it produced."""

    run_id: uuid.UUID
    reconciled: int


async def reconcile_ims_documents(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    period: str | None = None,
    at: datetime | None = None,
) -> ReconcileResult:
    """Work out what each inbound document means, and suggest what to do about it.

    **Suggests. Nothing here changes a document's status**, not even for a match so exact that
    the answer is obvious — accepting a supplier's document claims the input tax credit on it,
    which is a decision a person is accountable for and a program is not. The pass appends one
    suggestion per document and stops.

    Every inbound document is loaded, not only the period's, because the duplicate check spans
    periods: a supplier re-filing December's invoice in January is exactly the case a
    period-limited read would call two distinct documents and quietly suggest accepting twice.
    """
    at = at or _now()
    run_id = uuid.uuid4()

    documents = await selectors.all_ims_documents(session, ctx.organization_id)
    purchases = await selectors.all_purchase_documents(session, ctx.organization_id)

    facts = [
        domain.InboundDocument(
            id=doc.id,
            source_ref=doc.source_ref,
            supplier_gstin=doc.supplier_gstin,
            document_type=doc.document_type,
            document_number=doc.document_number,
            document_date=doc.document_date,
            taxable_minor=doc.taxable_minor,
            total_minor=doc.total_minor,
            first_seen=doc.fetched_at,
        )
        for doc in documents
    ]
    records = [
        domain.PurchaseRecord(
            id=p.id,
            supplier_gstin=p.supplier_gstin,
            document_type=p.document_type,
            document_number=p.number,
            document_date=p.document_date,
            taxable_minor=p.taxable_minor,
            total_minor=p.total_minor,
        )
        for p in purchases
    ]

    targets = [
        (doc, fact)
        for doc, fact in zip(documents, facts, strict=True)
        if period is None or doc.period == period
    ]
    for doc, fact in targets:
        suggestion = domain.suggest_action(fact, purchases=records, siblings=facts)
        session.add(
            ImsReconciliation(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                ims_document_id=doc.id,
                run_id=run_id,
                suggested_action=suggestion.action,
                reason=suggestion.reason,
                detail=suggestion.detail,
                delta_minor=suggestion.delta_minor,
                matched_purchase_id=suggestion.matched_purchase_id,
                duplicate_of_id=suggestion.duplicate_of_id,
                computed_at=at,
            )
        )
    await session.flush()
    return ReconcileResult(run_id=run_id, reconciled=len(targets))


# --------------------------------------------------------------------- the ITC ledger
# Two of the ledger's three posting kinds happen here, beside the decision that causes them —
# `utilisation`, the third, is posted by `file_return` when a `gstr3b` is filed.


def _itc_source_ref(*, source_type: str, source_id: uuid.UUID, tax_head: str) -> str:
    """The idempotency key `itc_ledger_entries.source_ref` — unique per `kind` at the database, so
    a retried write collides with the row it would otherwise duplicate rather than doubling the
    ledger."""
    return f"{source_type}:{source_id}:{tax_head}"


async def _post_itc_entries(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    gst_registration_id: uuid.UUID,
    period: str,
    source_period: str,
    kind: str,
    amounts: domain.TaxHeadAmounts,
    source_type: str,
    source_id: uuid.UUID,
    description: str,
    at: datetime,
) -> list[ItcLedgerEntry]:
    """Post one row per non-zero tax head. Skips a head that is exactly zero rather than posting
    an empty row — a document taxed only under IGST has nothing to say about CGST or SGST, and a
    zero-amount row would be noise on every list and report that reads this table.

    `period` is the period this posting is *attributed to* — the claim period, possibly later than
    `source_period` when the source event arrives after its own period's `gstr3b` is filed.
    """
    entries: list[ItcLedgerEntry] = []
    for head in domain.ITC_TAX_HEAD_ORDER:
        amount = amounts.for_head(head)
        if amount == 0:
            continue
        entry = ItcLedgerEntry(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            gst_registration_id=gst_registration_id,
            period=period,
            source_period=source_period,
            tax_head=head,
            kind=kind,
            amount_minor=amount,
            source_type=source_type,
            source_id=source_id,
            source_ref=_itc_source_ref(source_type=source_type, source_id=source_id, tax_head=head),
            description=description,
            posted_by=str(ctx.principal.sub),
            posted_at=at,
        )
        session.add(entry)
        entries.append(entry)
    if entries:
        await session.flush()
    return entries


def _publish_itc_posting(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    gst_registration_id: uuid.UUID,
    gstin: str,
    period: str,
    entries: list[ItcLedgerEntry],
    at: datetime,
) -> None:
    """Announce a batch of postings as one event — a document's accrual is one economic fact even
    though it may be several rows, one per tax head. Nothing is published when nothing was
    posted: a blocked document accrues no credit and no event, which is the correct silence."""
    if not entries:
        return
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload={
            "organization_id": str(ctx.organization_id),
            "gst_registration_id": str(gst_registration_id),
            "gstin": gstin,
            "period": period,
            "entries": [
                {
                    "tax_head": entry.tax_head,
                    "kind": entry.kind,
                    "amount_minor": entry.amount_minor,
                }
                for entry in entries
            ],
            "posted_at": at.isoformat(),
        },
        actor=_actor(ctx),
    )


class _DecisionBatch:
    """The reads a run of decisions **shares**, so a bulk request does not repeat them per
    document: the GST registration (row-locked once, and only if some decision moves the ITC
    ledger), each period's claim period (cached), and every document's net standing on the ITC
    ledger (loaded for all of them in one query, by the caller).

    A single decision is a batch of one; the code path is the same, which is what keeps the bulk
    request from being a second, subtly different way of recording a decision.
    """

    def __init__(
        self,
        session: AsyncSession,
        ctx: ScopeContext,
        *,
        net_accrued: dict[uuid.UUID, domain.TaxHeadAmounts],
    ) -> None:
        self._session = session
        self._ctx = ctx
        self.net_accrued = net_accrued
        self._registration: GstRegistration | None = None
        self._claim_periods: dict[str, str] = {}

    async def registration(self) -> GstRegistration:
        # Locked before `claim_period_for` reads which periods are filed: a filing racing a
        # decision holds this same lock while it moves a return to `filed`, so this either waits
        # for that commit and then correctly sees the period as filed, or the filing is still in
        # flight and blocks behind *this* transaction — never both reading "not filed yet".
        if self._registration is None:
            self._registration = await registration_for(
                self._session, self._ctx, None, for_update=True
            )
        return self._registration

    async def claim_period(self, period: str) -> str:
        if period not in self._claim_periods:
            registration = await self.registration()
            self._claim_periods[period] = await selectors.claim_period_for(
                self._session, self._ctx.organization_id, registration.id, period
            )
        return self._claim_periods[period]


async def record_ims_decision(
    session: AsyncSession,
    ctx: ScopeContext,
    ims_document_id: uuid.UUID,
    payload: ImsDecisionCreate,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> ImsDecision:
    """Record what a person decided about one inbound document — append-only. The document is
    locked, its context loaded, and `_decide` does the rest; see it for what a decision does."""
    at = at or _now()
    if payload.action not in domain.IMS_ACTIONS:
        actions = ", ".join(sorted(domain.IMS_ACTIONS))
        raise BusinessRuleProblem(f"An IMS decision must be one of: {actions}")
    if payload.expense_category not in domain.ITC_EXPENSE_CATEGORIES:
        categories = ", ".join(sorted(domain.ITC_EXPENSE_CATEGORIES))
        raise BusinessRuleProblem(f"An expense category must be one of: {categories}")

    document = await selectors.get_ims_document(
        session, ctx.organization_id, ims_document_id, for_update=True
    )
    if document is None:
        raise NotFoundProblem("That supplier document is not part of this organization")

    suggestions = await selectors.latest_reconciliations(
        session, ctx.organization_id, [document.id]
    )
    counts = await selectors.decision_counts(session, ctx.organization_id, [document.id])
    net = await selectors.itc_net_accrued_for_documents(session, ctx.organization_id, [document.id])
    return await _decide(
        session,
        ctx,
        document,
        payload,
        suggestion=suggestions.get(document.id),
        version=counts.get(document.id, 0) + 1,
        batch=_DecisionBatch(session, ctx, net_accrued=net),
        at=at,
        request=request,
    )


async def record_ims_decisions_bulk(
    session: AsyncSession,
    ctx: ScopeContext,
    items: list[ImsBulkDecisionItem],
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> list[tuple[uuid.UUID, ImsDecision]]:
    """Record several decisions in the caller's one transaction — **all of them or none** — each
    exactly as `record_ims_decision` would have recorded it alone: its own append-only row with the
    suggestion snapshotted beside it, its own audit row, its own ITC posting, its own queued push.

    What is shared is only what is safe to share: the documents are fetched and row-locked in one
    query (in id order, so two overlapping bulk requests cannot deadlock), and the suggestions,
    decision counts and ITC standings for all of them are read in one query each — a bulk request
    never runs a read per document. A document that is not part of this organization (another
    tenant's id reads the same) is a 404 for the **whole** request, before anything is written,
    and names none of them.
    """
    at = at or _now()
    for item in items:
        if item.action not in domain.IMS_ACTIONS:
            actions = ", ".join(sorted(domain.IMS_ACTIONS))
            raise BusinessRuleProblem(f"An IMS decision must be one of: {actions}")
        if item.expense_category not in domain.ITC_EXPENSE_CATEGORIES:
            categories = ", ".join(sorted(domain.ITC_EXPENSE_CATEGORIES))
            raise BusinessRuleProblem(f"An expense category must be one of: {categories}")

    ids = [item.document_id for item in items]
    documents = await selectors.get_ims_documents(
        session, ctx.organization_id, ids, for_update=True
    )
    if len(documents) != len(set(ids)):
        raise NotFoundProblem(
            "One or more of those supplier documents is not part of this organization"
        )

    suggestions = await selectors.latest_reconciliations(session, ctx.organization_id, ids)
    counts = await selectors.decision_counts(session, ctx.organization_id, ids)
    net = await selectors.itc_net_accrued_for_documents(session, ctx.organization_id, ids)
    batch = _DecisionBatch(session, ctx, net_accrued=net)

    decided: list[tuple[uuid.UUID, ImsDecision]] = []
    for item in items:
        document = documents[item.document_id]
        decision = await _decide(
            session,
            ctx,
            document,
            ImsDecisionCreate(
                action=item.action, note=item.note, expense_category=item.expense_category
            ),
            suggestion=suggestions.get(document.id),
            version=counts.get(document.id, 0) + 1,
            batch=batch,
            at=at,
            request=request,
        )
        decided.append((document.id, decision))
    return decided


async def _decide(
    session: AsyncSession,
    ctx: ScopeContext,
    document: ImsDocument,
    payload: ImsDecisionCreate,
    *,
    suggestion: ImsReconciliation | None,
    version: int,
    batch: _DecisionBatch,
    at: datetime,
    request: Request | None,
) -> ImsDecision:
    """Record what a person decided about one inbound document — append-only.

    A change of mind is a further row, never an edit, because the question asked later is never
    "what does it say now" but "who decided this, when, and what were they shown at the time".
    So the suggestion standing when the decision was made is snapshotted beside it: a re-run may
    change the current advice, and "did the operator follow the machine" has to stay answerable
    afterwards.

    `ims_documents.status` is moved here, in this same transaction, and **nowhere else** — it is a
    projection of the newest decision, kept as a column because the queue's whole access path is
    "every pending document for August" and that is not a query you build from a lateral join on
    every list page. A test asserts the projection agrees with the history, which is what keeps
    the shortcut honest.

    **The ITC ledger moves in the same transaction, whenever the document's net standing should
    change.** That covers three cases with one formula: a plain accept (nothing outstanding yet,
    something now should be), a plain un-accept (something outstanding, nothing now should be),
    and a later decision that re-classifies an *already*-accepted document under a different
    `expense_category` (something outstanding, a different something now should be — see
    `domain.split_positive_negative`). The document is locked `FOR UPDATE` above before any of
    this runs, so two decisions racing the same document serialise on the row rather than each
    reading the same `before_status` and each posting a transition of their own.

    **The exclusion of rejected, held and un-actioned documents from ITC is this function's, and
    it is a projection of the newest decision, not a mutation of any document (C2).** Credit is
    posted only while the newest decision is an accept (`target` below is the document's eligible
    amount then and zero otherwise), and leaving `accepted` for anything — `reject`,
    `pending_action`, `no_action` — posts the exact reversal of what was outstanding. Purchase
    documents are never touched, and GSTR-3B Table 4A reads the ledger, so a document that is not
    currently accepted is not in the return.

    **A push is queued in the same transaction** (`_queue_push`): the decision says what a person
    decided and the push says whether GSTN has been told, so the two are written together and the
    push never edits the decision. Sending it is a separate act with a separate permission.
    """
    decision = ImsDecision(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ims_document_id=document.id,
        action=payload.action,
        note=payload.note,
        suggested_action=suggestion.suggested_action if suggestion else None,
        suggestion_reason=suggestion.reason if suggestion else None,
        reconciliation_id=suggestion.id if suggestion else None,
        expense_category=payload.expense_category,
        decided_by=str(ctx.principal.sub),
        decided_at=at,
    )
    session.add(decision)

    before_status = document.status
    document.status = domain.IMS_ACTION_STATUS[payload.action]
    document.updated_by = str(ctx.principal.sub)
    await session.flush()

    was_accepted = before_status == "accepted"
    now_accepted = document.status == "accepted"
    if was_accepted or now_accepted:
        registration = await batch.registration()
        outstanding = (
            batch.net_accrued.get(document.id, domain.ZERO_ITC) if was_accepted else domain.ZERO_ITC
        )
        # A credit note reduces what a supplier billed, so it reduces the credit this organization
        # may claim rather than adding to it — accepting one should move the ledger down.
        sign = -1 if document.document_type == "credit_note" else 1
        document_amounts = domain.TaxHeadAmounts(
            igst_minor=sign * document.igst_minor,
            cgst_minor=sign * document.cgst_minor,
            sgst_minor=sign * document.sgst_minor,
        )
        target = (
            domain.itc_eligible_amounts(document_amounts, category=payload.expense_category)
            if now_accepted
            else domain.ZERO_ITC
        )
        delta = target - outstanding
        accrual_part, reversal_part = domain.split_positive_negative(delta)

        if accrual_part.total_minor or reversal_part.total_minor:
            claim_period = await batch.claim_period(document.period)
            if was_accepted and now_accepted:
                description = (
                    f"IMS document {document.document_number} re-classified as "
                    f"{payload.expense_category}"
                )
            elif now_accepted:
                description = f"IMS document {document.document_number} accepted"
            else:
                description = f"IMS document {document.document_number} moved to {document.status}"

            for kind, amounts, event_type in (
                ("accrual", accrual_part, EVT_ITC_ACCRUED),
                ("reversal", reversal_part, EVT_ITC_REVERSED),
            ):
                entries = await _post_itc_entries(
                    session,
                    ctx,
                    gst_registration_id=registration.id,
                    period=claim_period,
                    source_period=document.period,
                    kind=kind,
                    amounts=amounts,
                    source_type="ims_decision",
                    source_id=decision.id,
                    description=description,
                    at=at,
                )
                _publish_itc_posting(
                    session,
                    ctx,
                    event_type=event_type,
                    aggregate_type="ims_document",
                    aggregate_id=document.id,
                    gst_registration_id=registration.id,
                    gstin=registration.gstin,
                    period=claim_period,
                    entries=entries,
                    at=at,
                )

    push = await _queue_push(session, ctx, document, decision, version=version, at=at)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ims.decided",
        entity="ims_document",
        entity_id=document.id,
        before={"status": before_status},
        after={
            "status": document.status,
            "action": decision.action,
            "suggested_action": decision.suggested_action,
            "suggestion_reason": decision.suggestion_reason,
            "expense_category": decision.expense_category,
            "decision_id": str(decision.id),
            "version": version,
            "push_id": str(push.id),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_IMS_DOCUMENT_DECIDED,
        aggregate_type="ims_document",
        aggregate_id=document.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ims_document_id": str(document.id),
            "decision_id": str(decision.id),
            "source_ref": document.source_ref,
            "supplier_gstin": document.supplier_gstin,
            "document_number": document.document_number,
            "period": document.period,
            "action": decision.action,
            "status": document.status,
            "suggested_action": decision.suggested_action,
            "suggestion_reason": decision.suggestion_reason,
            #: Whether the person disagreed with the platform. The number worth watching: a
            #: suggestion nobody follows is a suggestion that should be changed.
            "followed_suggestion": decision.suggested_action == decision.action,
            "decided_at": decision.decided_at.isoformat(),
            #: The decision's ordinal within its document, and that its push is now queued.
            "version": version,
            "push_status": push.status,
        },
        actor=_actor(ctx),
    )
    return decision


async def _queue_push(
    session: AsyncSession,
    ctx: ScopeContext,
    document: ImsDocument,
    decision: ImsDecision,
    *,
    version: int,
    at: datetime,
) -> ImsPush:
    """Queue the delivery of one decision to GSTN — inside the decision's own transaction.

    The document's earlier current push stops being current. One still `queued` (decided, never
    sent) becomes `superseded`: sending it would tell GSTN something the person has since
    reversed, and it is never worth a portal call. One already `submitting`, `pushed`, `failed` or
    `conflict` keeps its status — it *happened*, or is happening — and only stops being the
    document's answer; the new push is sent after it (the claim never sends a decision while an
    older one for the same document is still in flight), so GSTN's final state is the newest one.
    """
    await session.execute(
        update(ImsPush)
        .where(
            ImsPush.organization_id == ctx.organization_id,
            ImsPush.ims_document_id == document.id,
            ImsPush.is_current.is_(True),
            ImsPush.deleted_at.is_(None),
        )
        .values(
            is_current=False,
            status=case((ImsPush.status == "queued", "superseded"), else_=ImsPush.status),
            updated_at=at,
            updated_by=str(ctx.principal.sub),
        )
        .execution_options(synchronize_session=False)
    )
    push = ImsPush(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ims_document_id=document.id,
        decision_id=decision.id,
        period=document.period,
        source_ref=document.source_ref,
        action=decision.action,
        version=version,
        idempotency_key=domain.ims_push_key(document.period, document.source_ref, version),
        status="queued",
        is_current=True,
        attempts=0,
        drift=False,
    )
    session.add(push)
    await session.flush()
    return push


# --------------------------------------------------------------------- IMS: pushing decisions back
# Recording a decision is the platform's own position; telling GSTN is a second act with a second
# failure mode, and it runs the way every portal call here runs (D3): a short **claim**
# transaction, the **call** with no transaction open and off the event loop, a short **record**
# transaction. The state machine (`domain.IMS_PUSH_STATUSES`), the idempotency key (period,
# document, decision version), the batching (per period, within the portal's limit), and the
# recovery (the reclaim sweep resumes what a crash left `submitting`, bounded) are all here.
#
# Three properties carry the design, and each has a test:
#
# * **Nobody sends without asking.** Decisions are queued when they are recorded, but a push is
#   sent only after a person with the push permission has *requested* it (`request_ims_push`) —
#   recording and telling a government are separate permissions, and a decision a supervisor has
#   not reviewed must not leave the building because a clerk recorded it.
# * **One call in flight per decision, and per document.** The claim is a conditional `UPDATE ...
#   RETURNING` under `FOR UPDATE SKIP LOCKED`, and `attempts` is the fencing token the record step
#   writes against — a worker whose lease lapsed cannot overwrite what the one that took over
#   recorded.
# * **"Already recorded" is success.** A crash between the call and the record is repaired by
#   asking again under the same key; the portal's answer is recorded as the success it is.


def _push_aggregate_id(organization_id: uuid.UUID, period: str) -> uuid.UUID:
    """A stable id for "this organization's pushes of this period" — the aggregate the period-level
    events are published under (an outbox row needs a UUID, and a period is not one)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"finance:ims-push:{organization_id}:{period}")


def _audit_push_row(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    push_id: uuid.UUID,
    action: str,
    after: dict[str, Any],
    request: Request | None = None,
) -> None:
    """One audit row for one push transition. The ledger of what the portal said is the push row;
    this is who asked and what came of it, append-only."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="ims_push",
        entity_id=push_id,
        after=after,
        request=request,
    )


def _push_audit_facts(push: ImsPush, **extra: Any) -> dict[str, Any]:
    return {
        "ims_document_id": str(push.ims_document_id),
        "decision_id": str(push.decision_id),
        "period": push.period,
        "action": push.action,
        "version": push.version,
        "status": push.status,
        "attempts": push.attempts,
        "error_code": push.error_code,
        **extra,
    }


async def request_ims_push(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    period: str,
    document_ids: list[uuid.UUID] | None,
    request: Request | None = None,
    now: datetime | None = None,
) -> ImsPushRequestOut:
    """A person asks for one return period's decisions to be sent — the request half. Stages
    `EVT_IMS_PUSH_REQUESTED` on the outbox and marks the pushes it covers as *asked for*; **no
    portal call and no claim here** (D3), so the request path never waits on a government.

    What a request covers: every current push of `period` that is `queued` (decided, waiting), and
    every `failed` one (a person choosing to send a refused decision again, with a fresh budget) —
    optionally only the named documents. `submitting`, `pushed` and `conflict` pushes are left
    exactly as they are and counted as `not_pushable`: in flight, done, or needing a new decision.

    Decisions recorded before pushes existed have no push row; each gets one here, queued and then
    covered by the same request, so a period's whole backlog is sendable without a migration having
    to touch the ledger. Refuses an organization with no GST registration (there is no ledger on
    the portal to record decisions against) and a named document that is not this organization's or
    not this period's — the whole request, before anything is written.
    """
    now = now or _now()
    try:
        domain.parse_period(period)
    except ValueError as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    # The documents first: another organization's id is a 404 whatever else is true of the caller's
    # own organization, and answering "no registration" first would let a probe tell the two apart.
    scope_ids: list[uuid.UUID] | None = None
    if document_ids is not None:
        scope_ids = list(dict.fromkeys(document_ids))
        found = await selectors.get_ims_documents(session, ctx.organization_id, scope_ids)
        if len(found) != len(scope_ids):
            raise NotFoundProblem(
                "One or more of those supplier documents is not part of this organization"
            )
        if any(doc.period != period for doc in found.values()):
            raise BusinessRuleProblem(
                f"Those documents are not all in {period}; a push is made one return period at "
                "a time"
            )
    if await selectors.get_gst_registration(session, ctx.organization_id) is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so there is no portal ledger "
            "to record decisions against"
        )

    legacy = await selectors.documents_with_unqueued_decisions(
        session, ctx.organization_id, period, document_ids=scope_ids
    )
    if legacy:
        counts = await selectors.decision_counts(
            session, ctx.organization_id, [doc.id for doc, _ in legacy]
        )
        for doc, decision in legacy:
            version = counts.get(doc.id, 1)
            session.add(
                ImsPush(
                    organization_id=ctx.organization_id,
                    created_by=str(ctx.principal.sub),
                    ims_document_id=doc.id,
                    decision_id=decision.id,
                    period=doc.period,
                    source_ref=doc.source_ref,
                    action=decision.action,
                    version=version,
                    idempotency_key=domain.ims_push_key(doc.period, doc.source_ref, version),
                    status="queued",
                    is_current=True,
                    attempts=0,
                    drift=False,
                )
            )
        await session.flush()

    stmt = (
        select(ImsPush)
        .where(
            ImsPush.organization_id == ctx.organization_id,
            ImsPush.period == period,
            ImsPush.is_current.is_(True),
            ImsPush.deleted_at.is_(None),
        )
        .order_by(ImsPush.id)
        .with_for_update()
    )
    if scope_ids is not None:
        stmt = stmt.where(ImsPush.ims_document_id.in_(scope_ids))
    rows = list((await session.execute(stmt)).scalars().all())
    pushable = [row for row in rows if row.status in ("queued", "failed")]
    for row in pushable:
        if row.status == "failed":
            # A person choosing to send it again: a fresh budget, and the last refusal cleared.
            row.status = "queued"
            row.attempts = 0
            row.error_code = None
            row.error = None
        row.requested_at = now
        row.requested_by = str(ctx.principal.sub)
        row.next_attempt_at = None
        row.lease_until = None
        row.updated_by = str(ctx.principal.sub)
    if pushable:
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ims.push_requested",
        entity="ims_push_request",
        entity_id=_push_aggregate_id(ctx.organization_id, period),
        after={
            "period": period,
            "requested": len(pushable),
            "not_pushable": len(rows) - len(pushable),
            "backfilled": len(legacy),
            "push_ids": [str(row.id) for row in pushable[:200]],
        },
        request=request,
    )
    if pushable:
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_IMS_PUSH_REQUESTED,
            aggregate_type="ims_push_period",
            aggregate_id=_push_aggregate_id(ctx.organization_id, period),
            payload={
                "organization_id": str(ctx.organization_id),
                "period": period,
                "requested": len(pushable),
                "requested_by": str(ctx.principal.sub),
            },
            actor=_actor(ctx),
        )
    return ImsPushRequestOut(
        period=period, requested=len(pushable), not_pushable=len(rows) - len(pushable)
    )


@dataclass(frozen=True, slots=True)
class _ClaimedPush:
    """One push a claim took: which row, and the `attempts` value that claim wrote — the fencing
    token `record_ims_push_batch` must find unchanged to record anything."""

    push_id: uuid.UUID
    attempts: int
    source_ref: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ImsPushClaim:
    """What `claim_ims_push_batch` decided. `batch` carries what the portal call still to come
    needs, as plain values, so nothing crosses the network call holding an ORM instance from a
    transaction that has closed."""

    outcome: str  # "batch" | "empty" | "no_registration"
    period: str
    gstin: str = ""
    items: tuple[ImsPushItem, ...] = ()
    claimed: tuple[_ClaimedPush, ...] = ()
    #: Pushes this claim ended **without** a call: budget spent, superseded by a newer decision, or
    #: no registration left to record them against.
    settled: int = 0


def _open_push_work(p: Any, now: datetime) -> Any:
    """SQL: a push of `p` that is waiting for a worker — asked for and either never attempted or
    past its backoff, or claimed and past its lease."""
    return or_(
        and_(
            p.status == "queued",
            p.requested_at.is_not(None),
            or_(p.next_attempt_at.is_(None), p.next_attempt_at <= now),
        ),
        and_(p.status == "submitting", p.lease_until.is_not(None), p.lease_until <= now),
    )


async def claim_ims_push_batch(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    period: str,
    now: datetime,
    limit: int,
    portal: str,
) -> ImsPushClaim:
    """The first act of a push: settle what needs no portal call, then claim up to `limit` of
    the period's waiting decisions for one.

    **Settled without a call**, in this order, each as a terminal row and an audit row:

    * a superseded decision still waiting (`is_current` false) — a newer decision exists, and
      sending the older one would tell GSTN something the person has since reversed;
    * no GST registration — nothing to record decisions against (`no_registration`);
    * a spent budget — `attempts` already at `domain.IMS_PUSH_MAX_ATTEMPTS` (`retry_exhausted`):
      the poisoned-step guard, so a decision the portal never answers becomes a failure a person
      sees rather than a row the sweep picks up on every tick forever.

    **Claimed** by one conditional `UPDATE ... RETURNING` over a `FOR UPDATE SKIP LOCKED` subselect:
    `submitting`, `attempts + 1` (the fencing token), a lease `domain.IMS_PUSH_LEASE` long. Two
    workers racing a period take disjoint rows; a decision whose document has an older decision
    still in flight (a live lease) is not taken, so GSTN's final state is always the newest one.
    Oldest request first.
    """
    org = ctx.organization_id
    actor = str(ctx.principal.sub)
    p = aliased(ImsPush)
    settled = 0

    async def settle(where: Any, *, status: str, code: str | None, error: str | None) -> int:
        result = await session.execute(
            update(ImsPush)
            .where(ImsPush.organization_id == org, ImsPush.period == period, where)
            .values(
                status=status,
                error_code=code,
                error=error,
                lease_until=None,
                next_attempt_at=None,
                updated_at=now,
                updated_by=actor,
            )
            .returning(ImsPush.id, ImsPush.source_ref, ImsPush.ims_document_id)
            .execution_options(synchronize_session=False)
        )
        settled_rows = result.all()
        for push_id, source_ref, document_id in settled_rows:
            _audit_push_row(
                session,
                ctx,
                push_id=push_id,
                action="ims.push_superseded" if status == "superseded" else "ims.push_failed",
                after={
                    "period": period,
                    "source_ref": source_ref,
                    "ims_document_id": str(document_id),
                    "status": status,
                    "error_code": code,
                },
            )
        return len(settled_rows)

    waiting = _open_push_work(ImsPush, now)
    settled += await settle(
        and_(ImsPush.deleted_at.is_(None), ImsPush.is_current.is_(False), waiting),
        status="superseded",
        code=None,
        error=None,
    )

    registration = await selectors.get_gst_registration(session, org)
    if registration is None:
        settled += await settle(
            and_(ImsPush.deleted_at.is_(None), waiting),
            status="failed",
            code=domain.ERR_NO_REGISTRATION,
            error="this organization no longer has a GST registration configured",
        )
        return ImsPushClaim("no_registration", period, settled=settled)

    settled += await settle(
        and_(
            ImsPush.deleted_at.is_(None),
            waiting,
            ImsPush.attempts >= domain.IMS_PUSH_MAX_ATTEMPTS,
        ),
        status="failed",
        code=domain.ERR_RETRY_EXHAUSTED,
        error=(
            f"gave up after {domain.IMS_PUSH_MAX_ATTEMPTS} attempts; the portal never confirmed "
            "this decision"
        ),
    )

    other = aliased(ImsPush)
    older_in_flight = (
        select(other.id)
        .where(
            other.organization_id == org,
            other.ims_document_id == p.ims_document_id,
            other.status == "submitting",
            other.lease_until > now,
            other.id != p.id,
        )
        .exists()
    )
    # Two statements in this one transaction, not one `UPDATE ... WHERE id IN (SELECT ... LIMIT n
    # FOR UPDATE SKIP LOCKED)`: the planner may re-evaluate that subselect per candidate row, and
    # under READ COMMITTED a LIMIT inside it then stops bounding the update — a batch of five went
    # to a portal that takes two. The row locks the first statement takes are held until commit, so
    # the second updates exactly the rows the first chose, and no other worker can have them.
    chosen = list(
        (
            await session.execute(
                select(p.id)
                .where(
                    p.organization_id == org,
                    p.period == period,
                    p.deleted_at.is_(None),
                    p.is_current.is_(True),
                    p.attempts < domain.IMS_PUSH_MAX_ATTEMPTS,
                    _open_push_work(p, now),
                    ~older_in_flight,
                )
                .order_by(p.requested_at, p.created_at, p.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if not chosen:
        return ImsPushClaim("empty", period, gstin=registration.gstin, settled=settled)
    result = await session.execute(
        update(ImsPush)
        .where(ImsPush.organization_id == org, ImsPush.id.in_(chosen))
        .values(
            status="submitting",
            attempts=ImsPush.attempts + 1,
            lease_until=now + domain.IMS_PUSH_LEASE,
            next_attempt_at=None,
            portal=portal,
            updated_at=now,
            updated_by=actor,
        )
        .returning(
            ImsPush.id,
            ImsPush.attempts,
            ImsPush.source_ref,
            ImsPush.idempotency_key,
            ImsPush.action,
            ImsPush.version,
        )
        .execution_options(synchronize_session=False)
    )
    taken = result.all()
    if not taken:
        return ImsPushClaim("empty", period, gstin=registration.gstin, settled=settled)
    return ImsPushClaim(
        "batch",
        period,
        gstin=registration.gstin,
        items=tuple(
            ImsPushItem(
                source_ref=source_ref,
                action=domain.ims_portal_action(action),
                version=version,
                idempotency_key=key,
            )
            for _, _, source_ref, key, action, version in taken
        ),
        claimed=tuple(
            _ClaimedPush(push_id, attempts, source_ref, key)
            for push_id, attempts, source_ref, key, _, _ in taken
        ),
        settled=settled,
    )


@dataclass(frozen=True, slots=True)
class _ImsPushOutcome:
    """What the portal did to one batch, folded into plain values — no session, no database.
    `answered` carries a verdict per document; `unavailable` (retried, with a backoff) and
    `rejected` (the whole request refused; terminal) carry the masked reason."""

    kind: str  # "answered" | "unavailable" | "rejected"
    results: dict[str, ImsPushResult] = dataclass_field(default_factory=dict)
    error_code: str | None = None
    error: str | None = None
    rate_limited: bool = False
    retry_after: timedelta | None = None


def call_ims_push(
    client: ImsClient, gstin: str, period: str, items: Sequence[ImsPushItem]
) -> _ImsPushOutcome:
    """The second act of a push, and the only one that touches the portal. Called with **no
    session and no transaction open** (D3).

    A batch that reached the portal is `answered` document by document. An outage — a timeout, a
    5xx, and, as `ImsRateLimited` (checked first, the more specific kind), a rate limit whose
    `Retry-After` the retry honours — is `unavailable`: nothing is known to have been recorded and
    every decision is sent again under the same key. A whole-request refusal is `rejected`. Every
    word the portal sent back is masked here, once."""
    try:
        answers = client.push_ims_decisions(gstin=gstin, period=period, decisions=list(items))
    except ImsPushRejected as rejected:
        return _ImsPushOutcome(
            kind="rejected",
            error_code=rejected.code,
            error=domain.mask_portal_text(rejected.message),
        )
    except ImsRateLimited as limited:
        return _ImsPushOutcome(
            kind="unavailable",
            error=domain.mask_portal_text(str(limited)),
            rate_limited=True,
            retry_after=limited.retry_after,
        )
    except ImsUnavailable as outage:
        return _ImsPushOutcome(kind="unavailable", error=domain.mask_portal_text(str(outage)))
    return _ImsPushOutcome(kind="answered", results={a.source_ref: a for a in answers})


async def call_ims_push_off_loop(
    client: ImsClient, gstin: str, period: str, items: Sequence[ImsPushItem]
) -> _ImsPushOutcome:
    """`call_ims_push` on a worker thread, bounded (`off_loop`); a timeout is an outage."""
    return await off_loop(
        call_ims_push,
        client,
        gstin,
        period,
        items,
        on_timeout=lambda s: _ImsPushOutcome(kind="unavailable", error=_timed_out(s)),
    )


@dataclass(frozen=True, slots=True)
class ImsPushRecord:
    """What one record step wrote. `skipped` are claims this recorder no longer held — a lease that
    lapsed and was taken over — which it correctly wrote nothing for."""

    pushed: int = 0
    failed: int = 0
    conflict: int = 0
    retry: int = 0
    skipped: int = 0


async def record_ims_push_batch(
    session: AsyncSession,
    ctx: ScopeContext,
    claim: ImsPushClaim,
    outcome: _ImsPushOutcome,
    *,
    portal: str,
    now: datetime,
    request: Request | None = None,
) -> ImsPushRecord:
    """The third act: write what the portal decided, per decision, and audit each.

    * `recorded` / `already_recorded` → `pushed` — *already recorded is success by fetch*. Unless
      the portal says it holds a **different** action than this decision's, which is a `conflict`
      wearing a success's shape, and is recorded as one.
    * a per-document refusal → `failed`, with the portal's code and its masked words.
    * `conflict` → `conflict`: the portal holds something else; only a new decision resolves it.
    * an outage, or a document the portal did not answer for → back to `queued` with the bounded
      backoff (`domain.ims_push_retry_delay`, honouring a `Retry-After` up to the ceiling) — or,
      with the budget spent, `failed(retry_exhausted)`. A whole-request refusal fails every
      decision in the batch.

    **Fenced**: a row is written only if it is still `submitting` with the `attempts` this claim
    took, so a late recorder whose lease lapsed cannot overwrite what the worker that took over
    recorded. Idempotent the same way — a second recorder of the same claim finds nothing to write.
    Every row is loaded in one query and written in one flush.
    """
    if not claim.claimed:
        return ImsPushRecord()
    actor = str(ctx.principal.sub)
    stmt = (
        select(ImsPush)
        .where(
            ImsPush.organization_id == ctx.organization_id,
            tuple_(ImsPush.id, ImsPush.attempts).in_(
                [(held.push_id, held.attempts) for held in claim.claimed]
            ),
            ImsPush.status == "submitting",
        )
        .order_by(ImsPush.id)
        .with_for_update()
    )
    rows = list((await session.execute(stmt)).scalars().all())
    skipped = len(claim.claimed) - len(rows)

    pushed = failed = conflict = retry = 0
    problems: list[dict[str, Any]] = []

    def retry_or_exhaust(
        row: ImsPush, *, code: str, error: str | None, after: timedelta | None
    ) -> str:
        row.lease_until = None
        row.portal = portal
        if row.attempts >= domain.IMS_PUSH_MAX_ATTEMPTS:
            row.status = "failed"
            row.error_code = domain.ERR_RETRY_EXHAUSTED
            row.error = (
                f"gave up after {row.attempts} attempts; the portal was last unreachable with: "
                f"{error}"
            )[: domain.PORTAL_TEXT_LIMIT + 200]
            row.next_attempt_at = None
            return "failed"
        delay = domain.ims_push_retry_delay(row.attempts)
        if after is not None:
            # A portal that says how long it wants is obeyed, up to the platform's own ceiling.
            delay = max(delay, min(after, domain.IMS_PUSH_RETRY_CEILING))
        row.status = "queued"
        row.error_code = code
        row.error = error
        row.next_attempt_at = now + delay
        return "retry"

    for row in rows:
        row.updated_at = now
        row.updated_by = actor
        expected = domain.ims_portal_action(row.action)
        result = outcome.results.get(row.source_ref) if outcome.kind == "answered" else None
        disposition: str
        audit_action: str

        if outcome.kind == "rejected":
            row.status = "failed"
            row.error_code = outcome.error_code
            row.error = outcome.error
            row.lease_until = None
            row.portal = portal
            disposition, audit_action = "failed", "ims.push_failed"
        elif outcome.kind == "unavailable":
            disposition = retry_or_exhaust(
                row,
                code=domain.ERR_RATE_LIMITED
                if outcome.rate_limited
                else domain.ERR_PORTAL_UNAVAILABLE,
                error=outcome.error,
                after=outcome.retry_after,
            )
            audit_action = (
                "ims.push_failed" if disposition == "failed" else "ims.push_retry_scheduled"
            )
        elif result is None:
            disposition = retry_or_exhaust(
                row,
                code=domain.ERR_PORTAL_UNAVAILABLE,
                error="the portal did not answer for this document",
                after=None,
            )
            audit_action = (
                "ims.push_failed" if disposition == "failed" else "ims.push_retry_scheduled"
            )
        elif result.outcome in ("recorded", "already_recorded") and result.recorded_action in (
            None,
            expected,
        ):
            row.status = "pushed"
            row.pushed_at = now
            row.error_code = None
            row.error = None
            row.lease_until = None
            row.portal = portal
            row.observed_action = result.recorded_action or expected
            row.observed_at = now
            row.drift = False
            disposition, audit_action = "pushed", "ims.pushed"
        elif result.outcome == "rejected":
            row.status = "failed"
            row.error_code = result.code or "rejected"
            row.error = domain.mask_portal_text(result.message)
            row.lease_until = None
            row.portal = portal
            disposition, audit_action = "failed", "ims.push_failed"
        elif result.outcome in ("conflict", "already_recorded", "recorded"):
            # `conflict`, and also an "already recorded" that names a different action than ours:
            # the portal holds something else for this row, and a retry would only repeat it.
            row.status = "conflict"
            row.error_code = result.code or "conflict"
            row.error = domain.mask_portal_text(
                result.message
                or f"the portal holds {result.recorded_action!r} here, not {expected!r}"
            )
            row.lease_until = None
            row.portal = portal
            row.observed_action = result.recorded_action
            row.observed_at = now
            disposition, audit_action = "conflict", "ims.push_conflict"
        else:
            disposition = retry_or_exhaust(
                row,
                code=domain.ERR_PORTAL_UNAVAILABLE,
                error="the portal answered in a form this platform does not understand",
                after=None,
            )
            audit_action = (
                "ims.push_failed" if disposition == "failed" else "ims.push_retry_scheduled"
            )

        if disposition == "pushed":
            pushed += 1
        elif disposition == "conflict":
            conflict += 1
        elif disposition == "failed":
            failed += 1
        else:
            retry += 1
        if disposition in ("failed", "conflict"):
            problems.append(
                {"source_ref": row.source_ref, "status": row.status, "error_code": row.error_code}
            )
        _audit_push_row(
            session,
            ctx,
            push_id=row.id,
            action=audit_action,
            after=_push_audit_facts(
                row,
                source_ref=row.source_ref,
                next_attempt_at=row.next_attempt_at.isoformat() if row.next_attempt_at else None,
                idempotency_key=row.idempotency_key,
            ),
            request=request,
        )

    if rows:
        await session.flush()
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_IMS_PUSH_COMPLETED,
            aggregate_type="ims_push_period",
            aggregate_id=_push_aggregate_id(ctx.organization_id, claim.period),
            payload={
                "organization_id": str(ctx.organization_id),
                "period": claim.period,
                "portal": portal,
                "pushed": pushed,
                "failed": failed,
                "conflict": conflict,
                "retry_scheduled": retry,
                #: The decisions a person now has to look at, by portal reference and code only —
                #: nothing the portal said, so the event carries no supplier's words.
                "needs_attention": problems[:100],
            },
            actor=_actor(ctx),
        )
    return ImsPushRecord(
        pushed=pushed, failed=failed, conflict=conflict, retry=retry, skipped=skipped
    )


@dataclass(slots=True)
class ImsPushRun:
    """What one run over a period did, summed over its batches."""

    batches: int = 0
    pushed: int = 0
    failed: int = 0
    conflict: int = 0
    retry: int = 0
    settled: int = 0
    skipped: int = 0
    #: True when the run stopped at its batch cap with work still waiting.
    more: bool = False

    @property
    def acted(self) -> int:
        return self.pushed + self.failed + self.conflict + self.retry + self.settled


WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


async def drive_ims_push_period(
    work_scope: WorkScope,
    client: ImsClient,
    organization_id: uuid.UUID,
    period: str,
    *,
    now: datetime | None = None,
    max_batches: int = domain.IMS_PUSH_MAX_BATCHES_PER_RUN,
) -> ImsPushRun:
    """Send one period's waiting decisions: claim, call, record — repeated a batch at a time.

    The one function the consumer of `EVT_IMS_PUSH_REQUESTED` and the reclaim sweep both call, so a
    push a person asked for and a push a crash left half-done are the same code. Each claim and each
    record is its own short transaction under `work_scope` (the tenant-bound `session_scope`); the
    portal call in between runs with **no session open, on a worker thread** (D3). It stops when the
    period has nothing waiting, when the portal is unreachable (the rows carry their own backoff —
    hammering an outage from a loop would only spend the budget), or at `max_batches`, in which case
    `more` is set and the next tick continues.
    """
    ctx = service_ctx(organization_id)
    limit = max(1, min(client.push_batch_limit, domain.IMS_PUSH_BATCH_MAX))
    run = ImsPushRun()
    for _ in range(max_batches):
        clock = now or _now()
        async with work_scope(organization_id) as session:
            claim = await claim_ims_push_batch(
                session, ctx, period=period, now=clock, limit=limit, portal=client.name
            )
        run.settled += claim.settled
        if claim.outcome != "batch":
            return run

        outcome = await call_ims_push_off_loop(client, claim.gstin, period, claim.items)

        async with work_scope(organization_id) as session:
            recorded = await record_ims_push_batch(
                session, ctx, claim, outcome, portal=client.name, now=clock
            )
        run.batches += 1
        run.pushed += recorded.pushed
        run.failed += recorded.failed
        run.conflict += recorded.conflict
        run.retry += recorded.retry
        run.skipped += recorded.skipped
        if outcome.kind != "answered":
            return run
    run.more = True
    return run


@dataclass(frozen=True, slots=True)
class DriftResult:
    detected: int = 0
    cleared: int = 0


_DRIFT_CHUNK = 500


async def flag_ims_drift(
    session: AsyncSession,
    ctx: ScopeContext,
    fetched: Sequence[ImsSupplierDocument],
    *,
    at: datetime,
) -> DriftResult:
    """After a sync: does the portal now hold what was pushed? The reconciliation half of C2.

    For every fetched document whose portal row has a **current, `pushed`** decision, compare what
    the portal reports as the taxpayer's action (`ImsSupplierDocument.action`) with what was pushed.
    A disagreement sets `drift` (and announces it once, on the sync that first sees it); the two
    agreeing again clears it. A push still `queued`, `failed` or `conflict` is never compared — it
    is *known* not to match; drift means "we told GSTN, and GSTN does not say so". A portal that
    reports no action for a row (`None`) is silence, not disagreement.

    One query per chunk of references and one write per group — never a query per document.
    Called inside `record_ims_fetch`'s transaction.
    """
    # An action outside the portal vocabulary (`domain.IMS_PORTAL_ACTIONS`) is an adapter that did
    # not map its answer, and is treated as silence rather than compared: a word this platform
    # does not know cannot be a disagreement about a decision it does.
    known = set(domain.IMS_PORTAL_ACTIONS.values())
    observed = {doc.source_ref: doc.action for doc in fetched if doc.action in known}
    if not observed:
        return DriftResult()
    refs = list(observed)
    detected = cleared = 0
    newly: list[dict[str, Any]] = []
    for start in range(0, len(refs), _DRIFT_CHUNK):
        chunk = refs[start : start + _DRIFT_CHUNK]
        rows = (
            (
                await session.execute(
                    select(ImsPush)
                    .where(
                        ImsPush.organization_id == ctx.organization_id,
                        ImsPush.is_current.is_(True),
                        ImsPush.status == "pushed",
                        ImsPush.deleted_at.is_(None),
                        ImsPush.source_ref.in_(chunk),
                    )
                    .order_by(ImsPush.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            seen = observed[row.source_ref]
            drifted = domain.ims_is_drift(pushed_action=row.action, observed=seen)
            row.observed_action = seen
            row.observed_at = at
            if drifted and not row.drift:
                row.drift = True
                row.drift_detected_at = at
                detected += 1
                newly.append(
                    {
                        "source_ref": row.source_ref,
                        "pushed": domain.ims_portal_action(row.action),
                        "observed": seen,
                    }
                )
                _audit_push_row(
                    session,
                    ctx,
                    push_id=row.id,
                    action="ims.push_drift",
                    after=_push_audit_facts(row, observed_action=seen),
                )
            elif not drifted and row.drift:
                row.drift = False
                cleared += 1
                _audit_push_row(
                    session,
                    ctx,
                    push_id=row.id,
                    action="ims.push_drift_cleared",
                    after=_push_audit_facts(row, observed_action=seen),
                )
    if detected or cleared:
        await session.flush()
    if newly:
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_IMS_PUSH_DRIFT_DETECTED,
            aggregate_type="ims_push_period",
            aggregate_id=_push_aggregate_id(ctx.organization_id, fetched[0].period),
            payload={
                "organization_id": str(ctx.organization_id),
                "period": fetched[0].period,
                "detected": detected,
                "items": newly[:100],
            },
            actor=_actor(ctx),
        )
    return DriftResult(detected=detected, cleared=cleared)


# --------------------------------------------------------------------- GST returns
# Assembling a return is a *read* of the book followed by one write: the snapshot. Nothing here
# computes tax — `domain.assemble_gstr1` sums the splits the invoices already carry — so the
# return foots to the documents it is made of by construction rather than by a reconciliation
# step somebody has to remember to run.


async def generate_return(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: GenerateReturnIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> GstReturn:
    """Assemble one period's return and freeze it as the snapshot of what would be reported.

    Two things this deliberately does **not** do.

    It does not file. Generating is a statement about this platform's own book; filing is a claim
    made to a government, and collapsing them would mean an operator could not look at the return
    before it went.

    And it does not touch a **filed** return. Once a period has been filed, the snapshot is what
    was reported, and an invoice corrected afterwards is a *difference* — read through
    `diff_return`, declared as an amendment in a later period, which is GST's own remedy and the
    same shape as a credit note against a filed invoice. Regenerating over the top would erase
    the only record of what the filing actually said.
    """
    at = at or _now()
    if payload.return_type not in domain.GST_RETURN_TYPES:
        types = ", ".join(sorted(domain.GST_RETURN_TYPES))
        raise BusinessRuleProblem(f"Unknown return type {payload.return_type!r} (one of: {types})")
    try:
        domain.parse_period(payload.period)
    except ValueError as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    is_gstr3b = payload.return_type == "gstr3b"
    # The registration row is locked for the length of the whole assembly when generating a
    # gstr3b, so a concurrent generate or file for the *same* registration serialises on it
    # rather than each reading the ITC ledger's position before the other has committed what it
    # decided to do with it.
    registration = await registration_for(
        session, ctx, payload.gst_registration_id, for_update=is_gstr3b
    )
    if is_gstr3b:
        if not registration.is_primary:
            raise BusinessRuleProblem(
                "GSTR-3B can only be generated for this organization's primary registration "
                "today — every ITC ledger posting is attributed to it, since the IMS sync this "
                "platform drives carries no registration of its own to attribute a posting to"
            )
        pending = await selectors.gstr3b_pending_periods_before(
            session, ctx.organization_id, registration.id, payload.period
        )
        if pending:
            raise BusinessRuleProblem(
                f"{pending[0]}'s GSTR-3B has been generated but not filed; file it before "
                f"generating {payload.period}'s — filing out of order can utilise the same "
                "brought-forward credit against two periods"
            )
        later = await selectors.gstr3b_periods_after(
            session, ctx.organization_id, registration.id, payload.period
        )
        if later:
            raise BusinessRuleProblem(
                f"{later[0]}'s GSTR-3B already exists for this registration; generating "
                f"{payload.period}'s now would let it be filed after {later[0]}'s and utilise "
                f"{later[0]}'s brought-forward credit a second time — GSTR-3B must be generated "
                "and filed in period order, forwards only"
            )
        gstr3b = await assemble_gstr3b_for(
            session, ctx, registration=registration, period=payload.period
        )
        sections: Gstr1Sections | Gstr3bSections = selectors.to_gstr3b_sections(gstr3b)
        # The header totals declare the *whole* of Table 3.1 — (a) + (b) + the reverse-charge
        # figure — matching what GSTR-1 declares for the same period, since that is what a list
        # page and `gstr3b_reconciles_with_gstr1` both compare against. `payment.liability`
        # deliberately differs: it excludes the reverse-charge figure, because that tax is the
        # recipient's to pay, not this organization's.
        declared = (
            gstr3b.outward.taxable_supplies
            + gstr3b.outward.zero_rated
            + gstr3b.outward.outward_reverse_charge
        )
        totals = domain.ReturnTotals(
            taxable_minor=(
                gstr3b.outward.outward_taxable_minor
                + gstr3b.outward.zero_rated_taxable_minor
                + gstr3b.outward.outward_reverse_charge_taxable_minor
            ),
            cgst_minor=declared.cgst_minor,
            sgst_minor=declared.sgst_minor,
            igst_minor=declared.igst_minor,
            document_count=gstr3b.document_count,
            line_count=0,
        )
    else:
        assembled = await assemble_return_for(
            session, ctx, period=payload.period, gstin=registration.gstin
        )
        sections = selectors.to_sections(assembled)
        totals = assembled.totals

    existing = await selectors.find_return(
        session,
        ctx.organization_id,
        registration_id=registration.id,
        period=payload.period,
        return_type=payload.return_type,
    )
    if existing is not None and existing.status == "filed":
        raise ConflictProblem(
            f"The {payload.return_type} for {payload.period} was filed on "
            f"{existing.filed_at.isoformat() if existing.filed_at else 'an earlier date'} under "
            f"ARN {existing.arn}; a later correction is an amendment in a later period, not a "
            "regeneration — read the difference instead"
        )

    ret = existing
    if ret is None:
        ret = GstReturn(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            gst_registration_id=registration.id,
            gstin=registration.gstin,
            state_code=registration.state_code,
            period=payload.period,
            return_type=payload.return_type,
            status="draft",
        )
        session.add(ret)
        await session.flush()

    from_status = ret.status
    if not domain.may_transition_return(from_status, "generated"):
        raise BusinessRuleProblem(f"A {from_status} return cannot be generated")

    # The snapshot. Written through the Pydantic contract rather than as a loose dict, so what is
    # stored is exactly what will be served back, and a shape nothing describes cannot be
    # persisted in the first place.
    ret.sections = sections.model_dump(mode="json")
    ret.gstin = registration.gstin
    ret.state_code = registration.state_code
    ret.total_taxable_minor = totals.taxable_minor
    ret.total_cgst_minor = totals.cgst_minor
    ret.total_sgst_minor = totals.sgst_minor
    ret.total_igst_minor = totals.igst_minor
    ret.document_count = totals.document_count
    ret.line_count = totals.line_count
    ret.generated_at = at
    ret.status = "generated"
    ret.updated_by = str(ctx.principal.sub)
    await session.flush()

    _append_transition(
        session,
        ctx,
        ret,
        from_status=from_status,
        to_status="generated",
        at=at,
        note=f"assembled from {totals.document_count} documents",
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="gst_return.generated",
        entity="gst_return",
        entity_id=ret.id,
        before={"status": from_status},
        after={
            "status": ret.status,
            "period": ret.period,
            "return_type": ret.return_type,
            "total_taxable_minor": ret.total_taxable_minor,
            "document_count": ret.document_count,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_RETURN_GENERATED,
        aggregate_type="gst_return",
        aggregate_id=ret.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "gst_return_id": str(ret.id),
            "gstin": ret.gstin,
            "period": ret.period,
            "return_type": ret.return_type,
            "total_taxable_minor": ret.total_taxable_minor,
            "total_tax_minor": (ret.total_cgst_minor + ret.total_sgst_minor + ret.total_igst_minor),
            "document_count": ret.document_count,
            "generated_at": at.isoformat(),
        },
        actor=_actor(ctx),
    )
    return ret


async def file_return(
    session: AsyncSession,
    ctx: ScopeContext,
    return_id: uuid.UUID,
    payload: FileReturnIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> GstReturn:
    """Record that a generated return went to the portal, with the ARN it came back with.

    A separate, dangerous permission from generating, because it is a separate act: generating
    says what this platform believes, filing says what was told to a tax authority. The
    transition is appended to the trail with the totals as they stood, so the evidence row is
    self-contained — reading it never depends on the header still saying the same thing.

    **Filing a `gstr3b` also posts its utilisation to the ITC ledger**, in this same transaction,
    behind four guards, in this order:

    1. The registration is locked `FOR UPDATE` before anything else, so a concurrent generate or
       file for the same registration serialises on it rather than each acting on a stale read of
       the ledger.
    2. Every earlier period's `gstr3b` for this registration must already be filed — the same
       check `generate_return` runs, repeated here because a period can be generated in order and
       still raced to be *filed* out of order.
    3. No later period's `gstr3b` may already exist (filed or merely generated) — filing
       backwards after a later period has already drawn on this ledger's brought-forward balance
       would let both periods spend the same credit, since brought-forward is read off the ledger
       directly rather than off any particular return.
    4. The ITC side of the frozen snapshot is recomputed against the ledger **as it now stands**
       and compared to what was generated. If a decision landed on the ledger between generation
       and filing — the exact shape of "accept, generate, reject, file" leaving a negative
       balance — the two disagree and filing is refused: regenerate first, so what gets filed is
       what the ledger currently says, not what it said a moment that has since passed.

    Table 3.1's own figures are not recomputed here; only the ITC ledger position is, since that
    is the part a decision taken between generation and filing can move.
    """
    at = at or _now()
    ret = await selectors.get_return(session, ctx.organization_id, return_id)
    if ret is None:
        raise NotFoundProblem("That return is not part of this organization")
    if not payload.arn.strip():
        raise BusinessRuleProblem("Filing a return requires the portal's acknowledgement number")

    is_gstr3b = ret.return_type == "gstr3b"
    if is_gstr3b:
        # Locked before the transition check below is even read, so a concurrent file of this
        # exact return also serialises here rather than racing past `may_transition_return`.
        registration = await registration_for(
            session, ctx, ret.gst_registration_id, for_update=True
        )
        pending = await selectors.gstr3b_pending_periods_before(
            session, ctx.organization_id, registration.id, ret.period
        )
        if pending:
            raise BusinessRuleProblem(
                f"{pending[0]}'s GSTR-3B has been generated but not filed; file it before "
                f"filing {ret.period}'s — filing out of order can utilise the same "
                "brought-forward credit against two periods"
            )
        later = await selectors.gstr3b_periods_after(
            session, ctx.organization_id, registration.id, ret.period
        )
        if later:
            raise BusinessRuleProblem(
                f"{later[0]}'s GSTR-3B already exists for this registration; filing "
                f"{ret.period}'s now would utilise brought-forward credit {later[0]}'s return "
                "already used — GSTR-3B must be generated and filed in period order, forwards "
                "only"
            )

    if not domain.may_transition_return(ret.status, "filed"):
        if ret.status == "filed":
            raise ConflictProblem(
                f"The {ret.return_type} for {ret.period} is already filed under ARN {ret.arn}"
            )
        raise BusinessRuleProblem(
            f"A {ret.status} return cannot be filed; generate it first so there is a snapshot of "
            "what was reported"
        )

    if is_gstr3b and ret.sections:
        stored = Gstr3bSections.model_validate(ret.sections)
        recomputed = await _recompute_gstr3b_payment(
            session, ctx.organization_id, registration.id, ret.period, stored
        )
        if not _gstr3b_payment_matches(stored.payment, recomputed):
            raise ConflictProblem(
                f"The ITC ledger has moved since {ret.period}'s GSTR-3B was generated — "
                "regenerate it so what is filed matches the ledger as it now stands"
            )
        # `line.amount_minor` is a positive "amount applied" figure and a head can carry more than
        # one line (IGST commonly has three: against itself, against CGST, against SGST) — summed
        # per head, not overwritten, and never negated: it is subtracted from the balance below,
        # not added to it. Checked against the registration's *whole* ledger balance, not this
        # period's alone, so a later period's own postings — already on the ledger by the time this
        # runs — are not invisible to a check that exists to catch exactly that kind of drift.
        consumed: dict[str, int] = {}
        for line in stored.payment.utilisation:
            consumed[line.head] = consumed.get(line.head, 0) + line.amount_minor
        balance = await selectors.itc_total_balance(session, ctx.organization_id, registration.id)
        for head in domain.ITC_TAX_HEAD_ORDER:
            if balance.for_head(head) - consumed.get(head, 0) < 0:
                raise BusinessRuleProblem(
                    f"Filing {ret.period}'s GSTR-3B would take the {head.upper()} "
                    "ITC balance negative — regenerate the return before filing it"
                )

    from_status = ret.status
    ret.status = "filed"
    ret.filed_at = at
    ret.arn = payload.arn.strip()
    ret.updated_by = str(ctx.principal.sub)
    await session.flush()

    if is_gstr3b and ret.sections:
        stored = Gstr3bSections.model_validate(ret.sections)
        consumed = {}
        for line in stored.payment.utilisation:
            consumed[line.head] = consumed.get(line.head, 0) + line.amount_minor
        utilisation_amounts = domain.TaxHeadAmounts(
            igst_minor=-consumed.get("igst", 0),
            cgst_minor=-consumed.get("cgst", 0),
            sgst_minor=-consumed.get("sgst", 0),
            cess_minor=-consumed.get("cess", 0),
        )
        entries = await _post_itc_entries(
            session,
            ctx,
            gst_registration_id=ret.gst_registration_id,
            period=ret.period,
            source_period=ret.period,
            kind="utilisation",
            amounts=utilisation_amounts,
            source_type="gst_return",
            source_id=ret.id,
            description=f"GSTR-3B {ret.period} filed under ARN {ret.arn}",
            at=at,
        )
        _publish_itc_posting(
            session,
            ctx,
            event_type=EVT_ITC_UTILISED,
            aggregate_type="gst_return",
            aggregate_id=ret.id,
            gst_registration_id=ret.gst_registration_id,
            gstin=ret.gstin,
            period=ret.period,
            entries=entries,
            at=at,
        )

    _append_transition(
        session,
        ctx,
        ret,
        from_status=from_status,
        to_status="filed",
        at=at,
        arn=ret.arn,
        note=payload.note,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="gst_return.filed",
        entity="gst_return",
        entity_id=ret.id,
        before={"status": from_status},
        after={"status": ret.status, "arn": ret.arn, "period": ret.period},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_RETURN_FILED,
        aggregate_type="gst_return",
        aggregate_id=ret.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "gst_return_id": str(ret.id),
            "gstin": ret.gstin,
            "period": ret.period,
            "return_type": ret.return_type,
            "arn": ret.arn,
            "total_taxable_minor": ret.total_taxable_minor,
            "filed_at": at.isoformat(),
        },
        actor=_actor(ctx),
    )
    return ret


async def assemble_return_for(
    session: AsyncSession, ctx: ScopeContext, *, period: str, gstin: str
) -> domain.Gstr1Return:
    """Assemble a period from the book as it stands **now**. A pure read — nothing is written.

    Used twice, and that is the point: once by `generate_return` to make the snapshot, and again
    by `diff_return` to compute what the book says today. Two callers, one assembly, so the
    difference between a snapshot and the present can only ever be the *data*.
    """
    documents = await selectors.return_documents(session, ctx.organization_id, period)
    return domain.assemble_gstr1(documents, period=period, gstin=gstin)


async def assemble_gstr3b_for(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    registration: GstRegistration,
    period: str,
) -> domain.Gstr3bReturn:
    """Assemble one period's GSTR-3B from the book and the ITC ledger as they stand **now**. A
    pure read — nothing is written, including to the ledger.

    Reconciled against a **freshly assembled** GSTR-1 for the same period rather than against
    whatever `gst_returns` row happens to exist for it — a GSTR-3B may be generated before its
    GSTR-1 counterpart, the same way GSTR-1 itself may be regenerated any number of times before
    it is filed, and the two are always built from the same documents. `domain.assemble_gstr3b`
    checks the two foot to each other and raises if they ever do not, which given they are built
    from one call to `selectors.return_documents` should never happen — the check is defensive,
    not load-bearing.
    """
    documents = await selectors.return_documents(session, ctx.organization_id, period)
    gstr1_totals = domain.assemble_gstr1(documents, period=period, gstin=registration.gstin).totals

    brought_forward = await selectors.itc_brought_forward(
        session, ctx.organization_id, registration.id, period
    )
    accrued, reversed_ = await selectors.itc_period_movement(
        session, ctx.organization_id, registration.id, period
    )
    blocked = await selectors.itc_blocked_amounts_for_period(
        session, ctx.organization_id, registration.id, period
    )

    gstr3b = domain.assemble_gstr3b(
        documents,
        period=period,
        gstin=registration.gstin,
        itc_brought_forward=brought_forward,
        itc_accrued=accrued,
        itc_reversed=reversed_,
        itc_blocked=blocked,
    )
    if not domain.gstr3b_reconciles_with_gstr1(gstr3b, gstr1_totals):
        raise BusinessRuleProblem(
            f"GSTR-3B Table 3.1 for {period} does not reconcile with GSTR-1 for the same "
            "period — this should never happen, since both are assembled from the same book"
        )
    return gstr3b


def _tax_head_out_equals(out: TaxHeadAmountsOut, dc: domain.TaxHeadAmounts) -> bool:
    return (
        out.igst_minor == dc.igst_minor
        and out.cgst_minor == dc.cgst_minor
        and out.sgst_minor == dc.sgst_minor
        and out.cess_minor == dc.cess_minor
    )


async def _recompute_gstr3b_payment(
    session: AsyncSession,
    org_id: uuid.UUID,
    gst_registration_id: uuid.UUID,
    period: str,
    stored: Gstr3bSections,
) -> domain.UtilisationResult:
    """What Table 6.1 would compute **right now** against the ledger, holding Table 3.1's
    liability fixed at whatever the frozen snapshot already declared it to be.

    Used only by `file_return`'s pre-flight check. Table 3.1 is not recomputed at file time — an
    invoice recorded between generation and filing is the same "regenerate to see it" story
    `diff_return` already tells for GSTR-1 — but the ITC ledger position genuinely can have moved:
    a late decision landing between generation and filing. That is exactly what this re-derives,
    so filing against a stale ITC position is refused rather than posting a utilisation the ledger
    no longer actually holds.
    """
    brought_forward = await selectors.itc_brought_forward(
        session, org_id, gst_registration_id, period
    )
    accrued, reversed_ = await selectors.itc_period_movement(
        session, org_id, gst_registration_id, period
    )
    blocked = await selectors.itc_blocked_amounts_for_period(
        session, org_id, gst_registration_id, period
    )
    itc = domain.Gstr3bItc(
        available=accrued + blocked,
        reversed_section_17_5=blocked,
        reversed_other=reversed_,
        reclaimed=domain.ZERO_ITC,
        ineligible_other=domain.ZERO_ITC,
    )
    liability = domain.TaxHeadAmounts(
        igst_minor=stored.payment.liability.igst_minor,
        cgst_minor=stored.payment.liability.cgst_minor,
        sgst_minor=stored.payment.liability.sgst_minor,
        cess_minor=stored.payment.liability.cess_minor,
    )
    return domain.utilise_itc(brought_forward + itc.net_eligible, liability)


def _gstr3b_payment_matches(stored: Gstr3bPaymentOut, recomputed: domain.UtilisationResult) -> bool:
    """Whether a freshly recomputed utilisation agrees with what was frozen at generation — line
    for line, not merely in total: two different allocations can foot to the same cash figure
    while consuming a different head of credit to get there."""
    stored_lines = {(line.head, line.against, line.amount_minor) for line in stored.utilisation}
    recomputed_lines = {(line.head, line.against, line.amount_minor) for line in recomputed.lines}
    return (
        stored_lines == recomputed_lines
        and _tax_head_out_equals(stored.cash_payable, recomputed.cash_payable)
        and _tax_head_out_equals(stored.credit_remaining, recomputed.credit_remaining)
    )


async def diff_return(
    session: AsyncSession,
    ctx: ScopeContext,
    return_id: uuid.UUID,
    *,
    at: datetime | None = None,
) -> ReturnDiffOut:
    """What has changed since the return was generated, per section and in total.

    The endpoint the frozen snapshot exists for. A credit note raised in September against an
    August invoice does not make August a different return — it makes a difference, and a
    difference is what an amendment declares. Computing it here means the amendment is prepared
    from an answer rather than from somebody's memory of what August looked like.

    **`gstr1` only.** A `gstr3b` diff would mean re-deriving the ITC ledger's position at two
    different moments as well as the book, which is a real feature and not this one — out of
    scope for this slice; see the README. A `gstr3b` is generated afresh each time instead, and
    `may_transition_return` already allows `generated → generated` for exactly that.
    """
    at = at or _now()
    ret = await selectors.get_return(session, ctx.organization_id, return_id)
    if ret is None:
        raise NotFoundProblem("That return is not part of this organization")
    if ret.return_type != "gstr1":
        raise BusinessRuleProblem(
            f"Diffing is only supported for gstr1 today; regenerate {ret.return_type} instead — "
            "it is always assembled fresh from the book and the ITC ledger as they stand now"
        )
    if ret.sections is None:
        raise BusinessRuleProblem(
            f"The {ret.return_type} for {ret.period} has not been generated, so there is no "
            "snapshot to compare the book against"
        )

    snapshot = domain.ReturnTotals(
        taxable_minor=ret.total_taxable_minor,
        cgst_minor=ret.total_cgst_minor,
        sgst_minor=ret.total_sgst_minor,
        igst_minor=ret.total_igst_minor,
        document_count=ret.document_count,
        line_count=ret.line_count,
    )
    current_return = await assemble_return_for(session, ctx, period=ret.period, gstin=ret.gstin)
    stored = Gstr1Sections.model_validate(ret.sections)

    sections: list[SectionDeltaOut] = []
    for name in domain.GSTR1_SECTIONS:
        if name == "docs":
            # A document series is a range of strings, not a set of figures; there is nothing to
            # subtract. It shows up in the sections of both snapshots and is deliberately left
            # out of the arithmetic rather than given a made-up delta.
            continue
        before = _snapshot_section_totals(name, stored)
        after = domain.section_totals(name, current_return)
        delta = domain.totals_delta(before, after)
        sections.append(
            SectionDeltaOut(
                section=name,
                snapshot=selectors.to_totals_out(before),
                current=selectors.to_totals_out(after),
                delta=selectors.to_totals_out(delta),
                changed=_is_nonzero(delta),
            )
        )

    total_delta = domain.totals_delta(snapshot, current_return.totals)
    return ReturnDiffOut(
        ret=selectors.to_return_out(ret),
        current=selectors.to_totals_out(current_return.totals),
        snapshot=selectors.to_totals_out(snapshot),
        delta=selectors.to_totals_out(total_delta),
        sections=sections,
        changed=_is_nonzero(total_delta) or any(s.changed for s in sections),
        computed_at=at,
    )


def _snapshot_section_totals(section: str, stored: Gstr1Sections) -> domain.ReturnTotals:
    """One section's totals, read back off a stored snapshot.

    Summed from the snapshot's own rows rather than from the header, so a diff compares like with
    like: both sides of the subtraction are "add up the rows of this section", and a header that
    had somehow drifted would show as a difference rather than hiding one.
    """
    rows: list[Any] = getattr(stored, section)
    if section == "b2cs":
        return domain.ReturnTotals(
            taxable_minor=sum(r.taxable_minor for r in rows),
            cgst_minor=sum(r.cgst_minor for r in rows),
            sgst_minor=sum(r.sgst_minor for r in rows),
            igst_minor=sum(r.igst_minor for r in rows),
            document_count=sum(r.document_count for r in rows),
            line_count=len(rows),
        )
    if section == "hsn":
        return domain.ReturnTotals(
            taxable_minor=sum(r.taxable_minor for r in rows),
            cgst_minor=sum(r.cgst_minor for r in rows),
            sgst_minor=sum(r.sgst_minor for r in rows),
            igst_minor=sum(r.igst_minor for r in rows),
            document_count=0,
            line_count=len(rows),
        )
    return domain.ReturnTotals(
        taxable_minor=sum(item.taxable_minor for r in rows for item in r.items),
        cgst_minor=sum(item.cgst_minor for r in rows for item in r.items),
        sgst_minor=sum(item.sgst_minor for r in rows for item in r.items),
        igst_minor=sum(item.igst_minor for r in rows for item in r.items),
        document_count=len(rows),
        line_count=sum(len(r.items) for r in rows),
    )


def _is_nonzero(totals: domain.ReturnTotals) -> bool:
    return any(
        (
            totals.taxable_minor,
            totals.cgst_minor,
            totals.sgst_minor,
            totals.igst_minor,
            totals.document_count,
            totals.line_count,
        )
    )


def _append_transition(
    session: AsyncSession,
    ctx: ScopeContext,
    ret: GstReturn,
    *,
    from_status: str | None,
    to_status: str,
    at: datetime,
    arn: str | None = None,
    note: str | None = None,
) -> GstReturnTransition:
    """Append one row of the status trail. The only writer of that table, and it only inserts."""
    transition = GstReturnTransition(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        gst_return_id=ret.id,
        from_status=from_status,
        to_status=to_status,
        arn=arn,
        note=note,
        total_taxable_minor=ret.total_taxable_minor,
        total_tax_minor=(ret.total_cgst_minor + ret.total_sgst_minor + ret.total_igst_minor),
        transitioned_by=str(ctx.principal.sub),
        transitioned_at=at,
    )
    session.add(transition)
    return transition


async def registration_for(
    session: AsyncSession,
    ctx: ScopeContext,
    registration_id: uuid.UUID | None,
    *,
    for_update: bool = False,
) -> GstRegistration:
    """The registration a call names, or the organization's primary — used wherever a return, a
    ledger posting or a balance needs one and none was given explicitly.

    `for_update=True` row-locks it — `generate_return` and `file_return` take this for a `gstr3b`,
    so two calls racing the same registration serialise rather than each acting on the same stale
    read of the ITC ledger's position."""
    if registration_id is not None:
        registrations = await selectors.all_gst_registrations(
            session, ctx.organization_id, for_update=for_update
        )
        found = next((r for r in registrations if r.id == registration_id), None)
        if found is None:
            raise NotFoundProblem("That GST registration is not part of this organization")
        return found
    primary = await selectors.get_gst_registration(
        session, ctx.organization_id, for_update=for_update
    )
    if primary is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so it has nothing to file "
            "a return under"
        )
    return primary


# --------------------------------------------------------------------- e-Way bills
# The third portal, and the same state machine shape as e-invoicing: every path ends in exactly
# one appended row, because an attempt nobody recorded is an attempt nobody can prove was made.
#
# What differs is validity. The portal returns a bill number and the moment it was raised; the
# **deadline is this platform's arithmetic** over the distance it stored and that timestamp. A
# deadline only the portal knows is one a screen cannot explain and a test cannot check, and the
# consequence of a screen and a checkpoint disagreeing falls on a driver.
#
# Both writes are claim / call / record (D3, ADR-0015 addendum, `docs/io-outside-transactions.md`):
# a `submitting` or `cancelling` claim row is committed first, the portal is called with **no
# transaction open**, and the outcome is appended in a second short transaction. Unlike an IRN
# registration the claim *is* a row, because the transport facts are the caller's and on no
# invoice — a resumed attempt can only find them there. A crash between the call and the record
# leaves the claim standing; `app/cron.py`'s reclaim sweep re-drives it, and the portal's "already
# generated" / "already cancelled" answers (`EwayAlreadyGenerated`, `EwayAlreadyCancelled`) are
# recorded as the successes they are.


def build_eway_payload(
    invoice: Invoice,
    registration: GstRegistration,
    payload: GenerateEwayIn,
    *,
    buyer_name: str,
    at: datetime,
    idempotency_key: str = "",
) -> EwayPayload:
    """Assemble the consignment the portal reasons about, from a frozen document plus the
    transport facts, which are the caller's — a distance and a vehicle are not on an invoice."""
    return EwayPayload(
        document_type=domain.IRP_DOCUMENT_TYPES[invoice.document_type],
        document_number=invoice.number,
        document_date=invoice.issued_date,
        seller_gstin=invoice.seller_gstin or registration.gstin,
        seller_state_code=registration.state_code,
        buyer_gstin=invoice.buyer_gstin,
        buyer_name=buyer_name,
        place_of_supply=invoice.place_of_supply or "",
        transport_mode=payload.transport_mode,
        distance_km=payload.distance_km,
        vehicle_no=payload.vehicle_no,
        transporter_id=payload.transporter_id,
        transport_doc_no=payload.transport_doc_no,
        transport_doc_date=payload.transport_doc_date,
        over_dimensional=payload.over_dimensional,
        consignment_value_minor=invoice.total_minor,
        taxable_minor=invoice.subtotal_minor,
        cgst_minor=invoice.cgst_minor,
        sgst_minor=invoice.sgst_minor,
        igst_minor=invoice.igst_minor,
        generated_at=at,
        lines=[
            EwayLine(
                serial_no=line.line_no,
                description=line.description,
                hsn_sac=line.hsn_sac,
                quantity=line.quantity,
                taxable_minor=line.amount_minor,
                rate_pct=line.tax_rate_pct,
                cgst_minor=line.cgst_minor,
                sgst_minor=line.sgst_minor,
                igst_minor=line.igst_minor,
            )
            for line in invoice.lines
        ],
        idempotency_key=idempotency_key,
    )


def _append_eway(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    payload: GenerateEwayIn,
    *,
    status: str,
    at: datetime,
    eway_bill_no: str | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    error_code: str | None = None,
    error: str | None = None,
    attempt: int | None = None,
    retry_at: datetime | None = None,
) -> EwayBill:
    """Append one attempt. The only writer of this table, and it only ever inserts.

    `attempt` (which attempt of a generation chain this row belongs to) and `retry_at` (on a
    `pending` row, when the scheduled retry is due) are the two C4 facts; `error` is stored as
    given, so a caller putting a *portal's* words here masks them first (`domain.mask_portal_text`
    — done once, in `call_eway_generate` and `call_eway_cancel`, where the words arrive)."""
    if status not in domain.EWAY_STATUSES:
        raise BusinessRuleProblem(f"Unknown e-Way status {status!r}")
    bill = EwayBill(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        invoice_id=invoice.id,
        status=status,
        eway_bill_no=eway_bill_no,
        transport_mode=payload.transport_mode,
        distance_km=payload.distance_km,
        vehicle_no=payload.vehicle_no,
        transporter_id=payload.transporter_id,
        transport_doc_no=payload.transport_doc_no,
        transport_doc_date=payload.transport_doc_date,
        over_dimensional=payload.over_dimensional,
        consignment_value_minor=invoice.total_minor,
        valid_from=valid_from,
        valid_until=valid_until,
        error_code=error_code,
        error=error,
        attempted_at=at,
        attempt=attempt,
        retry_at=retry_at,
    )
    session.add(bill)
    return bill


def _transport_of(row: EwayBill) -> GenerateEwayIn:
    """The transport facts a stored attempt carries, as the request that made it would have
    stated them — how a claim row hands its facts to a resumed attempt, and how a withdrawal
    row copies the facts of the bill it withdraws."""
    return GenerateEwayIn(
        organization_id=row.organization_id,
        invoice_id=row.invoice_id,
        distance_km=row.distance_km,
        transport_mode=row.transport_mode,
        vehicle_no=row.vehicle_no,
        transporter_id=row.transporter_id,
        transport_doc_no=row.transport_doc_no,
        transport_doc_date=row.transport_doc_date,
        over_dimensional=row.over_dimensional,
    )


def _eway_key(invoice: Invoice, transport: GenerateEwayIn, bills: list[EwayBill]) -> str:
    return domain.eway_idempotency_key(
        invoice_id=invoice.id,
        generation=sum(1 for bill in bills if bill.status == "generated") + 1,
        transport_mode=transport.transport_mode,
        vehicle_no=transport.vehicle_no,
        transporter_id=transport.transporter_id,
        transport_doc_no=transport.transport_doc_no,
        transport_doc_date=transport.transport_doc_date,
        distance_km=transport.distance_km,
        over_dimensional=transport.over_dimensional,
    )


@dataclass(frozen=True, slots=True)
class EwayGenerateFacts:
    """What `claim_eway_generation` (or the sweep's resume) committed, as plain values: the
    transport facts to record the outcome against and the consignment to send."""

    invoice_id: uuid.UUID
    transport: GenerateEwayIn
    payload: EwayPayload
    #: The clock reading the claim row was written at; every row this attempt appends uses it.
    at: datetime
    #: Which attempt of its chain this call is (1-based) — what `record_eway_generation` decides
    #: "is there a next one" from (`domain.may_retry_eway`).
    attempt: int = 1
    #: The id of the first claim row of this attempt's chain: the identity a scheduled retry is
    #: keyed by (a chain that ended and was restarted by a person must not collide with the
    #: schedule of the one before it) and the one a late-arriving retry is checked against.
    chain_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class _EwayGenerateOutcome:
    kind: str  # "generated" | "rejected" | "unavailable"
    ack: EwayAck | None = None
    error_code: str | None = None
    #: The portal's words, already masked (`domain.mask_portal_text`).
    error: str | None = None
    #: Set on an outage that was the portal saying "slow down" (`EwayRateLimited`).
    rate_limited: bool = False
    retry_after: timedelta | None = None


def call_eway_generate(client: EwayClient, payload: EwayPayload) -> _EwayGenerateOutcome:
    """The second act of a generation, and the only one that touches the portal. Called with
    **no session and no transaction open** (D3).

    `EwayAlreadyGenerated` is folded into `"generated"`: the portal's answer to a repeat of the
    same (document, transporter/vehicle part) key — the resumed half of a call whose first
    attempt crashed before it was recorded — carrying the bill it already issued, which is
    exactly as good as a fresh one. An outage is `"unavailable"`, recorded as `pending` and
    retried (C4): that covers a timeout, a connection failure, a 5xx and — as `EwayRateLimited`,
    checked first because it is the more specific kind — a rate limit, whose `Retry-After` the
    retry honours. A refusal is `"rejected"` and terminal. **Every word the portal sent back is
    masked here**, once, so the ledger, the outbox and the API only ever hold the masked text."""
    try:
        ack = client.generate(payload)
    except EwayAlreadyGenerated as already:
        return _EwayGenerateOutcome(kind="generated", ack=already.ack)
    except EwayRejected as rejected:
        return _EwayGenerateOutcome(
            kind="rejected",
            error_code=rejected.code,
            error=domain.mask_portal_text(rejected.message),
        )
    except EwayRateLimited as limited:
        return _EwayGenerateOutcome(
            kind="unavailable",
            error=domain.mask_portal_text(str(limited)),
            rate_limited=True,
            retry_after=limited.retry_after,
        )
    except EwayUnavailable as outage:
        return _EwayGenerateOutcome(kind="unavailable", error=domain.mask_portal_text(str(outage)))
    return _EwayGenerateOutcome(kind="generated", ack=ack)


async def call_eway_generate_off_loop(
    client: EwayClient, payload: EwayPayload
) -> _EwayGenerateOutcome:
    """`call_eway_generate` on a worker thread, bounded (`off_loop`); a timeout is an outage."""
    return await off_loop(
        call_eway_generate,
        client,
        payload,
        on_timeout=lambda s: _EwayGenerateOutcome(kind="unavailable", error=_timed_out(s)),
    )


async def claim_eway_generation(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: GenerateEwayIn,
    *,
    at: datetime | None = None,
    request: Request | None = None,
) -> EwayGenerateFacts:
    """The first act: refuse everything that does not need the portal, then commit a
    `submitting` claim row carrying the transport facts, audited as `eway.generate_requested`.

    The refusals here are errors, because the caller asked for something impossible and deserves
    to be told: an unknown document, a consignment under the threshold, an impossible distance,
    no registration, a bill already standing, or a generation already in flight (the newest row
    is somebody else's `submitting` claim — the sweep finishes it if its process died). Row-locks
    the invoice first, so two requests racing for the same consignment serialize here.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, payload.invoice_id, for_update=True
    )
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")

    if not domain.requires_eway_bill(
        consignment_value_minor=invoice.total_minor,
        document_type=invoice.document_type,
        supply_type=invoice.supply_type,
        status=invoice.status,
    ):
        raise BusinessRuleProblem(
            f"{invoice.number} is a consignment of {invoice.total_minor} paise, at or below the "
            f"{domain.EWAY_THRESHOLD_MINOR} paise threshold, so it needs no e-Way bill"
        )
    try:
        domain.eway_validity_days(payload.distance_km, over_dimensional=payload.over_dimensional)
    except ValueError as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so it cannot raise a bill"
        )

    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    if bills and bills[0].status in ("submitting", "cancelling"):
        raise ConflictProblem(
            f"An e-Way {'generation' if bills[0].status == 'submitting' else 'withdrawal'} for "
            f"{invoice.number} is already in progress; the reclaim sweep finishes it if the "
            "portal was unreachable"
        )
    live = selectors.live_eway_bill(bills, at=at)
    if live is not None:
        raise ConflictProblem(
            f"{invoice.number} already has e-Way bill {live.eway_bill_no}, valid until "
            f"{live.valid_until.isoformat() if live.valid_until else 'an unrecorded time'}; "
            "cancel it within 24 hours or let it expire before raising another"
        )

    customer = await selectors.get_customer(session, ctx.organization_id, invoice.customer_id)
    eway_payload = build_eway_payload(
        invoice,
        registration,
        payload,
        buyer_name=customer.name if customer else "",
        at=at,
        idempotency_key=_eway_key(invoice, payload, bills),
    )
    # A request landing on an outage the automatic retry still owes continues that chain (the
    # dispatch system re-asking is one more attempt at the same bill, not a fresh budget); one
    # landing on anything else starts a chain of its own.
    attempts = domain.leading_claim_attempts(
        [b.status for b in bills], claim="submitting", chain=_EWAY_GENERATION_CHAIN
    )
    first = domain.chain_start(bills, chain=_EWAY_GENERATION_CHAIN)
    claim = _append_eway(
        session, ctx, invoice, payload, status="submitting", at=at, attempt=attempts + 1
    )
    await session.flush()
    _audit_eway(
        session,
        ctx,
        invoice,
        claim,
        action="eway.generate_requested",
        request=request,
        detail={"idempotency_key": eway_payload.idempotency_key, "attempt": attempts + 1},
    )
    return EwayGenerateFacts(
        invoice_id=invoice.id,
        transport=payload,
        payload=eway_payload,
        at=at,
        attempt=attempts + 1,
        chain_id=first.id if first is not None else claim.id,
    )


async def record_eway_generation(
    session: AsyncSession,
    ctx: ScopeContext,
    facts: EwayGenerateFacts,
    outcome: _EwayGenerateOutcome,
    *,
    portal: str,
    request: Request | None = None,
) -> EwayBill:
    """The third act: append what the portal decided, and return it.

    * **generated** — a `generated` row with the number and the validity this platform computes
      from the stored distance; audit + `EVT_EWAY_GENERATED`. If the same number is already on
      the ledger — a second recorder of the same claim, the portal having answered both with its
      one bill — that row is returned instead: success-by-lookup, never a duplicate.
    * **rejected** — a `failed` row carrying the portal's code; audit + `EVT_EWAY_FAILED`
      (`retryable: false`).
    * **unavailable** — a `pending` row (the goods are loaded; somebody is going to try again,
      and the reclaim sweep is that somebody); audit + `EVT_EWAY_FAILED` (`retryable: true`).

    A refusal or an outage arriving after the claim was already resolved by somebody else (the
    newest row is no longer a `submitting` claim) appends nothing. The invoice is row-locked so
    two recorders serialize and the second sees the first's row.
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, facts.invoice_id, for_update=True
    )
    if invoice is None:  # pragma: no cover - defensive; existed moments ago in the claim
        raise NotFoundProblem("That invoice is not part of this organization")
    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    transport = facts.transport
    at = _after_newest(facts.at, bills)

    if outcome.kind == "generated":
        ack = outcome.ack
        if ack is None:  # pragma: no cover - exhaustiveness
            raise BusinessRuleProblem("a generated outcome carried no acknowledgement")
        recorded = next(
            (b for b in bills if b.status == "generated" and b.eway_bill_no == ack.eway_bill_no),
            None,
        )
        if recorded is not None:
            return recorded
        # Validity is computed here, from the distance this platform is about to store and the
        # moment the portal says the bill began — never taken from the portal's own answer, so
        # the deadline a screen shows and the deadline a checkpoint enforces are the same
        # arithmetic.
        valid_until = domain.eway_valid_until(
            ack.valid_from, transport.distance_km, over_dimensional=transport.over_dimensional
        )
        bill = _append_eway(
            session,
            ctx,
            invoice,
            transport,
            status="generated",
            at=at,
            eway_bill_no=ack.eway_bill_no,
            valid_from=ack.valid_from,
            valid_until=valid_until,
            attempt=facts.attempt,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.generated", request=request)
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_EWAY_GENERATED,
            aggregate_type="invoice",
            aggregate_id=invoice.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "invoice_id": str(invoice.id),
                "eway_bill_id": str(bill.id),
                "number": invoice.number,
                "eway_bill_no": bill.eway_bill_no,
                "transport_mode": bill.transport_mode,
                "distance_km": bill.distance_km,
                "valid_from": ack.valid_from.isoformat(),
                "valid_until": valid_until.isoformat(),
                "portal": portal,
                #: Which attempt of its chain finally raised it — 1 for a bill raised first time,
                #: more when the automatic retry (C4) is what got it through an outage.
                "attempt": facts.attempt,
            },
            actor=_actor(ctx),
        )
        return bill

    newest = bills[0]
    if newest.status != "submitting":
        return newest  # resolved by somebody else since this claim was made

    if outcome.kind == "rejected":
        # Terminal at once, and never retried: the portal read the consignment and refused it, and
        # the same bytes will be refused again. The row carries the portal's code and its masked
        # words; the event carries the remedy (`domain.eway_failure_remedy`).
        bill = _append_eway(
            session,
            ctx,
            invoice,
            transport,
            status="failed",
            at=at,
            error_code=outcome.error_code,
            error=outcome.error,
            attempt=facts.attempt,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.rejected", request=request)
        _publish_eway_failed(
            session, ctx, invoice, bill, retryable=False, portal=portal, attempt=facts.attempt
        )
        return bill

    # The document is fine, the portal is not — and here the goods are already loaded. Three
    # futures, decided here and nowhere else: another attempt is scheduled; the budget is spent
    # (`retry_exhausted`); or the next attempt would land after the generation window shut
    # (`stale`). None of them leaves a `pending` row nobody will ever look at again.
    retry_at = facts.at + domain.eway_retry_delay(facts.attempt, retry_after=outcome.retry_after)
    first = domain.chain_start(bills, chain=_EWAY_GENERATION_CHAIN)
    window_shuts = first.attempted_at + domain.EWAY_REDRIVE_MAX_AGE if first is not None else None
    stop: tuple[str, str] | None = None
    if not domain.may_retry_eway(facts.attempt):
        stop = (
            domain.ERR_RETRY_EXHAUSTED,
            f"gave up raising an e-Way bill for {invoice.number} after {facts.attempt} attempts; "
            f"the portal was last unreachable with: {outcome.error}",
        )
    elif window_shuts is not None and retry_at >= window_shuts:
        stop = (
            domain.ERR_STALE,
            f"the next attempt for {invoice.number} would land after the 24-hour generation "
            f"window shut, so the e-Way bill is not raised automatically; the portal was last "
            f"unreachable with: {outcome.error}",
        )
    if stop is not None:
        failed = _append_eway(
            session,
            ctx,
            invoice,
            transport,
            status="failed",
            at=at,
            error_code=stop[0],
            error=stop[1][: domain.PORTAL_TEXT_LIMIT + 200],
            attempt=facts.attempt,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, failed, action="eway.failed", request=request)
        _publish_eway_failed(
            session, ctx, invoice, failed, retryable=False, portal=portal, attempt=facts.attempt
        )
        return failed

    bill = _append_eway(
        session,
        ctx,
        invoice,
        transport,
        status="pending",
        at=facts.at,
        error_code=domain.ERR_RATE_LIMITED
        if outcome.rate_limited
        else domain.ERR_PORTAL_UNAVAILABLE,
        error=outcome.error,
        attempt=facts.attempt,
        retry_at=retry_at,
    )
    await session.flush()
    _audit_eway(
        session,
        ctx,
        invoice,
        bill,
        action="eway.pending",
        request=request,
        detail={"attempt": facts.attempt, "retry_at": retry_at.isoformat()},
    )
    request_eway_generation(
        session,
        ctx,
        invoice_id=invoice.id,
        number=invoice.number,
        attempt=facts.attempt + 1,
        chain_id=facts.chain_id,
        not_before=retry_at,
        trigger="retry",
        now=facts.at,
    )
    _publish_eway_retry_scheduled(
        session, ctx, invoice, bill, next_attempt=facts.attempt + 1, portal=portal
    )
    return bill


async def generate_eway_bill(
    session: AsyncSession,
    ctx: ScopeContext,
    client: EwayClient,
    payload: GenerateEwayIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EwayBill:
    """All three acts back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that exercise the generation rules against a hand-built session and
    do not care about the transaction boundary. The real caller — `POST /internal/v1/eway/bills`
    — runs `claim_eway_generation`, `call_eway_generate` with **no transaction open**, and
    `record_eway_generation`; see `app/api/internal.py`.
    """
    facts = await claim_eway_generation(session, ctx, payload, at=at, request=request)
    outcome = call_eway_generate(client, facts.payload)
    return await record_eway_generation(
        session, ctx, facts, outcome, portal=client.name, request=request
    )


@dataclass(frozen=True, slots=True)
class EwayCancelFacts:
    """What a withdrawal's second and third acts need, as plain values."""

    invoice_id: uuid.UUID
    eway_bill_no: str
    reason_code: str
    remark: str
    at: datetime


@dataclass(frozen=True, slots=True)
class _EwayCancelOutcome:
    kind: str  # "cancelled" | "rejected" | "unavailable"
    ack: EwayCancelAck | None = None
    error_code: str | None = None
    error: str | None = None


def call_eway_cancel(client: EwayClient, facts: EwayCancelFacts) -> _EwayCancelOutcome:
    """The second act of a withdrawal. **No session and no transaction open** (D3).
    `EwayAlreadyCancelled` is success-by-fetch, folded into `"cancelled"`."""
    try:
        ack = client.cancel(
            eway_bill_no=facts.eway_bill_no,
            reason_code=facts.reason_code,
            remark=facts.remark,
            at=facts.at,
        )
    except EwayAlreadyCancelled as already:
        return _EwayCancelOutcome(kind="cancelled", ack=already.ack)
    except EwayRejected as rejected:
        return _EwayCancelOutcome(
            kind="rejected",
            error_code=rejected.code,
            error=domain.mask_portal_text(rejected.message),
        )
    except EwayUnavailable as outage:
        return _EwayCancelOutcome(kind="unavailable", error=domain.mask_portal_text(str(outage)))
    return _EwayCancelOutcome(kind="cancelled", ack=ack)


async def call_eway_cancel_off_loop(
    client: EwayClient, facts: EwayCancelFacts
) -> _EwayCancelOutcome:
    """`call_eway_cancel` on a worker thread, bounded (`off_loop`); a timeout is an outage."""
    return await off_loop(
        call_eway_cancel,
        client,
        facts,
        on_timeout=lambda s: _EwayCancelOutcome(kind="unavailable", error=_timed_out(s)),
    )


def _eway_cancel_remark(reason_code: str, remark: str) -> str:
    return remark or domain.EWAY_CANCELLATION_REASONS[reason_code]


async def claim_eway_cancellation(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CancelEwayIn,
    *,
    at: datetime | None = None,
    request: Request | None = None,
) -> EwayCancelFacts:
    """The first act of a withdrawal: refuse what the ledger can refuse, then commit a
    `cancelling` claim carrying the reason and the bill's own validity, audited as
    `eway.cancel_requested`.

    The 24-hour window is checked against the **stored** `valid_from` — the moment the platform
    recorded the bill as beginning — rather than left to the portal to refuse. Same reasoning as
    the IRN's window: the answer is knowable locally, and knowing it is what lets a screen offer
    "cancel" only when it will work. A withdrawal already in flight is a conflict.

    The claim row carries **no bill number**: the partial unique index on `(org, eway_bill_no)`
    would refuse a second row naming it. The number is found again, when needed, as the
    `generated` row behind the claim (`selectors.generation_under_claim`).
    """
    at = at or _now()
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, payload.invoice_id, for_update=True
    )
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    if payload.reason_code not in domain.EWAY_CANCELLATION_REASONS:
        reasons = ", ".join(f"{k} {v}" for k, v in domain.EWAY_CANCELLATION_REASONS.items())
        raise BusinessRuleProblem(f"Cancellation reason must be one of: {reasons}")

    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    if bills and bills[0].status == "cancelling":
        raise ConflictProblem(
            f"A withdrawal of {invoice.number}'s e-Way bill is already in progress; the reclaim "
            "sweep finishes it if the portal was unreachable"
        )
    live = selectors.live_eway_bill(bills, at=at)
    if live is None or live.eway_bill_no is None:
        raise BusinessRuleProblem(
            f"{invoice.number} has no live e-Way bill to cancel; a bill that has expired is "
            "spent rather than withdrawable"
        )
    if live.valid_from is not None and not domain.eway_cancellation_window_open(
        live.valid_from, at
    ):
        deadline = domain.eway_cancellation_deadline(live.valid_from)
        raise BusinessRuleProblem(
            f"e-Way bill {live.eway_bill_no} was raised more than 24 hours ago (the window "
            f"closed at {deadline.isoformat()}); the movement has to be reported as it happened"
        )

    claim = _append_eway(
        session,
        ctx,
        invoice,
        _transport_of(live),
        status="cancelling",
        at=at,
        eway_bill_no=None,
        valid_from=live.valid_from,
        valid_until=live.valid_until,
        error_code=payload.reason_code,
        error=_eway_cancel_remark(payload.reason_code, payload.remark),
    )
    await session.flush()
    _audit_eway(
        session,
        ctx,
        invoice,
        claim,
        action="eway.cancel_requested",
        request=request,
        detail={"withdraws": live.eway_bill_no, "reason_code": payload.reason_code},
    )
    return EwayCancelFacts(
        invoice_id=invoice.id,
        eway_bill_no=live.eway_bill_no,
        reason_code=payload.reason_code,
        remark=payload.remark,
        at=at,
    )


async def record_eway_cancellation(
    session: AsyncSession,
    ctx: ScopeContext,
    facts: EwayCancelFacts,
    outcome: _EwayCancelOutcome,
    *,
    request: Request | None = None,
) -> EwayBill:
    """The third act of a withdrawal, the e-Way twin of `record_irn_cancellation`: a
    `cancelled` row (audit + `EVT_EWAY_CANCELLED`), a `failed` row on a refusal, or — on an
    outage — nothing, the `cancelling` claim standing as the record that the withdrawal is owed.
    Idempotent against a second recorder the same way: invoice row-locked, newest row re-read.
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, facts.invoice_id, for_update=True
    )
    if invoice is None:  # pragma: no cover - defensive; existed moments ago in the claim
        raise NotFoundProblem("That invoice is not part of this organization")
    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    newest = bills[0]

    if outcome.kind == "unavailable":
        return newest
    if outcome.kind == "cancelled" and newest.status == "cancelled":
        return newest
    if outcome.kind == "rejected" and newest.status != "cancelling":
        return newest
    generation = selectors.generation_under_claim(bills)
    if generation is None:  # pragma: no cover - a claim is only appended over a generated row
        return newest
    at = _after_newest(facts.at, bills)

    if outcome.kind == "rejected":
        # Carries the bill's own validity: the bill still stands, and this is how the read model
        # knows (`to_eway_state`).
        bill = _append_eway(
            session,
            ctx,
            invoice,
            _transport_of(generation),
            status="failed",
            at=at,
            valid_from=generation.valid_from,
            valid_until=generation.valid_until,
            error_code=outcome.error_code,
            error=outcome.error,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.cancel_failed", request=request)
        return bill

    ack = outcome.ack
    if ack is None:  # pragma: no cover - exhaustiveness
        raise BusinessRuleProblem("a cancelled outcome carried no acknowledgement")
    # The withdrawal is a **new** row and it does not carry the bill number: the partial unique
    # index on (org, eway_bill_no) is what would refuse a second claim on it, and more to the
    # point the generation row is left exactly as it was, so "raised, then withdrawn" reads off
    # the history in order rather than being reconstructed.
    bill = _append_eway(
        session,
        ctx,
        invoice,
        _transport_of(generation),
        status="cancelled",
        at=at,
        eway_bill_no=None,
        valid_from=generation.valid_from,
        valid_until=generation.valid_until,
        error_code=facts.reason_code,
        error=_eway_cancel_remark(facts.reason_code, facts.remark),
    )
    await session.flush()
    _audit_eway(session, ctx, invoice, bill, action="eway.cancelled", request=request)
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EWAY_CANCELLED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "eway_bill_id": str(bill.id),
            "number": invoice.number,
            "eway_bill_no": ack.eway_bill_no,
            "reason_code": facts.reason_code,
            "cancelled_at": ack.cancel_date.isoformat(),
        },
        actor=_actor(ctx),
    )
    return bill


async def cancel_eway_bill(
    session: AsyncSession,
    ctx: ScopeContext,
    client: EwayClient,
    payload: CancelEwayIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EwayBill:
    """All three acts back to back, inside whatever transaction the caller already holds — for
    the unit tests. The real caller — `POST /internal/v1/eway/bills:cancel` — runs
    `claim_eway_cancellation`, `call_eway_cancel` with **no transaction open**, and
    `record_eway_cancellation`; see `app/api/internal.py`."""
    facts = await claim_eway_cancellation(session, ctx, payload, at=at, request=request)
    outcome = call_eway_cancel(client, facts)
    return await record_eway_cancellation(session, ctx, facts, outcome, request=request)


@dataclass(frozen=True, slots=True)
class EwayRedrive:
    """What the sweep's first act decided for one stale e-Way row. `generate` / `cancel` carry
    the facts for the portal call still to come, with no transaction open."""

    outcome: str
    generate: EwayGenerateFacts | None = None
    cancel: EwayCancelFacts | None = None


#: The rows a generation chain is made of, newest first: `submitting` claims, and the `pending`
#: rows their outages left. Only the claims count as drives (`domain.leading_claim_attempts`).
_EWAY_GENERATION_CHAIN: frozenset[str] = frozenset({"submitting", "pending"})


async def _drive_generation(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    bills: list[EwayBill],
    *,
    attempts: int,
    first: EwayBill | None,
    now: datetime,
    portal: str,
    automatic: bool,
    trigger: str,
    transport_from: EwayBill | None = None,
) -> EwayRedrive:
    """The claim half of one more attempt at a generation — shared by the reclaim sweep, the
    scheduled retry and a person's "retry now", because all three are the same act: check that the
    attempt is still allowed, then commit a fresh `submitting` row as the lease.

    `attempts` is how many claims the chain has had (`domain.leading_claim_attempts`); `first` is
    its oldest row (`None` for a chain that starts now, after a terminal `failed` a person is
    retrying); `transport_from` is the row whose transport facts are re-sent (the newest, unless a
    person is retrying a `failed` row, which copies them). Four things end the chain instead of
    driving it, each as a terminal `failed` row a person can read, and no portal call:

    * **no registration** — nothing to raise a bill under (`no_registration`);
    * **stale** — an *automatic* drive of a chain first asked for more than 24 hours ago
      (`stale`): goods that were meant to move a day ago are a person's decision. A person's own
      retry (`automatic=False`) is exactly that decision and is not refused for it;
    * **exhausted** — `domain.EWAY_MAX_ATTEMPTS` claims already made (`retry_exhausted`).

    A generation is re-sent with the idempotency key its first claim computed — a function of the
    stored transport facts and the ledger, not of anything a crash could lose — so a portal that
    already issued the bill answers "already generated" with it, and a retry can never mint a
    second one.
    """
    source = transport_from if transport_from is not None else bills[0]
    transport = _transport_of(source)
    at = max(now, bills[0].attempted_at)
    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    stale = (
        automatic and first is not None and now - first.attempted_at > domain.EWAY_REDRIVE_MAX_AGE
    )
    if stale or attempts >= domain.EWAY_MAX_ATTEMPTS or registration is None:
        if registration is None:
            code = domain.ERR_NO_REGISTRATION
            why = "this organization no longer has a GST registration configured"
        elif stale:
            code = domain.ERR_STALE
            why = (
                f"the e-Way bill for {invoice.number} was first asked for more than "
                f"{int(domain.EWAY_REDRIVE_MAX_AGE.total_seconds() // 3600)} hours ago and "
                "never generated; raise it again if the goods are still to move"
            )
        else:
            code = domain.ERR_RETRY_EXHAUSTED
            why = (
                f"gave up raising an e-Way bill for {invoice.number} after {attempts} "
                "attempts; the portal never answered"
            )
        failed = _append_eway(
            session,
            ctx,
            invoice,
            transport,
            status="failed",
            at=at,
            error_code=code,
            error=why,
            attempt=max(attempts, 1),
        )
        await session.flush()
        _audit_eway(
            session,
            ctx,
            invoice,
            failed,
            action="eway.failed",
            request=None,
            detail={"trigger": trigger},
        )
        _publish_eway_failed(
            session,
            ctx,
            invoice,
            failed,
            retryable=False,
            portal=portal,
            attempt=max(attempts, 1),
        )
        return EwayRedrive("exhausted")

    customer = await selectors.get_customer(session, ctx.organization_id, invoice.customer_id)
    payload = build_eway_payload(
        invoice,
        registration,
        transport,
        buyer_name=customer.name if customer else "",
        at=at,
        idempotency_key=_eway_key(invoice, transport, bills),
    )
    lease = _append_eway(
        session, ctx, invoice, transport, status="submitting", at=at, attempt=attempts + 1
    )
    await session.flush()
    _audit_eway(
        session,
        ctx,
        invoice,
        lease,
        action="eway.generate_resumed",
        request=None,
        detail={
            "idempotency_key": payload.idempotency_key,
            "attempt": attempts + 1,
            "trigger": trigger,
        },
    )
    return EwayRedrive(
        "generate",
        generate=EwayGenerateFacts(
            invoice_id=invoice.id,
            transport=transport,
            payload=payload,
            at=at,
            attempt=attempts + 1,
            chain_id=first.id if first is not None else lease.id,
        ),
    )


def _eway_request_key(invoice_id: uuid.UUID, chain_id: uuid.UUID | None, attempt: int) -> str:
    """The scheduler's idempotency key for one retry: unique per invoice **per chain** per attempt.
    The filing key is `(invoice, attempt)` because a filing chain never restarts; an e-Way chain
    does (a person retries after a terminal failure and attempt numbers begin again), and a key
    that ignored the chain would collide with the earlier chain's schedule and be silently
    dropped by the scheduler's `ON CONFLICT DO NOTHING`."""
    return f"eway-generation:{invoice_id}:{chain_id or 'new'}:{attempt}"


def request_eway_generation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    invoice_id: uuid.UUID,
    number: str,
    attempt: int,
    chain_id: uuid.UUID | None,
    not_before: datetime | None,
    trigger: str,
    now: datetime | None = None,
    idempotency_suffix: str | None = None,
    requested_by: uuid.UUID | None = None,
) -> None:
    """Stage one more attempt at a generation — inside the caller's transaction, never as a call.

    The e-Way twin of `request_filing` (ADR-0015 §4): a `not_before` in the future hands the event
    to the scheduler (`schedule_event`, keyed per chain and attempt so a redelivered schedule
    request cannot produce two deliveries); a null or past one publishes directly — which is what a
    person's "retry now" does, since nobody chose to wait. The consumer that receives it
    (`app/consumers.py:generate_requested_eway`) claims, calls and records; this function only
    says that it should.
    """
    now = now or _now()
    payload = {
        "organization_id": str(ctx.organization_id),
        "invoice_id": str(invoice_id),
        "number": number,
        "attempt": attempt,
        "max_attempts": domain.EWAY_MAX_ATTEMPTS,
        "chain_id": str(chain_id) if chain_id else None,
        "not_before": not_before.isoformat() if not_before else None,
        #: `retry` for the automatic backoff, `manual` for a person's "retry now".
        "trigger": trigger,
        "requested_by": str(requested_by) if requested_by else None,
    }
    if not_before is not None and not_before > now:
        schedule_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            due_at=not_before,
            idempotency_key=(
                _eway_request_key(invoice_id, chain_id, attempt)
                + (f":{idempotency_suffix}" if idempotency_suffix else "")
            ),
            target_event_type=EVT_EWAY_GENERATION_REQUESTED,
            target_aggregate_type="invoice",
            target_aggregate_id=invoice_id,
            target_payload=payload,
            target_actor=_actor(ctx),
        )
        return
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EWAY_GENERATION_REQUESTED,
        aggregate_type="invoice",
        aggregate_id=invoice_id,
        payload=payload,
        actor=_actor(ctx),
    )


def rearm_early_eway_request(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    invoice_id: uuid.UUID,
    event_id: uuid.UUID,
    attempt: int,
    chain_id: uuid.UUID | None,
    not_before: datetime,
    payload: dict[str, Any],
) -> None:
    """ADR-0015 §5's defence in depth for `generation_requested`: a retry arriving meaningfully
    before its own `not_before` (a hand redelivery, a DLQ replay, a scheduler on a wrong clock) is
    re-armed for what remains rather than acted on early or dropped. Keyed off this message's own
    event id for the reason `rearm_early_filing_request` gives: the original key belongs to a job
    that has already fired — this message *is* that firing."""
    request_eway_generation(
        session,
        ctx,
        invoice_id=invoice_id,
        number=str(payload.get("number", "")),
        attempt=attempt,
        chain_id=chain_id,
        not_before=not_before,
        trigger=str(payload.get("trigger", "retry")),
        idempotency_suffix=f"early-arrival:{event_id}",
    )


async def claim_eway_retry(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice_id: uuid.UUID,
    *,
    attempt: int | None,
    chain_id: uuid.UUID | None,
    trigger: str,
    now: datetime | None = None,
    portal: str,
) -> EwayRedrive:
    """The first act of a **scheduled** retry or a person's "retry now" — what the consumer of
    `generation_requested` runs. Same claim as the sweep's (`_drive_generation`), asked a different
    question first: not "is this row old enough" but "is this message still the attempt the ledger
    is waiting for".

    A retry message can be late, duplicated, or overtaken — by the sweep, by the dispatch system
    asking again, by a person — so every one of these is a named outcome that writes nothing:

    * `unknown_invoice` — the message outlived its subject (also what another tenant's id reads as);
    * `not_owed` — the newest row is not an outage awaiting a retry (a bill exists, it was
      cancelled, the portal refused it, a withdrawal is in flight);
    * `in_flight` — somebody's claim is standing; the sweep resumes it if their process died;
    * `superseded` — the chain has moved on since this was scheduled: the message names an
      attempt or a chain the ledger no longer waits for.

    Everything else is `_drive_generation`'s. `trigger` is `retry` (scheduled: the chain and attempt
    are checked, the age cap applies) or `manual` (a person: a `failed` row a person may retry
    starts a new chain, the age cap does not apply). Row-locks the invoice, waiting for it —
    every holder of that lock is a short transaction with no portal call in it (D3).
    """
    now = now or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id, for_update=True)
    if invoice is None:
        return EwayRedrive("unknown_invoice")
    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    if not bills:
        return EwayRedrive("not_owed")
    newest = bills[0]
    manual = trigger == "manual"

    if newest.status == "submitting":
        return EwayRedrive("in_flight")
    if newest.status == "pending":
        attempts = domain.leading_claim_attempts(
            [b.status for b in bills], claim="submitting", chain=_EWAY_GENERATION_CHAIN
        )
        first = domain.chain_start(bills, chain=_EWAY_GENERATION_CHAIN)
        if not manual:
            if attempt is not None and attempt != attempts + 1:
                return EwayRedrive("superseded")
            if chain_id is not None and first is not None and first.id != chain_id:
                return EwayRedrive("superseded")
        return await _drive_generation(
            session,
            ctx,
            invoice,
            bills,
            attempts=attempts,
            first=first,
            now=now,
            portal=portal,
            automatic=not manual,
            trigger=trigger,
        )
    if manual and _is_retryable_generation_failure(newest):
        return await _drive_generation(
            session,
            ctx,
            invoice,
            bills,
            attempts=0,
            first=None,
            now=now,
            portal=portal,
            automatic=False,
            trigger=trigger,
            transport_from=newest,
        )
    return EwayRedrive("not_owed")


def _is_retryable_generation_failure(row: EwayBill) -> bool:
    """A terminal `failed` **generation** row whose cause was never the portal's verdict on the
    consignment (`domain.EWAY_RETRYABLE_FAILURES`). A failed *withdrawal* carries the bill's
    `valid_from` and is not a generation at all."""
    return (
        row.status == "failed"
        and row.valid_from is None
        and row.error_code in domain.EWAY_RETRYABLE_FAILURES
    )


async def request_eway_retry(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice_id: uuid.UUID,
    *,
    request: Request | None = None,
    now: datetime | None = None,
) -> EwayBill:
    """A person's "retry now" — the request half. Refuses everything answerable from the ledger,
    then stages `generation_requested` (trigger `manual`) on the outbox in this same short
    transaction and returns the row the request was made against. **No portal call and no claim
    row here**: the request path never waits on a government (D3), and the consumer's claim
    re-checks everything under the invoice lock, so a double click, or a click racing the
    automatic retry, resolves there rather than here.

    What may be retried, and what may not, is the point:

    * an outage the platform still owes a retry on (`pending`) — sooner than scheduled;
    * a terminal `failed` whose cause was the platform's own (`retry_exhausted`, `stale`,
      `no_registration`) — a new chain, a fresh budget, a person's decision that the goods are
      still to move.

    A portal **refusal** is refused with a 409 that names its code and says what to fix: the
    identical consignment would be refused again. A bill already standing, a withdrawal, and a
    generation in flight are 409s too. An unknown invoice — and another tenant's — is a 404.
    """
    now = now or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id, for_update=True)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    if not bills:
        raise ConflictProblem(
            f"No e-Way bill has been asked for {invoice.number}, so there is nothing to retry; "
            "the dispatch system raises the request with the transport facts"
        )
    newest = bills[0]
    if newest.status == "submitting":
        raise ConflictProblem(
            f"An e-Way generation for {invoice.number} is already in progress; "
            "wait for it to finish"
        )
    if newest.status == "generated" or selectors.live_eway_bill(bills, at=now) is not None:
        raise ConflictProblem(
            f"{invoice.number} already has an e-Way bill; there is nothing to retry"
        )
    retryable = newest.status == "pending" or _is_retryable_generation_failure(newest)
    if not retryable:
        if newest.status == "failed" and newest.valid_from is None:
            _, detail = domain.eway_failure_remedy(newest.error_code)
            raise ConflictProblem(
                f"Retrying will not help — the identical consignment would be refused again "
                f"(code {newest.error_code}). {detail}"
            )
        raise ConflictProblem(
            f"{invoice.number}'s newest e-Way record is {newest.status}, which is not an "
            "outage or a failed generation, so there is nothing to retry"
        )
    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise BusinessRuleProblem(
            "This organization has no GST registration configured, so it cannot raise a bill"
        )
    attempts = (
        domain.leading_claim_attempts(
            [b.status for b in bills], claim="submitting", chain=_EWAY_GENERATION_CHAIN
        )
        if newest.status == "pending"
        else 0
    )
    first = (
        domain.chain_start(bills, chain=_EWAY_GENERATION_CHAIN)
        if newest.status == "pending"
        else None
    )
    request_eway_generation(
        session,
        ctx,
        invoice_id=invoice.id,
        number=invoice.number,
        attempt=attempts + 1,
        chain_id=first.id if first is not None else None,
        not_before=None,
        trigger="manual",
        now=now,
        requested_by=ctx.principal.sub,
    )
    _audit_eway(
        session,
        ctx,
        invoice,
        newest,
        action="eway.retry_requested",
        request=request,
        detail={"attempt": attempts + 1, "from_status": newest.status},
    )
    return newest


async def claim_eway_redrive(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice_id: uuid.UUID,
    *,
    now: datetime,
    portal: str,
) -> EwayRedrive:
    """The sweep's first act for one invoice whose newest e-Way row is a stale `submitting`,
    `pending` or `cancelling`. Same shape as `claim_filing_redrive`: `SELECT ... FOR UPDATE SKIP
    LOCKED` on the invoice, the deadline re-checked under the tenant binding against
    `domain.resume_after(attempts)` (the bounded backoff), the poisoned-step guard at
    `domain.MAX_COMPLIANCE_RESUMES` drives (a terminal `failed` row carrying `retry_exhausted`),
    and otherwise a **fresh claim row** appended as the lease before the facts are handed back.

    A generation is re-sent with the same idempotency key its first claim computed — the key is a
    function of the stored transport facts and the ledger, not of anything a crash could lose —
    so a portal that already issued the bill answers "already generated" with it.
    """
    invoice = await selectors.get_invoice(
        session, ctx.organization_id, invoice_id, for_update=True, skip_locked=True
    )
    if invoice is None:
        return EwayRedrive("not_due")
    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
    if not bills:
        return EwayRedrive("not_due")
    newest = bills[0]
    statuses = [b.status for b in bills]

    if newest.status in _EWAY_GENERATION_CHAIN:
        attempts = domain.leading_claim_attempts(
            statuses, claim="submitting", chain=_EWAY_GENERATION_CHAIN
        )
        if newest.attempted_at > now - domain.resume_after(attempts):
            return EwayRedrive("not_due")
        return await _drive_generation(
            session,
            ctx,
            invoice,
            bills,
            attempts=attempts,
            first=domain.chain_start(bills, chain=_EWAY_GENERATION_CHAIN),
            now=now,
            portal=portal,
            automatic=True,
            trigger="sweep",
        )

    if newest.status != "cancelling":
        return EwayRedrive("not_due")

    attempts = domain.leading_claim_attempts(
        statuses, claim="cancelling", chain=frozenset({"cancelling"})
    )
    if newest.attempted_at > now - domain.resume_after(attempts):
        return EwayRedrive("not_due")
    generation = selectors.generation_under_claim(bills)
    if (
        generation is None or generation.eway_bill_no is None or newest.error_code is None
    ):  # pragma: no cover - a claim is only ever appended over a generated bill
        return EwayRedrive("not_due")

    at = max(now, newest.attempted_at)
    window_closed = generation.valid_from is not None and not (
        domain.eway_cancellation_window_open(generation.valid_from, now)
    )
    if window_closed or attempts >= domain.MAX_COMPLIANCE_RESUMES:
        # Past the portal's 24-hour window a withdrawal can only be refused, so it is not asked
        # for; the row says so and carries the bill's validity, because the bill still stands.
        failed = _append_eway(
            session,
            ctx,
            invoice,
            _transport_of(generation),
            status="failed",
            at=at,
            valid_from=generation.valid_from,
            valid_until=generation.valid_until,
            error_code=domain.ERR_WINDOW_CLOSED if window_closed else domain.ERR_RETRY_EXHAUSTED,
            error=(
                f"the 24-hour window to cancel e-Way bill {generation.eway_bill_no} closed "
                "before the portal confirmed the withdrawal; check the portal"
                if window_closed
                else f"gave up cancelling e-Way bill {generation.eway_bill_no} after {attempts} "
                "attempts; the portal never confirmed the withdrawal"
            ),
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, failed, action="eway.cancel_failed", request=None)
        return EwayRedrive("exhausted")

    lease = _append_eway(
        session,
        ctx,
        invoice,
        _transport_of(generation),
        status="cancelling",
        at=at,
        eway_bill_no=None,
        valid_from=generation.valid_from,
        valid_until=generation.valid_until,
        error_code=newest.error_code,
        error=newest.error,
    )
    await session.flush()
    _audit_eway(
        session,
        ctx,
        invoice,
        lease,
        action="eway.cancel_resumed",
        request=None,
        detail={"withdraws": generation.eway_bill_no, "attempt": attempts + 1},
    )
    return EwayRedrive(
        "cancel",
        cancel=EwayCancelFacts(
            invoice_id=invoice.id,
            eway_bill_no=generation.eway_bill_no,
            reason_code=newest.error_code,
            remark=newest.error or "",
            at=at,
        ),
    )


def _publish_eway_failed(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    bill: EwayBill,
    *,
    retryable: bool,
    portal: str,
    attempt: int | None = None,
) -> None:
    """The **terminal** failure announcement (C4): a refusal, an exhausted budget or a stale chain.
    An outage that is going to be retried is `retry_scheduled` instead — `failed` means somebody has
    to act. Carries the remedy (`domain.eway_failure_remedy`) so a notification can say what to do
    without reading the ledger, and the portal's words already masked (`domain.mask_portal_text`,
    applied where they arrive; this function only forwards what the row holds)."""
    remedy, remedy_detail = domain.eway_failure_remedy(bill.error_code)
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EWAY_FAILED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "eway_bill_id": str(bill.id),
            "number": invoice.number,
            "error_code": bill.error_code,
            "error": bill.error,
            #: A refusal stands until the consignment changes; an outage does not. The same
            #: distinction the filing events carry, and for the same reason. Since C4 every
            #: `failed` event is terminal, so this is false on all of them — kept for the
            #: consumers written against the earlier contract, where an outage said `true`.
            "retryable": retryable,
            "terminal": True,
            "attempt": attempt if attempt is not None else bill.attempt,
            "remedy": remedy,
            "remedy_detail": remedy_detail,
            "portal": portal,
        },
        actor=_actor(ctx),
    )


def _publish_eway_retry_scheduled(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    bill: EwayBill,
    *,
    next_attempt: int,
    portal: str,
) -> None:
    """The portal did not answer and the same consignment will be sent again at `retry_at`. The
    goods are already loaded, which is why this is announced rather than left for a person to
    find on a screen: somebody may want to ring the driver."""
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EWAY_RETRY_SCHEDULED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "invoice_id": str(invoice.id),
            "eway_bill_id": str(bill.id),
            "number": invoice.number,
            "attempt": bill.attempt,
            "next_attempt": next_attempt,
            "max_attempts": domain.EWAY_MAX_ATTEMPTS,
            "retry_at": bill.retry_at.isoformat() if bill.retry_at else None,
            "error_code": bill.error_code,
            "error": bill.error,
            "portal": portal,
        },
        actor=_actor(ctx),
    )


def _audit_eway(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    bill: EwayBill,
    *,
    action: str,
    request: Request | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Every attempt leaves an audit row as well as a ledger row: the ledger records what the
    portal said, the audit records who asked and from where. `detail` adds what a claim row
    cannot say in its own columns — the idempotency key a generation was sent under, the bill
    number a withdrawal is for."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="eway_bill",
        entity_id=bill.id,
        after={
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "status": bill.status,
            "eway_bill_no": bill.eway_bill_no,
            "valid_until": bill.valid_until.isoformat() if bill.valid_until else None,
            "error_code": bill.error_code,
            **(detail or {}),
        },
        request=request,
    )


# --------------------------------------------------------------------- withholding: TDS and TCS
# Assessed when a document is finalized, from three facts about the buyer and the year's running
# total with them. Every figure is a pure function in `app.domain` over integer minor units, and
# the rate is resolved against the **document's** date so a later rate change cannot restate it.


async def assess_withholdings(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    customer: Customer,
    *,
    request: Request | None = None,
) -> list[InvoiceWithholding]:
    """Work out what is withheld on one finalized document, and record it.

    Gated on the customer's flags, and deliberately: whether a body is notified under GST s. 51,
    and whether a buyer is liable to deduct under s. 194Q, are facts about **somebody else's**
    registration and somebody else's turnover. The platform cannot infer them, so it does not
    guess — an organization that has not told it about a counterparty gets no figures invented
    for it, which is the failure direction that does not put a wrong number on an invoice.

    A credit note is skipped. A withholding is a deduction from a payment, and a credit note is
    not a payment; the correction to an over-deduction is made against the original document by
    the deductor, not restated here.
    """
    if invoice.document_type != "invoice" or invoice.status in domain.UNREPORTED_STATUSES:
        return []
    if not (customer.is_tds_deductor or customer.deducts_tds_194q or customer.tcs_applicable):
        return []

    schedule = await selectors.withholding_schedule(session)
    period = f"{invoice.issued_date.year:04d}-{invoice.issued_date.month:02d}"
    recorded: list[InvoiceWithholding] = []

    # --- GST s. 51: a cliff on this document's own taxable value, split across tax heads.
    if customer.is_tds_deductor:
        rate = domain.resolve_withholding_rate(schedule, "gst_tds_51", invoice.issued_date)
        if rate is not None:
            # Read off the frozen document's own split rather than re-deciding it from the state
            # codes — the same rule the return assembly uses, and the same function, so a
            # deduction can never land under a different head from the tax it sits beside.
            intra_state = domain.intra_state_from_split(
                domain.TaxSplit(invoice.cgst_minor, invoice.sgst_minor, invoice.igst_minor),
                supply_type=invoice.supply_type,
                seller_state_code=(invoice.seller_gstin or "")[:2],
                place_of_supply=invoice.place_of_supply,
            )
            computed = domain.compute_gst_tds(
                taxable_minor=invoice.subtotal_minor, rate=rate, intra_state=intra_state
            )
            if computed is not None:
                recorded.append(
                    _append_withholding(session, ctx, invoice, customer, computed, period=period)
                )

    # --- Income tax: 194Q and 206C(1H) are mutually exclusive, and the buyer's liability wins.
    head = domain.income_tax_head(buyer_deducts_194q=customer.deducts_tds_194q)
    wants_income_tax = customer.deducts_tds_194q or customer.tcs_applicable
    if wants_income_tax and head is not None:
        rate = domain.resolve_withholding_rate(schedule, head, invoice.issued_date)
        if rate is not None:
            prior = await selectors.financial_year_turnover(
                session,
                ctx.organization_id,
                customer_id=customer.id,
                on=invoice.issued_date,
                before_invoice_id=invoice.id,
            )
            computed = domain.compute_income_tax_withholding(
                prior_minor=prior, current_minor=invoice.total_minor, rate=rate
            )
            if computed is not None:
                recorded.append(
                    _append_withholding(session, ctx, invoice, customer, computed, period=period)
                )

    if not recorded:
        return []

    await session.flush()
    for row in recorded:
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="withholding.assessed",
            entity="invoice_withholding",
            entity_id=row.id,
            after={
                "invoice_id": str(invoice.id),
                "number": invoice.number,
                "code": row.code,
                "direction": row.direction,
                "basis_minor": row.basis_minor,
                "amount_minor": row.amount_minor,
                "rate_pct": str(row.rate_pct),
            },
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_WITHHOLDING_RECORDED,
            aggregate_type="invoice",
            aggregate_id=invoice.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "invoice_id": str(invoice.id),
                "withholding_id": str(row.id),
                "number": invoice.number,
                "code": row.code,
                "direction": row.direction,
                "basis_minor": row.basis_minor,
                "amount_minor": row.amount_minor,
                "period": row.period,
                #: The version the figure came from, so a consumer can explain it without
                #: re-resolving a schedule that may since have gained a later row.
                "withholding_rate_id": str(row.withholding_rate_id),
            },
            actor=_actor(ctx),
        )
    return recorded


def _append_withholding(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    customer: Customer,
    computed: domain.Withholding,
    *,
    period: str,
) -> InvoiceWithholding:
    """Append one register row. The only writer of this table, and it only ever inserts."""
    row = InvoiceWithholding(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        invoice_id=invoice.id,
        code=computed.code,
        direction=computed.direction,
        withholding_rate_id=computed.rate_id,
        rate_pct=computed.rate_pct,
        threshold_minor=computed.threshold_minor,
        basis=computed.basis,
        basis_minor=computed.basis_minor,
        amount_minor=computed.amount_minor,
        cgst_minor=computed.cgst_minor,
        sgst_minor=computed.sgst_minor,
        igst_minor=computed.igst_minor,
        counterparty_gstin=invoice.buyer_gstin or customer.gstin,
        counterparty_name=customer.name,
        resolved_on=invoice.issued_date,
        period=period,
    )
    session.add(row)
    return row
