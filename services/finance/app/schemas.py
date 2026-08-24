"""Request and response contracts for finance.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the finance SPA (`frontend/product-finance/src/api/types.ts`) generates its screens from
exactly these shapes. Every monetary field is an **integer number of minor units** (paise) in a
single ISO currency (`INR` on this org); invoices and the credit ledger are append-only, so a
correction is a new credit-note document, never an edit.

The `Out` models are the read surface the gateway serves. The `*Create` models are the write
contracts the demo seed (and any future write route) drives — there is no public invoice- or
payment-creation affordance in the SPA yet, so they are internal to the service today.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------- service metadata


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- literals
# Kept as plain strings rather than enums on the wire so the contract stays additive: a new
# payment method or status is a value, not a breaking schema change. The service validates
# against the vocabularies in `app.domain`.

InvoiceStatus = str  # draft | sent | paid | overdue | void
DocumentType = str  # invoice | credit_note
PaymentMethod = str  # bank_transfer | upi | card | cheque | cash
CustomerStanding = str  # overdue | open | clear


# --------------------------------------------------------------------- customers


class CustomerOut(BaseModel):
    """A billed party. `id` is the finance customer id invoices and payments reference."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact_name: str
    email: str
    #: GST identification number, or null for an unregistered buyer.
    gstin: str | None
    city: str
    state: str


class CustomerSummary(BaseModel):
    """One row of the customers ledger: the party plus their receivable position."""

    customer: CustomerOut
    receivables_minor: int
    overdue_minor: int
    invoice_count: int
    last_invoice_date: date | None
    standing: CustomerStanding


# --------------------------------------------------------------------- invoices


class InvoiceLineOut(BaseModel):
    """One line on an invoice. `amount_minor` foots to `quantity x unit_price_minor`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    quantity: float
    unit_price_minor: int
    amount_minor: int


class InvoiceOut(BaseModel):
    """An invoice or credit note. Totals foot exactly:
    `subtotal_minor = Σ line.amount_minor`, `total_minor = subtotal_minor + tax_minor`.
    A credit note carries negative amounts and a `corrects_invoice_id`.
    """

    id: uuid.UUID
    number: str
    document_type: DocumentType
    customer_id: uuid.UUID
    issued_date: date
    due_date: date
    status: InvoiceStatus
    currency: str
    lines: list[InvoiceLineOut]
    subtotal_minor: int
    tax_rate_pct: float
    tax_minor: int
    total_minor: int
    #: Settled so far, summed from the payment allocations pointing at this document.
    amount_paid_minor: int
    notes: str | None
    corrects_invoice_id: uuid.UUID | None


# --------------------------------------------------------------------- payments


class PaymentAllocationOut(BaseModel):
    """How much of a received payment was applied to one invoice."""

    invoice_id: uuid.UUID
    invoice_number: str
    amount_minor: int


class PaymentOut(BaseModel):
    """A payment received from a customer, split across one or more invoices."""

    id: uuid.UUID
    reference: str
    customer_id: uuid.UUID
    received_date: date
    method: PaymentMethod
    amount_minor: int
    allocations: list[PaymentAllocationOut]
    note: str | None


# --------------------------------------------------------------------- overview


class OverviewKpi(BaseModel):
    """A headline KPI for the overview, pre-derived from the ledger."""

    key: str
    label: str
    value_minor: int
    #: Percentage change vs the prior period. Field name mirrors the SPA contract exactly.
    deltaPct: float
    series: list[float]
    caption: str


class CashflowPoint(BaseModel):
    label: str
    inflow_minor: int
    outflow_minor: int


class AgingBucket(BaseModel):
    key: str
    label: str
    amount_minor: int
    count: int


class OverviewSummary(BaseModel):
    """Everything the Overview screen needs, in one payload."""

    period_label: str
    kpis: list[OverviewKpi]
    cashflow: list[CashflowPoint]
    aging: list[AgingBucket]
    #: Overdue invoices, largest outstanding first — the "needs attention" list.
    attention: list[InvoiceOut]


# --------------------------------------------------------------------- ledger


class LedgerAccount(BaseModel):
    code: str
    name: str
    type: str  # revenue | expense | contra_revenue
    debit_minor: int
    credit_minor: int


class LedgerReport(BaseModel):
    period_label: str
    as_of: date
    accounts: list[LedgerAccount]


# --------------------------------------------------------------------- write contracts
# Internal today: the SPA has no create affordances, so these drive the demo seed and any
# future write route. Amounts are minor units, validated to foot before a row is written.


class InvoiceLineCreate(BaseModel):
    description: str
    quantity: float
    unit_price_minor: int


class InvoiceCreate(BaseModel):
    number: str
    customer_id: uuid.UUID
    issued_date: date
    due_date: date
    tax_rate_pct: float
    lines: list[InvoiceLineCreate] = Field(min_length=1)
    document_type: DocumentType = "invoice"
    #: Force a lifecycle state the amounts cannot imply (draft, void). Otherwise derived.
    status: InvoiceStatus | None = None
    currency: str = "INR"
    notes: str | None = None
    corrects_invoice_id: uuid.UUID | None = None


class CustomerCreate(BaseModel):
    name: str
    contact_name: str
    email: str
    gstin: str | None = None
    city: str
    state: str


class PaymentAllocationCreate(BaseModel):
    invoice_id: uuid.UUID
    amount_minor: int


class PaymentCreate(BaseModel):
    reference: str
    customer_id: uuid.UUID
    received_date: date
    method: PaymentMethod
    allocations: list[PaymentAllocationCreate] = Field(min_length=1)
    note: str | None = None
