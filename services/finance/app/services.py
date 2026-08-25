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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.ims.base import ImsClient, ImsSupplierDocument, ImsUnavailable
from app.irp.base import (
    IrpAck,
    IrpClient,
    IrpInvoicePayload,
    IrpLine,
    IrpRejected,
    IrpUnavailable,
)
from app.models import (
    AuditLog,
    Customer,
    EInvoiceFiling,
    GstRegistration,
    ImsDecision,
    ImsDocument,
    ImsReconciliation,
    Invoice,
    InvoiceLine,
    OutboxEvent,
    Payment,
    PaymentAllocation,
    PurchaseDocument,
)
from app.schemas import (
    CancelFilingIn,
    CustomerCreate,
    FileInvoiceIn,
    GstRegistrationUpsert,
    ImsDecisionCreate,
    InvoiceCreate,
    PaymentCreate,
    PurchaseDocumentCreate,
)
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
)
from platform_core.events import publish_event

# Event names. `finance.*` because these are the product's own, not the platform's; the second
# segment is the module, matching the permission grammar (ADR-0000 §5).
EVT_CUSTOMER_CREATED = "finance.billing.customer.created"
EVT_INVOICE_ISSUED = "finance.billing.invoice.issued"
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


# --------------------------------------------------------------------- invoices


