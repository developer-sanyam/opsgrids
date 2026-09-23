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
ImsStatus = str  # pending | accepted | rejected | pending_action
ImsAction = str  # accept | reject | pending_action
ImsReason = str  # matched | amount_mismatch | supplier_unknown | no_purchase_record | duplicate
ImsExpenseCategory = str  # general | ... — see domain.ITC_EXPENSE_CATEGORIES
ReturnType = str  # gstr1 | gstr7 | gstr3b
ReturnStatus = str  # draft | generated | filed
EwayStatus = str  # pending | generated | failed | cancelled | expired
WithholdingCode = str  # gst_tds_51 | itax_194q | itax_206c_1h
WithholdingBasis = str  # supply | aggregate
WithholdingDirection = str  # deducted | collected


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
    # ---------------------------------------------------------------- withholding
    # Facts about this buyer that decide whether tax is withheld on what this organization
    # bills them. Carried here (not just on `CustomerCreate`) so an edit form can prefill them —
    # they affect **future** invoices only; a document already finalized keeps what it assessed.
    is_tds_deductor: bool = False
    deducts_tds_194q: bool = False
    tcs_applicable: bool = False


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

    # ---------------------------------------------------------------- what happens next
    # Filing is automatic, so a screen's job is no longer "offer a button" but "say where this
    # document stands and what a person still has to do". These three answer that, and each is
    # derived from stored facts rather than from a clock read at render time.

    #: True once the platform has stopped trying on its own and a person has to act. A `failed`
    #: attempt is terminal: either the portal refused the document, or the retry budget ran out.
    terminal: bool = False
    #: The instant this document's IRN stops being cancellable — its acknowledgement plus 24
    #: hours. Null unless a registration currently stands.
    cancellable_until: datetime | None = None
    #: `cancel` while the window is open, `credit_note` once it has closed, null when there is
    #: nothing to correct. Exactly one of the two is ever available, and offering both is how a
    #: user discovers which by having the other fail.
    remedy: str | None = None


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
    #: True when `value_minor` itself is representative rather than computed from this tenant's
    #: own invoices and payments. False for `revenue`/`receivables`/`overdue` (real, derived from
    #: the book); true only for `cash` — there is no bank feed or general ledger to derive a cash
    #: balance from, so it is never shown as though it were this tenant's real figure.
    sample: bool = False
    #: True when the trend line (`deltaPct` + `series`) is representative rather than computed.
    #: There is no history table behind any KPI's trend today, so this is true for all four —
    #: kept separate from `sample` because a KPI's headline number can be real while its
    #: sparkline is not.
    trend_sample: bool = True


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
    #: True: the six-month cashflow chart is a representative aggregate, not derived from a real
    #: cash ledger (there is none). The UI should render its sample chip whenever this is true.
    cashflow_sample: bool = True


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
    #: Always true today: there is no general ledger table behind this report, only the
    #: representative accounts in `app.domain.LEDGER_ACCOUNTS`. Never served unflagged.
    sample: bool = True


# --------------------------------------------------------------------- write contracts
# Internal today: the SPA has no create affordances, so these drive the demo seed and any
# future write route. Amounts are minor units, validated to foot before a row is written.


class InvoiceLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    #: Possibly fractional (tonnes, cft) and negative on a credit note line.
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
    number: str = Field(max_length=40)
    customer_id: uuid.UUID
    issued_date: date
    due_date: date
    #: The document-level rate every line inherits unless it declares its own.
    tax_rate_pct: float
    lines: list[InvoiceLineCreate] = Field(min_length=1)
    document_type: DocumentType = "invoice"
    #: Force a lifecycle state the amounts cannot imply (draft, void). Otherwise derived. Not on
    #: the principal route's own contract (`InvoiceIssueIn`) — only the demo seed and any future
    #: internal caller may set this; a person raising a real document cannot force `paid`/`void`.
    status: InvoiceStatus | None = None
    #: ISO 4217, three upper-case letters. `INR` for every document today; carried as a real
    #: field rather than a constant because the IRP payload and a GSTR-1 both declare it.
    currency: str = Field(default="INR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
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


class InvoiceIssueIn(BaseModel):
    """The principal route's own contract for issuing an invoice or a credit note.

    Deliberately **not** `InvoiceCreate`: that schema carries `number` and `status`, and a public
    caller may set neither. The server owns the numbering series
    (`services.allocate_invoice_number`, race-safe per organization/document-type/financial-year)
    and the lifecycle status is always derived — there is no draft -> issued transition and no way
    to force `paid` or `void` through this route. Unknown fields are rejected rather than silently
    ignored.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: uuid.UUID
    issued_date: date
    due_date: date
    tax_rate_pct: float
    lines: list[InvoiceLineCreate] = Field(min_length=1)
    document_type: DocumentType = "invoice"
    currency: str = Field(default="INR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    notes: str | None = None
    #: Required when `document_type` is `credit_note`; refused otherwise. Checked again, server
    #: side, against the invoice it names — the same customer and organization, and no more than
    #: what remains uncredited on it.
    corrects_invoice_id: uuid.UUID | None = None
    supply_type: SupplyType | None = None
    place_of_supply: str | None = None
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


#: A loose shape check, not a deliverability check — this is a directory field, not a mailbox
#: this service ever sends to. Empty passes; anything present must look like an address.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=200, pattern=f"({_EMAIL_PATTERN})|^$")
    #: 15 characters (the GSTIN length) or null; the checksum itself is verified server-side.
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    city: str = Field(default="", max_length=120)
    state: str = Field(default="", max_length=120)
    # Withholding facts about the buyer. All three default to false, which is what keeps
    # withholding opt-in: an organization that has not told the platform about its counterparties
    # has no TDS or TCS figures invented for it. 194Q and 206C(1H) are mutually exclusive by
    # statute (`models.py:111-116`); the service refuses a write that sets both.
    #: A body notified under GST s. 51 — it must deduct 2 % on supplies above the threshold.
    is_tds_deductor: bool = False
    #: A buyer liable to deduct income tax under s. 194Q. Excludes 206C(1H) by statute.
    deducts_tds_194q: bool = False
    #: This organization collects TCS from this buyer under s. 206C(1H).
    tcs_applicable: bool = False


class CustomerUpdate(BaseModel):
    """PATCH: every field optional, only what is sent is applied
    (`model_dump(exclude_unset=True)`); everything else is validated exactly as on create."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200, pattern=f"({_EMAIL_PATTERN})|^$")
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    is_tds_deductor: bool | None = None
    deducts_tds_194q: bool | None = None
    tcs_applicable: bool | None = None


class PaymentAllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID
    #: Must be positive and, checked server-side, no more than the invoice's own outstanding —
    #: the service never trusts a caller's arithmetic over its own allocations.
    amount_minor: int = Field(gt=0)


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Bank UTR / cheque number / gateway reference. Unique per organization (409 on a repeat).
    reference: str = Field(min_length=1, max_length=40)
    customer_id: uuid.UUID
    received_date: date
    method: PaymentMethod
    allocations: list[PaymentAllocationCreate] = Field(min_length=1)
    note: str | None = None


# --------------------------------------------------------------------- purchases
# The inbound half of the book: what suppliers billed *this* organization. Thin on purpose — this
# slice needs the facts a reconciliation matches on, not an accounts-payable lifecycle.


class PurchaseDocumentOut(BaseModel):
    """A recorded purchase or expense document — the local counterpart of a supplier's filing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supplier_gstin: str
    supplier_name: str
    number: str
    document_type: DocumentType
    document_date: date
    currency: str
    taxable_minor: int
    tax_rate_pct: float
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int
    notes: str | None = None


class PurchaseDocumentCreate(BaseModel):
    """Record what a supplier billed. The tax split is **computed**, never supplied: the service
    derives CGST/SGST/IGST from the taxable value and the rate with the same `domain.split_gst`
    an invoice uses, so the purchase book and the sales book cannot disagree about arithmetic."""

    model_config = ConfigDict(extra="forbid")

    supplier_gstin: str = Field(min_length=15, max_length=15)
    number: str = Field(min_length=1, max_length=40)
    document_date: date
    taxable_minor: int = Field(gt=0)
    tax_rate_pct: float
    supplier_name: str = Field(default="", max_length=200)
    document_type: DocumentType = "invoice"
    currency: str = Field(default="INR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    notes: str | None = None


# --------------------------------------------------------------------- IMS reconciliation
# What suppliers reported *about* this business. Every row is a decision waiting for a person:
# accepting claims the input tax credit on a document, rejecting declines it and sends the
# document back. The platform suggests, with its reason; it never decides.


class ImsSuggestion(BaseModel):
    """What the platform proposes for one inbound document, and why.

    `reason` is the field a queue is worked by: "matched exactly" and "the supplier declares 500
    paise more than the purchase order" need entirely different attention, and a queue showing
    only an action makes the operator re-derive the reason for every row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suggested_action: ImsAction
    reason: ImsReason
    #: The reason as a sentence, naming the counterpart document and the difference.
    detail: str
    #: Signed minor units: the supplier's total less the recorded document's. Zero unless the
    #: reason is `amount_mismatch`.
    delta_minor: int
    matched_purchase_id: uuid.UUID | None = None
    #: On a `duplicate`, the earlier arrival this one duplicates.
    duplicate_of_id: uuid.UUID | None = None
    computed_at: datetime


class ImsDecisionOut(BaseModel):
    """One operator's decision on one inbound document — a row of history, never a status.

    Carries what the platform suggested *at the time*, so "did the operator follow the machine"
    stays answerable after a later run changes the current suggestion.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: ImsAction
    note: str | None
    suggested_action: ImsAction | None
    suggestion_reason: ImsReason | None
    expense_category: ImsExpenseCategory
    #: The subject id of whoever decided.
    decided_by: str
    decided_at: datetime


class ImsDocumentOut(BaseModel):
    """One row of the reconciliation queue.

    `status` is where the document stands — `pending` until somebody decides. `suggestion` is the
    newest advice and is null only before the first reconciliation run has seen the document;
    `decision` is the newest decision, and null is what "nobody has decided yet" looks like.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: The portal's own reference for this row — not the identity of the supplier's document.
    source_ref: str
    source: str
    supplier_gstin: str
    supplier_name: str
    document_number: str
    document_type: DocumentType
    document_date: date
    #: The return period this document lands in, `YYYY-MM`.
    period: str
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int
    #: The supplier's own IRN where they e-invoiced. Carried verbatim, not verified here.
    irn: str | None
    status: ImsStatus
    fetched_at: datetime
    suggestion: ImsSuggestion | None = None
    decision: ImsDecisionOut | None = None


class ImsDocumentDetail(BaseModel):
    """One inbound document with the evidence behind its suggestion.

    The evidence is the point of the detail view: an operator asked to accept or reject somebody
    else's claim needs to see the document it was matched to, or the earlier copy it duplicates,
    or the absence of either — not a verdict.
    """

    document: ImsDocumentOut
    #: The purchase document the suggestion matched against, where there is one.
    matched_purchase: PurchaseDocumentOut | None = None
    #: The earlier arrival this document duplicates, where it does.
    duplicate_of: ImsDocumentOut | None = None
    #: Every decision recorded against this document, newest first. Append-only, so this is the
    #: whole history and not a reconstruction.
    decisions: list[ImsDecisionOut] = Field(default_factory=list)


class ImsDecisionCreate(BaseModel):
    """Record a decision on one inbound document. Append-only: a change of mind is a further row,
    never an edit, because the question asked later is what was decided *and when*."""

    #: accept | reject | pending_action.
    action: ImsAction
    #: The operator's own note. Free text: the portal takes no reason code for an IMS action, and
    #: a dropdown would only invite an untrue answer.
    note: str | None = None
    #: What the document was for — `domain.ITC_EXPENSE_CATEGORIES`. Decides, on an `accept`,
    #: whether the ITC ledger accrues credit at all: a category in `domain.BLOCKED_ITC_CATEGORIES`
    #: accrues nothing, which is how Section 17(5) is enforced when nothing upstream classifies an
    #: inbound document any further than a supplier, a number and four money columns.
    expense_category: ImsExpenseCategory = "general"


class ImsSyncIn(BaseModel):
    """Pull one return period's supplier documents from the portal and reconcile them.

    Service-to-service, so `organization_id` travels in the body — there is no principal to read
    it from, and the internal route binds the transaction to it as its first statement.
    """

    organization_id: uuid.UUID
    #: The return period to fetch, `YYYY-MM`.
    period: str


class ImsSyncOut(BaseModel):
    """What one sync did. `fetched` counts what the portal returned, `created` what was new —
    the two differ on a re-sync, which is how the ingest's idempotency shows up as a number."""

    period: str
    portal: str
    fetched: int
    created: int
    #: How many documents the reconciliation pass produced a suggestion for.
    reconciled: int
    #: Groups every suggestion this pass produced, so a run can be read back whole.
    run_id: uuid.UUID


# --------------------------------------------------------------------- GST returns
# The outward-supplies return, assembled from the invoices already recorded. Every figure here is
# a sum of stored line splits — nothing in this section recomputes tax, because a return that
# recomputed would be a second implementation of the one calculation that must foot exactly.


class RateGroupOut(BaseModel):
    """One rate group inside a section — the grain GSTR-1 is actually declared at."""

    rate_pct: float
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int


class ReturnInvoiceOut(BaseModel):
    """One document as B2B, B2CL, CDNR, CDNUR or EXP declares it: header plus rate groups."""

    document_id: uuid.UUID
    number: str
    document_type: DocumentType
    issued_date: date
    place_of_supply: str | None
    counterparty_gstin: str | None
    counterparty_name: str
    reverse_charge: bool
    invoice_value_minor: int
    #: Set on a credit note: the number of the invoice it corrects, which is what CDNR declares
    #: alongside the note itself.
    corrects_number: str | None
    items: list[RateGroupOut]


class B2csOut(BaseModel):
    """One aggregated B2C-small figure: place of supply x rate x inter/intra-state.

    Aggregated because that is what the return declares — B2CS is not invoice-wise. Credit notes
    to unregistered buyers below the B2CL threshold net in here with their sign, so the figure
    can legitimately be negative in a heavy-returns month.
    """

    place_of_supply: str | None
    rate_pct: float
    inter_state: bool
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int
    document_count: int


class HsnSummaryOut(BaseModel):
    """One row of the HSN summary. A line with no code groups under a null rather than vanishing:
    a missing classification is a gap the return must show, not one it should quietly close."""

    hsn_sac: str | None
    rate_pct: float
    quantity: float
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int


class DocumentSeriesOut(BaseModel):
    """One declared document series — the numbers issued and how many were cancelled.

    The one section a **void** belongs in: its figures are not a supply, but its number was
    consumed, and a gap in a series is how an unreported invoice announces itself.
    """

    document_type: DocumentType
    from_number: str
    to_number: str
    total_count: int
    cancelled_count: int
    net_count: int


class ReturnTotalsOut(BaseModel):
    """The return's one-line declaration. Every section sums to exactly this."""

    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    tax_minor: int
    total_minor: int
    document_count: int
    line_count: int


class Gstr1Sections(BaseModel):
    """Every section of one GSTR-1, and the shape the snapshot is stored in.

    This *is* the persisted JSONB on `gst_returns.sections`, validated on the way in and on the
    way out, so a snapshot written by an older revision cannot be served back as something the
    contract no longer describes.
    """

    b2b: list[ReturnInvoiceOut]
    b2cl: list[ReturnInvoiceOut]
    b2cs: list[B2csOut]
    cdnr: list[ReturnInvoiceOut]
    cdnur: list[ReturnInvoiceOut]
    exp: list[ReturnInvoiceOut]
    hsn: list[HsnSummaryOut]
    docs: list[DocumentSeriesOut]


class GstReturnOut(BaseModel):
    """One period's return: the record, not the snapshot. What a list page renders."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gst_registration_id: uuid.UUID
    #: The GSTIN as it stood when the return was generated — a snapshot, like an invoice's.
    gstin: str
    state_code: str
    period: str
    return_type: ReturnType
    status: ReturnStatus
    total_taxable_minor: int
    total_cgst_minor: int
    total_sgst_minor: int
    total_igst_minor: int
    document_count: int
    line_count: int
    generated_at: datetime | None
    filed_at: datetime | None
    arn: str | None


class GstReturnTransitionOut(BaseModel):
    """One movement between statuses — a row of the append-only trail."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_status: ReturnStatus | None
    to_status: ReturnStatus
    arn: str | None
    note: str | None
    total_taxable_minor: int
    total_tax_minor: int
    transitioned_by: str
    transitioned_at: datetime


# --------------------------------------------------------------------- GSTR-3B
# The summary return: outward liability netted against the input-tax-credit ledger. Every figure
# either comes straight off the same documents GSTR-1 is assembled from, or off the ITC ledger —
# nothing here computes tax independently, for the same reason nothing in GSTR-1 does.


class TaxHeadAmountsOut(BaseModel):
    """An amount broken across the four heads GST is payable under. `cess_minor` is carried for
    forward compatibility and is always zero today — see `domain.TaxHeadAmounts`."""

    igst_minor: int
    cgst_minor: int
    sgst_minor: int
    cess_minor: int
    total_minor: int


class Gstr3bOutwardOut(BaseModel):
    """Table 3.1. Only (a) and (b) are ever non-zero — see `domain.Gstr3bOutward` for why (c),
    (d) and (e) are always zero given what this platform records today. `outward_reverse_charge`
    is not a numbered row of the form; it is the taxable value of an outward reverse-charge supply
    this organization raised, kept visible and excluded from (a) because the recipient, not this
    organization, owes the tax on it."""

    taxable_supplies: TaxHeadAmountsOut  # 3.1(a)
    outward_taxable_minor: int
    zero_rated: TaxHeadAmountsOut  # 3.1(b)
    zero_rated_taxable_minor: int
    nil_rated_taxable_minor: int  # 3.1(c) — always 0
    reverse_charge_inward_taxable_minor: int  # 3.1(d) — always 0
    non_gst_taxable_minor: int  # 3.1(e) — always 0
    outward_reverse_charge: TaxHeadAmountsOut  # not part of (a); the recipient's liability
    outward_reverse_charge_taxable_minor: int


class Gstr3bItcOut(BaseModel):
    """Table 4, in the shape Notification 14/2022-CT gave it — see `domain.Gstr3bItc`.

    4A(5) is **gross**, including credit blocked under s. 17(5); 4B splits the reversal the return
    itself asks for; 4C is the net the ledger actually carries forward; 4D is informational and
    always zero — neither a Rule 37 re-claim nor a s. 16(4)/place-of-supply ineligibility is
    modelled.
    """

    available: TaxHeadAmountsOut  # 4A(5), gross
    reversed_section_17_5: TaxHeadAmountsOut  # 4B(1)
    reversed_other: TaxHeadAmountsOut  # 4B(2)
    reclaimed: TaxHeadAmountsOut  # 4D(1) — always zero
    ineligible_other: TaxHeadAmountsOut  # 4D(2) — always zero
    total_reversed: TaxHeadAmountsOut  # 4B(1) + 4B(2)
    net_eligible: TaxHeadAmountsOut  # 4C = 4A - 4B


class UtilisationLineOut(BaseModel):
    """One application of credit against liability — see `domain.utilise_itc`."""

    head: str
    against: str
    amount_minor: int


class Gstr3bPaymentOut(BaseModel):
    """Table 6.1 — the liability, how credit covered it, and what is left for cash. Late fee and
    interest are out of scope for this slice; see the README."""

    liability: TaxHeadAmountsOut
    utilisation: list[UtilisationLineOut]
    credit_remaining: TaxHeadAmountsOut
    cash_payable: TaxHeadAmountsOut
    #: The magnitude of any negative liability handed in — a heavy-credit-note period. Informational
    #: only: never netted into `cash_payable` and never spendable as credit.
    excess_liability: TaxHeadAmountsOut
    #: The magnitude of any negative *available credit* handed in — a credit note's own negative
    #: accrual outrunning what a head had otherwise accrued. Informational only: never treated as
    #: a liability and never added to `cash_payable`.
    credit_deficit: TaxHeadAmountsOut


class Gstr3bSections(BaseModel):
    """Every table of one GSTR-3B, and the shape the snapshot is stored in — the `gstr3b` analogue
    of `Gstr1Sections`."""

    outward: Gstr3bOutwardOut
    itc: Gstr3bItcOut
    itc_brought_forward: TaxHeadAmountsOut
    payment: Gstr3bPaymentOut


class GstReturnDetail(BaseModel):
    """The return with its sections and its whole status trail.

    `sections` is null on a `draft` — a period nobody has assembled has nothing to declare, and
    an empty return and an unassembled one are different answers. Which concrete shape it is —
    `Gstr1Sections` or `Gstr3bSections` — follows `ret.return_type`; `selectors.return_detail`
    picks the validator, since the two tables share nothing a discriminator could read.
    """

    ret: GstReturnOut
    sections: Gstr1Sections | Gstr3bSections | None
    transitions: list[GstReturnTransitionOut]


class GenerateReturnIn(BaseModel):
    """Assemble one period's return from the invoices already recorded.

    Generating twice replaces the snapshot with what the book says *now* and appends a second
    `generated` transition, which is the honest answer before filing. After filing it is refused
    — a filed return is what was reported, and the remedy for a later correction is the diff.
    """

    #: The tax period, `YYYY-MM`.
    period: str
    return_type: ReturnType = "gstr1"
    #: Which registration to file under. Defaults to the organization's primary one.
    gst_registration_id: uuid.UUID | None = None


class FileReturnIn(BaseModel):
    """Record that a generated return went to the portal, with the ARN it came back with."""

    #: The portal's Acknowledgement Reference Number. The one fact about a return that only
    #: exists after it has left this platform, so it is required rather than derived.
    arn: str
    note: str | None = None


class SectionDeltaOut(BaseModel):
    """How one section moved between the snapshot and the book as it stands now."""

    section: str
    snapshot: ReturnTotalsOut
    current: ReturnTotalsOut
    delta: ReturnTotalsOut
    changed: bool


class ReturnDiffOut(BaseModel):
    """What has changed since the return was generated — never a rewrite of the return.

    The whole reason a generated return is frozen. An invoice corrected after August was filed
    does not make August a different return; it makes a **difference**, which is what an
    amendment in a later period declares. This endpoint is that difference, per section and in
    total, so the amendment is prepared from an answer rather than from a memory.
    """

    ret: GstReturnOut
    #: The book as it stands at `computed_at`, re-assembled from the same invoices.
    current: ReturnTotalsOut
    snapshot: ReturnTotalsOut
    delta: ReturnTotalsOut
    sections: list[SectionDeltaOut]
    #: True when anything at all moved. The cheap question a screen asks first.
    changed: bool
    computed_at: datetime


# --------------------------------------------------------------------- the ITC ledger


class ItcLedgerEntryOut(BaseModel):
    """One posting to the input-tax-credit ledger. Append-only: `accrual`, `reversal` or
    `utilisation` — never an edit to an earlier row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gst_registration_id: uuid.UUID
    #: The period this posting is attributed to for GSTR-3B purposes — may be later than
    #: `source_period` when the source event arrived after that period's own return was filed.
    period: str
    #: The period the source event itself belongs to, kept apart from `period` purely for audit.
    source_period: str
    tax_head: str
    kind: str
    #: Signed: positive on an accrual, negative on a reversal or a utilisation.
    amount_minor: int
    source_type: str
    source_id: uuid.UUID
    description: str
    posted_by: str
    posted_at: datetime


class ItcHeadBalanceOut(BaseModel):
    """One tax head's position for one period: what accrued, what reversed, what was consumed,
    and the balance carried forward — `sum(amount_minor)` up to and including this period."""

    tax_head: str
    accrued_minor: int
    reversed_minor: int
    utilised_minor: int
    balance_minor: int


class ItcBalanceOut(BaseModel):
    """The ITC ledger's position for one registration as of one period — the figure Table 6.1
    utilises on top of the period's own net eligible credit."""

    gst_registration_id: uuid.UUID
    period: str
    heads: list[ItcHeadBalanceOut]
    total_balance_minor: int


# --------------------------------------------------------------------- e-Way bills
# The third portal. Unlike an IRN, a bill expires — so every read of one is a read *at a moment*,
# and `effective_status` is what the bill is now rather than what was written down.


class EwayBillOut(BaseModel):
    """One recorded attempt to raise an e-Way bill. Append-only: a retry is a further row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    #: The **stored** status, as written. `expired` is not among the ones the live path writes.
    status: EwayStatus
    #: The status as of now — `expired` once `valid_until` has passed. Derived on read, because
    #: the table is append-only and nothing can update a row when the clock moves past it.
    effective_status: EwayStatus
    eway_bill_no: str | None
    transport_mode: str
    distance_km: int
    vehicle_no: str | None
    transporter_id: str | None
    transport_doc_no: str | None
    transport_doc_date: date | None
    over_dimensional: bool
    consignment_value_minor: int
    valid_from: datetime | None
    #: Computed from `valid_from` and `distance_km` by `domain.eway_valid_until` and stored, so a
    #: screen and a checkpoint are reading the same arithmetic over the same two values.
    valid_until: datetime | None
    #: `cancel` while the 24-hour window is open, null once it has closed or there is nothing to
    #: withdraw. One answer, never a set of flags: a screen must offer the action that will work.
    remedy: str | None
    error_code: str | None
    error: str | None
    attempted_at: datetime


class EwayState(BaseModel):
    """A document's *current* e-Way position, derived from its newest attempt.

    Never null on a document that needs a bill: an unmet obligation must be **visible** rather
    than absent, which is the same reason `EInvoiceState.required` exists.
    """

    required: bool
    status: EwayStatus | None
    eway_bill_no: str | None
    valid_from: datetime | None
    valid_until: datetime | None
    #: True once the platform has stopped: a refusal stands until the consignment changes.
    terminal: bool
    #: The instant the cancellation window closes, or null when there is nothing to withdraw.
    cancellable_until: datetime | None
    remedy: str | None
    attempts: int


class EwayBillWithInvoice(BaseModel):
    """One bill on a list page, with enough of its document to be recognisable."""

    bill: EwayBillOut
    invoice_number: str
    customer_name: str


class GenerateEwayIn(BaseModel):
    """Raise an e-Way bill for one document's goods movement.

    Service-to-service, so `organization_id` travels in the body. The transport facts are the
    caller's — a distance and a vehicle are not derivable from an invoice — and everything else
    comes off the frozen document.
    """

    organization_id: uuid.UUID
    invoice_id: uuid.UUID
    #: Approximate road distance in kilometres. The **input** to validity, stored so the deadline
    #: stays recomputable.
    distance_km: int
    transport_mode: str = "road"
    vehicle_no: str | None = None
    transporter_id: str | None = None
    transport_doc_no: str | None = None
    transport_doc_date: date | None = None
    over_dimensional: bool = False


class CancelEwayIn(BaseModel):
    """Withdraw a raised e-Way bill, inside the portal's 24-hour window."""

    organization_id: uuid.UUID
    invoice_id: uuid.UUID
    #: 1 Duplicate | 2 Order cancelled | 3 Data entry mistake | 4 Others. No free text.
    reason_code: str
    remark: str = ""


# --------------------------------------------------------------------- withholding: TDS / TCS
# What somebody else already paid to a tax authority out of what they owe this organization, and
# what this organization collected on top. Two statutes, three heads, one register.


class WithholdingRateOut(BaseModel):
    """One version of one withholding head. Global vocabulary, versioned by `effective_from`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: WithholdingCode
    name: str
    rate_pct: float
    threshold_minor: int
    basis: WithholdingBasis
    jurisdiction: str
    effective_from: date
    effective_to: date | None


class InvoiceWithholdingOut(BaseModel):
    """One deduction or collection on one document — a row of the register.

    `rate_pct` and `threshold_minor` are **snapshots** of the version named by
    `withholding_rate_id`, not a live read of the schedule: a rate change must not restate a
    figure somebody has already been paid net of.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    code: WithholdingCode
    direction: WithholdingDirection
    withholding_rate_id: uuid.UUID
    rate_pct: float
    threshold_minor: int
    basis: WithholdingBasis
    #: What the rate was applied to: the whole taxable value for a per-supply head, this
    #: document's share of the excess over the threshold for an aggregate one.
    basis_minor: int
    amount_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    counterparty_gstin: str | None
    counterparty_name: str
    resolved_on: date
    period: str


class WithholdingWithInvoice(BaseModel):
    """One register row on a list page, with enough of its document to be recognisable."""

    withholding: InvoiceWithholdingOut
    invoice_number: str
    invoice_issued_date: date
    invoice_total_minor: int


class WithholdingDeductorRow(BaseModel):
    """One counterparty's line in the GSTR-7-style summary.

    GSTR-7 is declared **per deductor**: the number they hold, the value they deducted against,
    and the three heads. Grouping the register that way is what makes the platform's figures
    comparable to the return the deductor actually files — which is the reconciliation a supplier
    does, and the reason this summary exists on the receivable side at all.
    """

    counterparty_gstin: str | None
    counterparty_name: str
    document_count: int
    basis_minor: int
    amount_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int


class WithholdingSummaryOut(BaseModel):
    """One period's withholding, grouped the way the return declares it, and footed.

    `total_*` sums the rows exactly; a test asserts it, because a summary that does not foot to
    its own rows is worse than no summary.
    """

    period: str
    code: WithholdingCode | None
    direction: WithholdingDirection | None
    rows: list[WithholdingDeductorRow]
    document_count: int
    total_basis_minor: int
    total_amount_minor: int
    total_cgst_minor: int
    total_sgst_minor: int
    total_igst_minor: int
