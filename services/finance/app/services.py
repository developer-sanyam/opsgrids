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
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.eway.base import EwayClient, EwayLine, EwayPayload, EwayRejected, EwayUnavailable
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
    EwayBill,
    GstRegistration,
    GstReturn,
    GstReturnTransition,
    ImsDecision,
    ImsDocument,
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
    ImsDecisionCreate,
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
#: The return events. `generated` announces a snapshot of a period; `filed` announces that the
#: snapshot went to the portal. Two events rather than one because they are two claims — one
#: about this platform's book, one to a government — and a consumer may reasonably care about
#: only the second.
EVT_RETURN_GENERATED = "finance.compliance.gst_return.generated"
EVT_RETURN_FILED = "finance.compliance.gst_return.filed"
#: The e-Way events. `failed` carries `retryable` for the same reason the filing one does: an
#: outage is worth another attempt with the identical consignment and a refusal is not.
EVT_EWAY_GENERATED = "finance.compliance.eway_bill.generated"
EVT_EWAY_FAILED = "finance.compliance.eway_bill.failed"
EVT_EWAY_CANCELLED = "finance.compliance.eway_bill.cancelled"
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

    **The ITC ledger moves in the same transaction, whenever the document's net standing should
    change.** That covers three cases with one formula: a plain accept (nothing outstanding yet,
    something now should be), a plain un-accept (something outstanding, nothing now should be),
    and a later decision that re-classifies an *already*-accepted document under a different
    `expense_category` (something outstanding, a different something now should be — see
    `domain.split_positive_negative`). The document is locked `FOR UPDATE` above before any of
    this runs, so two decisions racing the same document serialise on the row rather than each
    reading the same `before_status` and each posting a transition of their own.
    """
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
        # Locked before `claim_period_for` reads which periods are filed: a filing racing this
        # decision holds this same lock while it moves a return to `filed`, so this either waits
        # for that commit and then correctly sees the period as filed (landing one period later),
        # or the filing is still in flight and blocks behind *this* transaction instead — never
        # both reading "not filed yet" and both proceeding as if the other had not happened.
        registration = await registration_for(session, ctx, None, for_update=True)
        outstanding = (
            await selectors.itc_document_net_accrued(session, ctx.organization_id, document.id)
            if was_accepted
            else domain.ZERO_ITC
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
            claim_period = await selectors.claim_period_for(
                session, ctx.organization_id, registration.id, document.period
            )
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


def build_eway_payload(
    invoice: Invoice,
    registration: GstRegistration,
    payload: GenerateEwayIn,
    *,
    buyer_name: str,
    at: datetime,
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
) -> EwayBill:
    """Append one attempt. The only writer of this table, and it only ever inserts."""
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
    )
    session.add(bill)
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
    """Raise an e-Way bill for one document's goods movement, and append what came back.

    The refusals that happen *before* the portal is called are errors, because the caller asked
    for something impossible and deserves to be told: an unknown document, a consignment under
    the threshold, no registration, a bill already standing. Everything from the call onward
    becomes a row — a rejection is a `failed` attempt, an outage a `pending` one — because the
    attempt happened and the ledger is what proves it.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, payload.invoice_id)
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
    live = selectors.live_eway_bill(bills, at=at)
    if live is not None:
        raise ConflictProblem(
            f"{invoice.number} already has e-Way bill {live.eway_bill_no}, valid until "
            f"{live.valid_until.isoformat() if live.valid_until else 'an unrecorded time'}; "
            "cancel it within 24 hours or let it expire before raising another"
        )

    customer = await selectors.get_customer(session, ctx.organization_id, invoice.customer_id)
    eway_payload = build_eway_payload(
        invoice, registration, payload, buyer_name=customer.name if customer else "", at=at
    )

    try:
        ack = client.generate(eway_payload)
    except EwayRejected as rejected:
        bill = _append_eway(
            session,
            ctx,
            invoice,
            payload,
            status="failed",
            at=at,
            error_code=rejected.code,
            error=rejected.message,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.rejected", request=request)
        _publish_eway_failed(session, ctx, invoice, bill, retryable=False, portal=client.name)
        return bill
    except EwayUnavailable as outage:
        # The document is fine, the portal is not — and here the goods are already loaded, which
        # is why the row is `pending` rather than `failed`: somebody is going to try again.
        bill = _append_eway(
            session,
            ctx,
            invoice,
            payload,
            status="pending",
            at=at,
            error_code=domain.ERR_PORTAL_UNAVAILABLE,
            error=str(outage),
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.pending", request=request)
        _publish_eway_failed(session, ctx, invoice, bill, retryable=True, portal=client.name)
        return bill

    # Validity is computed here, from the distance this platform is about to store and the
    # moment the portal says the bill began — never taken from the portal's own answer, so the
    # deadline a screen shows and the deadline a checkpoint enforces are the same arithmetic.
    valid_until = domain.eway_valid_until(
        ack.valid_from, payload.distance_km, over_dimensional=payload.over_dimensional
    )
    bill = _append_eway(
        session,
        ctx,
        invoice,
        payload,
        status="generated",
        at=at,
        eway_bill_no=ack.eway_bill_no,
        valid_from=ack.valid_from,
        valid_until=valid_until,
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
            "portal": client.name,
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
    """Withdraw a raised bill, and append the withdrawal as its own row.

    The 24-hour window is checked against the **stored** `valid_from` — the moment the platform
    recorded the bill as beginning — rather than left to the portal to refuse. Same reasoning as
    the IRN's window: the answer is knowable locally, and knowing it is what lets a screen offer
    "cancel" only when it will work.
    """
    at = at or _now()
    invoice = await selectors.get_invoice(session, ctx.organization_id, payload.invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    if payload.reason_code not in domain.EWAY_CANCELLATION_REASONS:
        reasons = ", ".join(f"{k} {v}" for k, v in domain.EWAY_CANCELLATION_REASONS.items())
        raise BusinessRuleProblem(f"Cancellation reason must be one of: {reasons}")

    bills = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice.id)
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

    attempt_payload = GenerateEwayIn(
        organization_id=ctx.organization_id,
        invoice_id=invoice.id,
        distance_km=live.distance_km,
        transport_mode=live.transport_mode,
        vehicle_no=live.vehicle_no,
        transporter_id=live.transporter_id,
        transport_doc_no=live.transport_doc_no,
        transport_doc_date=live.transport_doc_date,
        over_dimensional=live.over_dimensional,
    )

    try:
        ack = client.cancel(
            eway_bill_no=live.eway_bill_no,
            reason_code=payload.reason_code,
            remark=payload.remark,
            at=at,
        )
    except EwayRejected as rejected:
        bill = _append_eway(
            session,
            ctx,
            invoice,
            attempt_payload,
            status="failed",
            at=at,
            error_code=rejected.code,
            error=rejected.message,
        )
        await session.flush()
        _audit_eway(session, ctx, invoice, bill, action="eway.cancel_failed", request=request)
        return bill

    # The withdrawal is a **new** row and it does not carry the bill number: the partial unique
    # index on (org, eway_bill_no) is what would refuse a second claim on it, and more to the
    # point the generation row is left exactly as it was, so "raised, then withdrawn" reads off
    # the history in order rather than being reconstructed.
    bill = _append_eway(
        session,
        ctx,
        invoice,
        attempt_payload,
        status="cancelled",
        at=at,
        eway_bill_no=None,
        valid_from=live.valid_from,
        valid_until=live.valid_until,
        error_code=payload.reason_code,
        error=payload.remark or domain.EWAY_CANCELLATION_REASONS[payload.reason_code],
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
            "reason_code": payload.reason_code,
            "cancelled_at": ack.cancel_date.isoformat(),
        },
        actor=_actor(ctx),
    )
    return bill


def _publish_eway_failed(
    session: AsyncSession,
    ctx: ScopeContext,
    invoice: Invoice,
    bill: EwayBill,
    *,
    retryable: bool,
    portal: str,
) -> None:
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
            #: distinction the filing events carry, and for the same reason.
            "retryable": retryable,
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
) -> None:
    """Every attempt leaves an audit row as well as a ledger row: the ledger records what the
    portal said, the audit records who asked and from where."""
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