async def create_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: InvoiceCreate,
    *,
    request: Request | None = None,
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
    """
    if payload.document_type not in domain.DOCUMENT_TYPES:
        raise BusinessRuleProblem(f"Unknown document type {payload.document_type!r}")
    if payload.status is not None and payload.status not in domain.INVOICE_STATUSES:
        raise BusinessRuleProblem(f"Unknown invoice status {payload.status!r}")
    if payload.supply_type is not None and payload.supply_type not in domain.SUPPLY_TYPES:
        raise BusinessRuleProblem(f"Unknown supply type {payload.supply_type!r}")
    if await selectors.number_exists(session, ctx.organization_id, payload.number):
        raise ConflictProblem(f"An invoice numbered {payload.number!r} already exists")
    customer = await selectors.get_customer(session, ctx.organization_id, payload.customer_id)
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")
    if payload.corrects_invoice_id is not None and not await selectors.invoice_exists(
        session, ctx.organization_id, payload.corrects_invoice_id
    ):
        raise NotFoundProblem("The invoice this credit note corrects was not found")

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

    status = payload.status or domain.derive_invoice_status(
        document_type=payload.document_type,
        status_override=None,
        due_date=payload.due_date,
        total_minor=total,
        paid_minor=0,
        today=domain.REFERENCE_DATE,
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
    return invoice


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
) -> Payment:
    """Record a payment received, split across one or more invoices — an append-only fact."""
    if payload.method not in domain.PAYMENT_METHODS:
        raise BusinessRuleProblem(f"Unknown payment method {payload.method!r}")
    if await selectors.reference_exists(session, ctx.organization_id, payload.reference):
        raise ConflictProblem(f"A payment referenced {payload.reference!r} already exists")
    if await selectors.get_customer(session, ctx.organization_id, payload.customer_id) is None:
        raise NotFoundProblem("That customer is not part of this organization")

    allocations: list[PaymentAllocation] = []
    amount = 0
    for alloc in payload.allocations:
        if not await selectors.invoice_exists(session, ctx.organization_id, alloc.invoice_id):
            raise NotFoundProblem("An allocated invoice is not part of this organization")
        amount += alloc.amount_minor
        allocations.append(
            PaymentAllocation(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                invoice_id=alloc.invoice_id,
                amount_minor=alloc.amount_minor,
            )
        )

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


def request_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    *,
    attempt: int = 1,
    not_before: datetime | None = None,
    trigger: str,
) -> None:
    """Stage a filing attempt on the outbox — inside the caller's transaction, never as a call.

    Used twice: once when a document is finalized, and again by the consumer to schedule a bounded
    retry after a portal outage. `attempt` is 1-based and rides on the payload rather than being
    counted from the filing ledger, because the ledger records what happened to the *document* and
    the budget belongs to this *chain of attempts* — a manual re-file should not consume the
    automation's remaining tries, and reading a count back would make it do exactly that.

    `not_before` is advisory: it says when the retry becomes due. Honouring it is the scheduler's
    job, which is the piece of the autopilot still to come; carrying it now means the retry
    already knows its own backoff and the scheduler is a delivery change rather than a redesign.
    """
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVOICE_FILING_REQUESTED,
        aggregate_type="invoice",
        aggregate_id=invoice.id,
        payload={
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
        },
        actor=_actor(ctx),
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


async def _attempt_registration(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    invoice: Invoice,
    registration: GstRegistration,
    *,
    at: datetime,
    request: Request | None,
    retryable_outage: bool = True,
) -> _Attempt:
    """Send one document to the portal and append exactly one row describing what happened.

    There is no path through here that calls the portal and records nothing — that is the failure
    mode the ledger exists to prevent, because an attempt nobody recorded is an attempt nobody can
    prove was made.

    `retryable_outage` is what the caller knows and this function does not: whether another try is
    coming. When it is, an outage is a `pending` attempt awaiting that retry; when the budget is
    spent, the same outage is a terminal `failed` row carrying `retry_exhausted`, because "we
    stopped trying" is a different fact from "we are still trying" and an operator has to be able
    to tell them apart at a glance.
    """
    customer = await selectors.get_customer(session, ctx.organization_id, invoice.customer_id)
    irp_payload = build_irp_payload(
        invoice,
        registration,
        buyer_legal_name=customer.name if customer else "",
        at=at,
    )

    try:
        ack = client.register(irp_payload)
    except IrpRejected as rejected:
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="failed",
            at=at,
            error_code=rejected.code,
            error=rejected.message,
        )
        await session.flush()
        _audit_filing(
            session, ctx, invoice, filing, action="einvoice.filing_failed", request=request
        )
        return _Attempt(
            filing=filing,
            outcome="rejected",
            error_code=rejected.code,
            error=rejected.message,
        )
    except IrpUnavailable as outage:
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
                f"unreachable with: {outage}"
                if exhausted
                else str(outage)
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


async def file_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    payload: FileInvoiceIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EInvoiceFiling:
    """Register one document with the portal and append what came back — the *asked-for* filing.

    The manual path: an operator or an operator's tool naming one document. Its refusals are
    errors, because a caller who asked for something impossible deserves to be told rather than
    handed a row: no registration configured, a document that is not reportable, one already
    registered. Everything from the call onward becomes a row, success or failure alike.

    The automatic path (`autofile_invoice`) answers the same situations with outcomes instead of
    exceptions, because nobody is waiting on the other end of a queue — but both go through
    `live_registration` and `_attempt_registration`, so what "already filed" means and what gets
    written can never diverge between them.
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

    attempt = await _attempt_registration(
        session, ctx, client, invoice, registration, at=at, request=request
    )

    if attempt.outcome == "filed" and attempt.ack is not None:
        _publish_filed(session, ctx, invoice, attempt.filing, attempt.ack, portal=client.name)
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
    """File one document because the queue said so, and decide what happens next.

    The order of the guards is the design. **`live_registration` is consulted before the client
    is touched**, so a redelivered request, a manual filing that beat the consumer to it, and a
    second finalize event for the same document all cost nothing and change nothing — the portal
    is never asked to register a document it has already registered, because the portal's answer
    to that is an error and an error is not how a well-behaved integration discovers a fact it
    already had.

    After the call there are exactly three futures. A registration is done. A rejection is
    **terminal**: the portal read the document and refused it, and the same bytes will be refused
    again, so the row is `failed`, the event says `retryable: false`, and a person has to change
    the document. An outage is retried, bounded by `domain.MAX_FILING_ATTEMPTS`; when that budget
    is spent the outage becomes terminal too, and it says so in its own error code rather than
    disappearing into a queue that never drains.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        # A request for a document that is no longer there is not an error worth dead-lettering:
        # the outbox row outlived its subject, and the only correct action is to stop.
        return FilingOutcome("unknown_invoice")

    # Asked again here, not only at finalize: the request may have been queued minutes ago, and a
    # document that is no longer reportable — or never was — must not be filed because a stale
    # message said so.
    if not domain.should_auto_file(
        supply_type=invoice.supply_type,
        document_type=invoice.document_type,
        status=invoice.status,
    ):
        return FilingOutcome("not_required")

    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is not None:
        return FilingOutcome("already_filed", filing=live)

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
        return FilingOutcome("no_registration", attempt=attempt)

    retryable = domain.may_retry_filing(attempt)
    result = await _attempt_registration(
        session,
        ctx,
        client,
        invoice,
        registration,
        at=at,
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
            portal=client.name,
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


# --------------------------------------------------------------------- withdrawing a filing
# The one compliance action that stays deliberately manual. Cancelling an IRN is a statement that
# a registered document should never have been registered, and the portal gives 24 hours to make
# it; past that the remedy is a credit note. Nothing automatic should be making that call.


async def cancel_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    payload: CancelFilingIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EInvoiceFiling:
    """Withdraw a registered IRN, and append the withdrawal as its own row.

    The 24-hour window is checked here, against the `filed` row's acknowledgement date, rather
    than left to the portal to refuse — the answer is knowable locally, and knowing it is what
    lets a screen offer "cancel" or "raise a credit note" correctly instead of offering both.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, payload.invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    if payload.reason_code not in domain.IRN_CANCELLATION_REASONS:
        reasons = ", ".join(f"{k} {v}" for k, v in domain.IRN_CANCELLATION_REASONS.items())
        raise BusinessRuleProblem(f"Cancellation reason must be one of: {reasons}")

    filings = await selectors.filings_for_invoice(session, ctx.organization_id, invoice.id)
    live = live_registration(filings)
    if live is None or live.irn is None:
        raise BusinessRuleProblem(f"{invoice.number} carries no registered IRN to cancel")
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

    try:
        ack = client.cancel(
            irn=live.irn, reason_code=payload.reason_code, remark=payload.remark, at=at
        )
    except IrpRejected as rejected:
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="failed",
            at=at,
            error_code=rejected.code,
            error=rejected.message,
        )
        await session.flush()
        _audit_filing(
            session, ctx, invoice, filing, action="einvoice.cancel_failed", request=request
        )
        return filing

    # The cancellation is a *new* row carrying the same IRN — the registration row is left exactly
    # as it was, so "this document was registered, then withdrawn" reads off the history in order.
    # The partial unique index on (org, irn) is why the withdrawn row cannot claim the IRN twice.
    filing = _append_filing(
        session,
        ctx,
        invoice,
        status="cancelled",
        at=at,
        irn=None,
        ack_no=live.ack_no,
        ack_date=live.ack_date,
        error_code=payload.reason_code,
        error=payload.remark or domain.IRN_CANCELLATION_REASONS[payload.reason_code],
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
            "irn": ack.irn,
            "reason_code": payload.reason_code,
            "cancelled_at": ack.cancel_date.isoformat(),
        },
        actor=_actor(ctx),
    )
    return filing


