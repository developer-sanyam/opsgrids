"""Request and response contracts for finance.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the finance SPA (`frontend/product-finance/src/api/types.ts`) generates its screens from
exactly these shapes. Every monetary field is an **integer number of minor units** (paise) in a
single ISO currency (`INR` on this org); invoices and the credit ledger are append-only, so a
correction is a new credit-note document, never an edit.

The `Out` models are the read surface the gateway serves. The `*Create` models are the write
contracts the demo seed (and any future write route) drives — there is no public invoice- or
payment-creation affordance in the SPA yet, so they are internal to the service today.

**GST is part of the contract, not an annexe to it.** An invoice carries its place of supply, its
supply type, both parties' GSTINs as snapshots, and its tax split across CGST/SGST/IGST; a line
carries its HSN/SAC and its own rate. Every one of those is an integer or a code, never a
computed float, because they are what a GSTR-1 return and an e-invoice payload each declare
independently — and the two must agree to the paisa or a human reconciles them by hand.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

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
SupplyType = str  # b2b | b2c | export | sez
FilingStatus = str  # pending | filed | failed | cancelled


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
    """One line on an invoice. `amount_minor` foots to `quantity x unit_price_minor`, and is the
    **taxable value** the line's GST is computed on.

    Exactly one tax shape is populated: `cgst_minor` + `sgst_minor` on an intra-state supply,
    `igst_minor` on an inter-state one. The three always sum to the tax actually charged.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    quantity: float
    unit_price_minor: int
    amount_minor: int
    #: HSN (goods) or SAC (services) code. Null on documents raised before GST fields existed.
    hsn_sac: str | None = None
    tax_rate_pct: float = 0
    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0


class EInvoiceFilingOut(BaseModel):
    """One recorded attempt to register a document with the Invoice Registration Portal.

    Append-only: this is a row of history, never a mutable status. A retry is a further row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: FilingStatus
    #: The 64-character Invoice Reference Number, on a `filed` or `cancelled` attempt.
    irn: str | None
    ack_no: str | None
    ack_date: datetime | None
    #: The signed QR the invoice must print, verbatim as the portal returned it.
    qr_payload: str | None
    #: The portal's own numbered error and message on a `failed` attempt.
    error_code: str | None
    error: str | None
    attempted_at: datetime


class EInvoiceState(BaseModel):
    """An invoice's *current* e-invoice position, derived from its newest filing row.

    Carried on the invoice read model so a screen never has to fetch the history to know whether
    a document is registered. `required` is the statutory question (B2B, export and SEZ documents
    need an IRN; B2C does not), and it is answered even when nothing has been attempted — that is
    what makes an un-filed obligation visible instead of silent.
    """

    required: bool
    status: FilingStatus | None
    irn: str | None = None
    ack_no: str | None = None
    ack_date: datetime | None = None
    error_code: str | None = None
    error: str | None = None
    attempted_at: datetime | None = None
    #: How many attempts have been recorded for this document.
    attempts: int = 0


class InvoiceOut(BaseModel):
    """An invoice or credit note. Totals foot exactly:
    `subtotal_minor = Σ line.amount_minor`, `total_minor = subtotal_minor + tax_minor`,
    and `tax_minor = cgst_minor + sgst_minor + igst_minor = Σ line tax`.
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

    # ---------------------------------------------------------------- GST
    #: The GST state code the supply is taxed in.
    place_of_supply: str | None = None
    supply_type: SupplyType = "b2b"
    reverse_charge: bool = False
    #: Both GSTINs as they stood when the document was finalized — snapshots, not lookups.
    seller_gstin: str | None = None
    buyer_gstin: str | None = None
    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0
    #: Where this document stands with the portal. Never null: an unfiled document reports
    #: `required` with a null `status`, so an unmet obligation is visible rather than absent.
    einvoice: EInvoiceState


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
    #: HSN (goods) or SAC (services) code — 4, 6 or 8 digits. Optional at the contract because a
    #: document may legitimately be raised before the code is known; a filing attempt then fails
    #: with the portal's own "HSN required", which is the right place for that to surface.
    hsn_sac: str | None = None
    #: Overrides the document's rate for this line — a freight or installation line on a goods
    #: invoice is the everyday case. Defaults to the document's `tax_rate_pct`.
    tax_rate_pct: float | None = None


class InvoiceCreate(BaseModel):
    number: str
    customer_id: uuid.UUID
    issued_date: date
    due_date: date
    #: The document-level rate every line inherits unless it declares its own.
    tax_rate_pct: float
    lines: list[InvoiceLineCreate] = Field(min_length=1)
    document_type: DocumentType = "invoice"
    #: Force a lifecycle state the amounts cannot imply (draft, void). Otherwise derived.
    status: InvoiceStatus | None = None
    currency: str = "INR"
    notes: str | None = None
    corrects_invoice_id: uuid.UUID | None = None

    # ---------------------------------------------------------------- GST
    #: b2b | b2c | export | sez. Defaults to `b2b` when the customer holds a GSTIN and `b2c`
    #: when they do not — the derivation is the service's, so a caller need not restate it.
    supply_type: SupplyType | None = None
    #: The GST state code the supply is taxed in. Derived when omitted: the buyer's GSTIN state,
    #: else their recorded state, else the seller's own — never guessed from anything else.
    place_of_supply: str | None = None
    #: The recipient pays the tax instead of the supplier. The document carries the rate and no
    #: tax amounts.
    reverse_charge: bool = False


class GstRegistrationOut(BaseModel):
    """The organization's own GST registration — who this seller is, to the portal."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gstin: str
    #: The first two characters of the GSTIN: the state this registration is in, and the seller
    #: side of every intra/inter-state decision.
    state_code: str
    state_name: str
    legal_name: str
    trade_name: str
    registration_type: str
    is_primary: bool


class GstRegistrationUpsert(BaseModel):
    """Register or update this organization's GSTIN. The number is parsed and checksum-verified
    before a row is written — an unverified GSTIN is not stored anywhere in this service."""

    gstin: str
    legal_name: str
    trade_name: str = ""
    registration_type: str = "regular"
    is_primary: bool = True


# --------------------------------------------------------------------- e-invoicing (internal)
# Driven service-to-service, not by a person: an auto-filing agent watching the outbox is the
# intended caller. `organization_id` travels in the body because there is no principal to read it
# from — the internal route binds the transaction to it as its first statement.


class FileInvoiceIn(BaseModel):
    """Ask for one document to be registered with the portal, and record what came back."""

    organization_id: uuid.UUID
    invoice_id: uuid.UUID


class CancelFilingIn(BaseModel):
    """Withdraw a registered IRN, inside the portal's 24-hour window."""

    organization_id: uuid.UUID
    invoice_id: uuid.UUID
    #: 1 Duplicate · 2 Data entry mistake · 3 Order cancelled · 4 Others. The portal takes no
    #: free-text alternative.
    reason_code: str
    remark: str = ""


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
