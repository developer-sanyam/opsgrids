"""Writes for finance.

Business rules, audit rows and outbox rows — all inside the caller's one transaction. Nothing
here opens or commits: a route with a principal takes its session from `get_tenant_session`,
which opens the request's transaction and binds it to the caller's organization first
(ADR-0002 §1), so a state change, its audit entry and any event it emits all commit or all roll
back together. The demo seed drives these same functions inside its own `session.begin()`.

There is no *public* write surface in this reads-first slice — the finance SPA raises nothing —
so these functions exist to (a) finalize the append-only documents the reads serve and (b) let
the demo seed build the org's book through the real path rather than raw inserts. An invoice is
frozen at finalize: it foots exactly here, and a correction is a new credit-note document, never
an edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.irp.base import (
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
    Invoice,
    InvoiceLine,
    OutboxEvent,
    Payment,
    PaymentAllocation,
)
from app.schemas import (
    CancelFilingIn,
    CustomerCreate,
    FileInvoiceIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    PaymentCreate,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `finance.*` because these are the product's own, not the platform's; the second
# segment is the module, matching the permission grammar (ADR-0000 §5).
EVT_CUSTOMER_CREATED = "finance.billing.customer.created"
EVT_INVOICE_ISSUED = "finance.billing.invoice.issued"
EVT_PAYMENT_RECEIVED = "finance.billing.payment.received"
#: The compliance events. `filed` is the one a later auto-filing agent both produces the trigger
#: for and confirms against — it carries the IRN, so a consumer never has to read back to learn
#: whether the attempt succeeded.
EVT_INVOICE_FILED = "finance.compliance.invoice.filed"
EVT_INVOICE_FILING_FAILED = "finance.compliance.invoice.filing_failed"
EVT_INVOICE_FILING_CANCELLED = "finance.compliance.invoice.filing_cancelled"
EVT_GST_REGISTRATION_SET = "finance.compliance.gst_registration.set"


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


async def file_invoice(
    session: AsyncSession,
    ctx: ScopeContext,
    client: IrpClient,
    payload: FileInvoiceIn,
    *,
    request: Request | None = None,
    at: datetime | None = None,
) -> EInvoiceFiling:
    """Register one document with the portal and append what came back.

    The refusals that happen *before* the portal — no registration configured, a document that is
    not reportable, one already registered — are answered as errors rather than recorded as
    attempts: nothing was attempted, and a ledger of attempts that never happened is worse than
    no ledger. Everything from the call onward becomes a row, success or failure alike.
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
    live = next((f for f in filings if f.status == "filed"), None)
    if live is not None:
        raise ConflictProblem(
            f"{invoice.number} is already registered under IRN {live.irn}; a correction is a "
            "credit note, or cancel the IRN within 24 hours"
        )

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
                "filing_id": str(filing.id),
                "number": invoice.number,
                "error_code": rejected.code,
                "error": rejected.message,
                # A rejection is final until the document changes: retrying the identical bytes
                # is how an integration goes quiet and a deadline passes.
                "retryable": False,
            },
            actor=_actor(ctx),
        )
        return filing
    except IrpUnavailable as outage:
        # The opposite of a rejection: the document is fine, the portal is not. Recorded as a
        # pending attempt so the retry is visibly a retry rather than a first try.
        filing = _append_filing(
            session,
            ctx,
            invoice,
            status="pending",
            at=at,
            error_code="unavailable",
            error=str(outage),
        )
        await session.flush()
        _audit_filing(
            session, ctx, invoice, filing, action="einvoice.filing_pending", request=request
        )
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
                "filing_id": str(filing.id),
                "number": invoice.number,
                "error_code": "unavailable",
                "error": str(outage),
                "retryable": True,
            },
            actor=_actor(ctx),
        )
        return filing

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
            "portal": client.name,
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
    live = next((f for f in filings if f.status == "filed"), None)
    if live is None or live.irn is None:
        raise BusinessRuleProblem(f"{invoice.number} carries no registered IRN to cancel")
    if live.ack_date is not None and not domain.cancellation_window_open(live.ack_date, at):
        raise BusinessRuleProblem(
            f"IRN {live.irn} was registered more than 24 hours ago; the correction is a "
            "credit note, not a cancellation"
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