def _audit_filing(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    filing: EInvoiceFiling,
    *,
    action: str,
    request: Request | None,
) -> None:
    """Every filing attempt leaves an audit row as well as a ledger row.

    Not redundant: `einvoice_filings` records what the portal said, `audit_logs` records who asked
    and from where. A compliance question needs both — "was it filed" and "who filed it".
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


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one pull from the portal produced. `fetched` and `created` differ on a re-sync, which
    is how the ingest's idempotency shows up as a number rather than as a claim."""

    fetched: int
    created: int
    documents: list[ImsDocument]


async def ingest_ims_documents(
    session: AsyncSession,
    ctx: ScopeContext,
    client: ImsClient,
    *,
    period: str,
    at: datetime | None = None,
    request: Request | None = None,
) -> IngestResult:
    """Pull one return period's supplier documents and store the ones not already held.

    Idempotent on `source_ref`, the portal's own reference for its row — *not* on the supplier's
    document number. The distinction is the whole reconciliation: re-syncing August must not
    duplicate anything, and a supplier who filed the same invoice twice must still show up as two
    rows, because that duplicate is precisely what an operator needs to see and reject.
    """
    at = at or _now()
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

    try:
        fetched = client.fetch(gstin=registration.gstin, period=period)
    except ImsUnavailable as outage:
        # Unlike a filing, a failed fetch changes nothing and leaves nothing behind: there is no
        # attempt to record, because nothing was asserted to anybody. Answered as an upstream
        # failure so the caller retries rather than concluding the period is empty.
        raise UpstreamUnavailableProblem(
            f"The IMS portal could not be reached: {outage}"
        ) from outage

    existing = await selectors.ims_documents_by_source_ref(
        session, ctx.organization_id, [doc.source_ref for doc in fetched]
    )

    created: list[ImsDocument] = []
    for doc in fetched:
        if doc.source_ref in existing:
            continue
        created.append(_ingest_one(session, ctx, doc, period=period, at=at))
    await session.flush()

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
            "portal": client.name,
            "fetched": len(fetched),
            "created": len(created),
        },
        request=request,
    )
    return IngestResult(fetched=len(fetched), created=len(created), documents=created)


