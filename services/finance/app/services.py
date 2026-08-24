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

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    AuditLog,
    Customer,
    Invoice,
    InvoiceLine,
    OutboxEvent,
    Payment,
    PaymentAllocation,
)
from app.schemas import CustomerCreate, InvoiceCreate, PaymentCreate
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `finance.*` because these are the product's own, not the platform's.
EVT_CUSTOMER_CREATED = "finance.billing.customer.created"
EVT_INVOICE_ISSUED = "finance.billing.invoice.issued"
EVT_PAYMENT_RECEIVED = "finance.billing.payment.received"


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


# --------------------------------------------------------------------- customers


async def create_customer(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CustomerCreate,
    *,
    request: Request | None = None,
) -> Customer:
    """Add a billed party."""
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
    """Finalize an invoice or credit note. Totals foot exactly; the document is frozen after."""
    if payload.document_type not in domain.DOCUMENT_TYPES:
        raise BusinessRuleProblem(f"Unknown document type {payload.document_type!r}")
    if payload.status is not None and payload.status not in domain.INVOICE_STATUSES:
        raise BusinessRuleProblem(f"Unknown invoice status {payload.status!r}")
    if await selectors.number_exists(session, ctx.organization_id, payload.number):
        raise ConflictProblem(f"An invoice numbered {payload.number!r} already exists")
    if await selectors.get_customer(session, ctx.organization_id, payload.customer_id) is None:
        raise NotFoundProblem("That customer is not part of this organization")
    if payload.corrects_invoice_id is not None and not await selectors.invoice_exists(
        session, ctx.organization_id, payload.corrects_invoice_id
    ):
        raise NotFoundProblem("The invoice this credit note corrects was not found")

    lines: list[InvoiceLine] = []
    subtotal = 0
    for i, line in enumerate(payload.lines, start=1):
        amount = _line_amount_minor(line.quantity, line.unit_price_minor)
        subtotal += amount
        lines.append(
            InvoiceLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                line_no=i,
                description=line.description,
                quantity=Decimal(str(line.quantity)),
                unit_price_minor=line.unit_price_minor,
                amount_minor=amount,
            )
        )

    tax_rate = Decimal(str(payload.tax_rate_pct))
    tax = int((Decimal(subtotal) * tax_rate / Decimal(100)).to_integral_value(ROUND_HALF_UP))
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
        },
        actor=_actor(ctx),
    )
    return invoice


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