def _ingest_one(
    session: AsyncSession,
    ctx: ScopeContext,
    doc: ImsSupplierDocument,
    *,
    period: str,
    at: datetime,
) -> ImsDocument:
    """Validate one inbound document and stage it.

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

    row = ImsDocument(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        source_ref=doc.source_ref,
        source="ims",
        supplier_gstin=supplier.value,
        supplier_name=doc.supplier_name,
        document_number=doc.document_number,
        document_type=doc.document_type,
        document_date=doc.document_date,
        period=doc.period or period,
        taxable_minor=doc.taxable_minor,
        cgst_minor=doc.cgst_minor,
        sgst_minor=doc.sgst_minor,
        igst_minor=doc.igst_minor,
        total_minor=doc.total_minor,
        irn=doc.irn,
        # Every document starts undecided. Nothing this service does moves it — only a person.
        status="pending",
        fetched_at=at,
    )
    session.add(row)
    return row


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


async def record_ims_decision(
    session: AsyncSession,
    ctx: ScopeContext,
    ims_document_id: uuid.UUID,
    payload: ImsDecisionCreate,
    *,
    request: Request | None = None,
    at: datetime | None = None,
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
    """
    at = at or _now()
    if payload.action not in domain.IMS_ACTIONS:
        actions = ", ".join(sorted(domain.IMS_ACTIONS))
        raise BusinessRuleProblem(f"An IMS decision must be one of: {actions}")

    document = await selectors.get_ims_document(session, ctx.organization_id, ims_document_id)
    if document is None:
        raise NotFoundProblem("That supplier document is not part of this organization")

    suggestions = await selectors.latest_reconciliations(
        session, ctx.organization_id, [document.id]
    )
    suggestion = suggestions.get(document.id)

    decision = ImsDecision(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ims_document_id=document.id,
        action=payload.action,
        note=payload.note,
        suggested_action=suggestion.suggested_action if suggestion else None,
        suggestion_reason=suggestion.reason if suggestion else None,
        reconciliation_id=suggestion.id if suggestion else None,
        decided_by=str(ctx.principal.sub),
        decided_at=at,
    )
    session.add(decision)

    before_status = document.status
    document.status = domain.IMS_ACTION_STATUS[payload.action]
    document.updated_by = str(ctx.principal.sub)
    await session.flush()

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
            "decision_id": str(decision.id),
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
        },
        actor=_actor(ctx),
    )
    return decision
