/**
 * Wire types for the Finance product.
 *
 * These mirror the shape a `finance-service` will return once it ships (see the platform rules in
 * CLAUDE.md): every monetary field is an **integer number of minor units** (paise) in a single
 * ISO currency, invoices and the credit ledger are **append-only** (a correction is a new
 * credit-note row, never an edit), and each row is tenant-scoped by `organization_id` server-side.
 * The UI never does money arithmetic in floats — see `@/lib/money`.
 */

import type { Minor } from "@/lib/money";

/** A customer (the party billed). Names resolved client-side from `customer_id`. */
export interface Customer {
  id: string;
  name: string;
  contact_name: string;
  email: string;
  /** GST identification number, or null for an unregistered buyer. */
  gstin: string | null;
  city: string;
  state: string;
  /**
   * Withholding facts about this buyer, decided by the customer record and applied to **future**
   * invoices only — a document already finalized keeps what it assessed at the time.
   */
  is_tds_deductor: boolean;
  /** Mutually exclusive with `tcs_applicable` — the server refuses a write that sets both. */
  deducts_tds_194q: boolean;
  tcs_applicable: boolean;
}

/** `POST /finance/customers`. */
export interface CustomerCreate {
  name: string;
  contact_name?: string;
  email?: string;
  gstin?: string | null;
  city?: string;
  state?: string;
  is_tds_deductor?: boolean;
  deducts_tds_194q?: boolean;
  tcs_applicable?: boolean;
}

/** `PATCH /finance/customers/{id}` — every field optional; only what is sent is applied. */
export interface CustomerUpdate {
  name?: string;
  contact_name?: string;
  email?: string;
  gstin?: string | null;
  city?: string;
  state?: string;
  is_tds_deductor?: boolean;
  deducts_tds_194q?: boolean;
  tcs_applicable?: boolean;
}

/**
 * Invoice lifecycle. `draft` not yet issued; `sent` issued and open; `overdue` open past its due
 * date; `paid` fully settled; `void` cancelled (append-only: a void is a status, not a delete).
 */
export type InvoiceStatus = "draft" | "sent" | "paid" | "overdue" | "void";

/** A tax invoice bills; a credit note corrects a prior invoice with a negative total. */
export type DocumentType = "invoice" | "credit_note";

/**
 * One line on an invoice. `amount_minor` foots to `quantity × unit_price_minor`, and is the
 * **taxable value** the line's GST is computed on.
 *
 * The GST fields are optional here because documents raised before the compliance slice carry
 * none — a null HSN is "not recorded", which is a real state and one the filing screen surfaces
 * rather than hides. Exactly one tax shape is ever populated: CGST+SGST intra-state, IGST
 * inter-state, and the three always sum to the tax charged.
 */
export interface InvoiceLine {
  id: string;
  description: string;
  quantity: number;
  unit_price_minor: Minor;
  amount_minor: Minor;
  /** HSN (goods) or SAC (services) code — 4, 6 or 8 digits. */
  hsn_sac?: string | null;
  tax_rate_pct?: number;
  cgst_minor?: Minor;
  sgst_minor?: Minor;
  igst_minor?: Minor;
}

/** How a supply is classified for GST. B2C is the one that needs no IRN. */
export type SupplyType = "b2b" | "b2c" | "export" | "sez";

/**
 * One attempt at registering a document with the Invoice Registration Portal. `cancelling` is a
 * withdrawal the platform has asked for and not yet seen confirmed — it is only ever the newest
 * row while that is true; once answered, the history shows the answer instead.
 */
export type FilingStatus = "pending" | "filed" | "failed" | "cancelled" | "cancelling";

/**
 * An invoice's current e-invoice position, derived server-side from its newest filing attempt.
 *
 * `required` is the statutory question and is answered even when nothing has been attempted, so
 * `{ required: true, status: null }` is exactly the "not yet filed" queue rather than an absence.
 */
export interface EInvoiceState {
  required: boolean;
  status: FilingStatus | null;
  /** The 64-character Invoice Reference Number, once registered. */
  irn?: string | null;
  ack_no?: string | null;
  ack_date?: string | null;
  /** The portal's own numbered error and message on a failed attempt. */
  error_code?: string | null;
  error?: string | null;
  attempted_at?: string | null;
  attempts: number;
  /**
   * True once the platform has stopped filing this document on its own and a person has to act:
   * either the portal refused it, or the retry budget ran out.
   */
  terminal?: boolean;
  /** When this document's IRN stops being cancellable — its acknowledgement plus 24 hours. */
  cancellable_until?: string | null;
  /**
   * Which correction is still available, and it is never both: `cancel` while the portal's window
   * is open, `credit_note` once it has closed, `null` when there is nothing standing to correct.
   * Offering both is how a user finds out which is right by having the other fail.
   */
  remedy?: FilingRemedy | null;
}

/**
 * The corrections for a filed document. Mutually exclusive in time. `contact_support` follows a
 * withdrawal the platform could not confirm before the window shut: the IRN may or may not still
 * stand at the portal, and only a look there says which.
 */
export type FilingRemedy = "cancel" | "credit_note" | "contact_support";

/** One row of a document's filing history. Append-only server-side: a retry is a further row. */
export interface EInvoiceFiling {
  id: string;
  status: FilingStatus;
  irn: string | null;
  ack_no: string | null;
  ack_date: string | null;
  /** The signed QR the invoice must print, verbatim as the portal returned it. */
  qr_payload: string | null;
  error_code: string | null;
  error: string | null;
  attempted_at: string;
}

/** The organization's own GST registration — who this seller is, to the portal. */
export interface GstRegistration {
  id: string;
  gstin: string;
  state_code: string;
  state_name: string;
  legal_name: string;
  trade_name: string;
  registration_type: string;
  is_primary: boolean;
}

/** `regular | composition | casual`. */
export type GstRegistrationType = "regular" | "composition" | "casual";

/** `PUT /finance/gst/registration`. */
export interface GstRegistrationUpsert {
  gstin: string;
  legal_name: string;
  trade_name?: string;
  registration_type?: GstRegistrationType;
  is_primary?: boolean;
}

/**
 * An invoice or credit note. Totals foot exactly:
 * `subtotal_minor = Σ line.amount_minor`, `total_minor = subtotal_minor + tax_minor`. A credit
 * note carries negative amounts and a `corrects_invoice_id`.
 */
export interface Invoice {
  id: string;
  number: string;
  document_type: DocumentType;
  customer_id: string;
  issued_date: string; // YYYY-MM-DD
  due_date: string; // YYYY-MM-DD
  status: InvoiceStatus;
  currency: string; // ISO 4217, always "INR" on this org
  lines: InvoiceLine[];
  subtotal_minor: Minor;
  tax_rate_pct: number;
  tax_minor: Minor;
  total_minor: Minor;
  /** Settled so far, summed from the payments allocated to this document. */
  amount_paid_minor: Minor;
  notes: string | null;
  /** Set on a credit note: the invoice this document corrects. */
  corrects_invoice_id: string | null;

  // --- GST. Frozen with the rest of the document; both GSTINs are snapshots, not lookups, so a
  // filed invoice keeps naming the numbers it was raised under after either party re-registers.
  /** The GST state code the supply is taxed in. */
  place_of_supply?: string | null;
  supply_type?: SupplyType;
  /** The recipient pays the tax instead of the supplier: rate stated, nothing charged. */
  reverse_charge?: boolean;
  seller_gstin?: string | null;
  buyer_gstin?: string | null;
  cgst_minor?: Minor;
  sgst_minor?: Minor;
  igst_minor?: Minor;
  /** Where this document stands with the portal. Never null from the live API. */
  einvoice?: EInvoiceState;
}

/** One line as typed into the issue-invoice form. */
export interface InvoiceLineCreate {
  description: string;
  /** Possibly fractional (tonnes, cft), and negative on a credit note line. */
  quantity: number;
  unit_price_minor: Minor;
  /** HSN (goods) or SAC (services) code — 4, 6 or 8 digits. */
  hsn_sac?: string | null;
  /** Overrides the document's rate for this line. */
  tax_rate_pct?: number | null;
}

/**
 * `POST /finance/invoices` — the principal route's own contract. Deliberately not `number` or
 * `status`: the server allocates the number and always derives the lifecycle status.
 */
export interface InvoiceIssueIn {
  customer_id: string;
  issued_date: string;
  due_date: string;
  tax_rate_pct: number;
  lines: InvoiceLineCreate[];
  document_type: DocumentType;
  currency?: string;
  notes?: string | null;
  /** Required when `document_type` is `credit_note`; refused otherwise. */
  corrects_invoice_id?: string | null;
  supply_type?: SupplyType | null;
  place_of_supply?: string | null;
  reverse_charge?: boolean;
}

/* --------------------------------------------------------------------- IMS reconciliation
 * What suppliers reported *about* this business. Every row is a decision waiting for a person:
 * accepting claims the input tax credit on a document, rejecting declines it and sends the
 * document back. The platform suggests, with its reason; it never decides. */

/** Where an inbound document stands. `pending` until somebody decides. */
export type ImsStatus = "pending" | "accepted" | "rejected" | "pending_action";

/** What an operator may decide. `pending` is not a decision — it is the absence of one. */
export type ImsAction = "accept" | "reject" | "pending_action";

/**
 * Why the platform suggests what it suggests. This is the field a queue is actually worked by:
 * "matched exactly" and "the supplier declares more than the purchase order" need entirely
 * different attention, so a row that shows only an action makes the operator re-derive the reason.
 */
export type ImsReason =
  | "matched"
  | "amount_mismatch"
  | "supplier_unknown"
  | "no_purchase_record"
  | "duplicate";

/** A supplier may file an invoice, a credit note or a debit note against a recipient. */
export type ImsDocumentType = "invoice" | "credit_note" | "debit_note";

/**
 * What an accepted document was for. Decides whether the ITC ledger accrues credit on it at all:
 * every category but `general` is blocked under GST s. 17(5), and accepting one of those still
 * records the decision but claims no input tax credit. Asked for on every decision, not only
 * `accept`, so a `reject` or `pending_action` still records what the person understood the
 * document to be.
 */
export type ImsExpenseCategory =
  | "general"
  | "motor_vehicle"
  | "food_beverage"
  | "membership_club_fitness"
  | "works_contract_immovable"
  | "personal_consumption";

/** What the platform proposes for one inbound document, and why. Never applied on its own. */
export interface ImsSuggestion {
  id: string;
  suggested_action: ImsAction;
  reason: ImsReason;
  /** The reason as a sentence from the reconciler, naming the counterpart document. */
  detail: string;
  /** Signed minor units: the supplier's total less the recorded document's. Zero unless mismatch. */
  delta_minor: Minor;
  matched_purchase_id?: string | null;
  /** On a `duplicate`, the earlier arrival this one duplicates. */
  duplicate_of_id?: string | null;
  computed_at: string;
}

/**
 * One operator's decision on one inbound document — a row of history, never a status. Carries
 * what the platform suggested *at the time*, so "did the operator follow the machine" stays
 * answerable after a later run changes the current suggestion.
 */
export interface ImsDecision {
  id: string;
  action: ImsAction;
  note: string | null;
  suggested_action: ImsAction | null;
  suggestion_reason: ImsReason | null;
  expense_category: ImsExpenseCategory;
  /** The subject id of whoever decided. */
  decided_by: string;
  decided_at: string;
}

/** One row of the reconciliation queue: a document a supplier filed against this organization. */
export interface ImsDocument {
  id: string;
  /** The portal's own reference for this row — not the identity of the supplier's document. */
  source_ref: string;
  source: string;
  supplier_gstin: string;
  supplier_name: string;
  document_number: string;
  document_type: ImsDocumentType;
  document_date: string; // YYYY-MM-DD
  /** The return period this document lands in, `YYYY-MM`. */
  period: string;
  taxable_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  total_minor: Minor;
  /** The supplier's own IRN where they e-invoiced. Carried verbatim, not verified. */
  irn: string | null;
  status: ImsStatus;
  fetched_at: string;
  /** The newest advice; null only before the first reconciliation run has seen the document. */
  suggestion?: ImsSuggestion | null;
  /** The newest decision. Null is what "nobody has decided yet" looks like. */
  decision?: ImsDecision | null;
}

/** A recorded purchase document — the local counterpart a supplier's filing is matched against. */
export interface PurchaseDocument {
  id: string;
  supplier_gstin: string;
  supplier_name: string;
  number: string;
  document_type: DocumentType;
  document_date: string;
  currency: string;
  taxable_minor: Minor;
  tax_rate_pct: number;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  total_minor: Minor;
  notes?: string | null;
}

/**
 * `POST /finance/purchases`. The tax split is computed server-side from `taxable_minor` and
 * `tax_rate_pct` — never supplied.
 */
export interface PurchaseDocumentCreate {
  supplier_gstin: string;
  number: string;
  document_date: string;
  taxable_minor: Minor;
  tax_rate_pct: number;
  supplier_name?: string;
  document_type?: DocumentType;
  currency?: string;
  notes?: string | null;
}

/**
 * One inbound document with the evidence behind its suggestion.
 *
 * The evidence is the point of the detail view: an operator asked to accept or reject somebody
 * else's claim on this business needs to see the document it was matched to, or the earlier copy
 * it duplicates, or the absence of either — not a verdict.
 */
export interface ImsDocumentDetail {
  document: ImsDocument;
  matched_purchase?: PurchaseDocument | null;
  duplicate_of?: ImsDocument | null;
  /** Every decision recorded against this document, newest first. Append-only. */
  decisions: ImsDecision[];
}

export type PaymentMethod = "bank_transfer" | "upi" | "card" | "cheque" | "cash";

/** How much of a received payment was applied to one invoice. */
export interface PaymentAllocation {
  invoice_id: string;
  invoice_number: string;
  amount_minor: Minor;
}

/** A payment received from a customer, split across one or more invoices. */
export interface Payment {
  id: string;
  reference: string;
  customer_id: string;
  received_date: string; // YYYY-MM-DD
  method: PaymentMethod;
  amount_minor: Minor;
  allocations: PaymentAllocation[];
  note: string | null;
}

/** One allocation as typed into the record-payment form. */
export interface PaymentAllocationCreate {
  invoice_id: string;
  amount_minor: Minor;
}

/** `POST /finance/payments`. */
export interface PaymentCreate {
  /** Bank UTR / cheque number / gateway reference. Unique per organization (409 on a repeat). */
  reference: string;
  customer_id: string;
  received_date: string;
  method: PaymentMethod;
  allocations: PaymentAllocationCreate[];
  note?: string | null;
}

/** One AR-aging bucket: how much receivable sits in a given age band. */
export interface AgingBucket {
  key: string;
  label: string;
  amount_minor: Minor;
  count: number;
}

/**
 * A headline KPI for the overview — trimmed to the fields the Overview screen actually reads.
 * The wire response (`services/finance/app/schemas.py`) also carries `deltaPct`/`series`/
 * `sample`/`trend_sample`: every KPI's trend line is a representative constant with no history
 * table behind it yet (flagged `trend_sample: true` on every one), and `cash` itself is fully
 * representative (`sample: true`, no bank feed or ledger to derive a balance from). Those fields
 * are deliberately not declared here — the dashboard audit's do-not-render list — so nothing on
 * this screen can read them by accident. Real per-currency deltas come from `revenue_history`.
 */
export interface OverviewKpi {
  key: string;
  label: string;
  /** Amount in minor units; the UI decides compact vs full. */
  value_minor: Minor;
  caption: string;
}

/**
 * One point of finance's real 12-month history (`MonthlyRevenuePoint`,
 * `services/finance/app/schemas.py`). Never a sample: `revenue_minor`/`received_minor`/
 * `outstanding_minor` are all derived from this tenant's own invoices, payments and allocations.
 */
export interface MonthlyRevenuePoint {
  /** `YYYY-MM`. */
  month: string;
  /** e.g. `"Sep 2026"`. */
  label: string;
  revenue_minor: Minor;
  received_minor: Minor;
  outstanding_minor: Minor;
}

/**
 * A real revenue/receivables history, scoped to one currency (`CurrencyRevenueHistory`). Finance
 * never sums money across currencies (ADR-0000 §8), so a multi-currency tenant would get one of
 * these per currency it has actually issued or been paid in — today this organization issues only
 * in INR. `*_delta_pct` is `null`, not `0`, when the previous month has nothing to compare
 * against: an invented delta is worse than no delta.
 */
export interface CurrencyRevenueHistory {
  currency: string;
  revenue_current_minor: Minor;
  revenue_previous_minor: Minor;
  revenue_delta_pct: number | null;
  received_current_minor: Minor;
  received_previous_minor: Minor;
  received_delta_pct: number | null;
  outstanding_current_minor: Minor;
  outstanding_previous_minor: Minor;
  outstanding_delta_pct: number | null;
  /** 12 months, oldest first, ending at the current month. */
  months: MonthlyRevenuePoint[];
}

/**
 * Everything the Overview screen needs, trimmed to real figures. The wire response also carries
 * `cashflow`/`cashflow_sample` — a six-month cash chart with no real cash ledger behind it, always
 * flagged `cashflow_sample: true` — which are deliberately not declared here for the same reason
 * `OverviewKpi`'s trend fields aren't: Overview never reads them (dashboard audit §5.8).
 */
export interface OverviewSummary {
  period_label: string;
  kpis: OverviewKpi[];
  aging: AgingBucket[];
  /** Overdue invoices, largest outstanding first — the "needs attention" list. */
  attention: Invoice[];
  /** Real, per-currency revenue/receivables history. Empty when this tenant has no invoices yet. */
  revenue_history: CurrencyRevenueHistory[];
}

/** One line of the P&L / trial-balance summary. */
export interface LedgerAccount {
  code: string;
  name: string;
  type: "revenue" | "expense" | "contra_revenue";
  debit_minor: Minor;
  credit_minor: Minor;
}

/** The reports payload: the account rows plus the period they cover. */
export interface LedgerReport {
  period_label: string;
  as_of: string;
  accounts: LedgerAccount[];
  /** Always true today: there is no general ledger table behind this report. */
  sample?: boolean;
}

/** A customer's account standing, rolled up from their invoices. */
export type CustomerStanding = "overdue" | "open" | "clear";

/** One row of the customers ledger: the party plus their receivable position. */
export interface CustomerSummary {
  customer: Customer;
  receivables_minor: Minor;
  overdue_minor: Minor;
  invoice_count: number;
  last_invoice_date: string | null;
  standing: CustomerStanding;
}

/** The envelope every list endpoint returns: `items`, plus enough to drive a real pager. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* --------------------------------------------------------------------- GST returns
 * The outward-supplies return, assembled from the invoices already recorded. */

export type ReturnType = "gstr1" | "gstr7" | "gstr3b";
export type ReturnStatus = "draft" | "generated" | "filed";

/** One period's return: the record, not the snapshot. What the list page renders. */
export interface GstReturn {
  id: string;
  gst_registration_id: string;
  gstin: string;
  state_code: string;
  period: string;
  return_type: ReturnType;
  status: ReturnStatus;
  total_taxable_minor: Minor;
  total_cgst_minor: Minor;
  total_sgst_minor: Minor;
  total_igst_minor: Minor;
  document_count: number;
  line_count: number;
  generated_at: string | null;
  filed_at: string | null;
  arn: string | null;
}

/** One movement between statuses — a row of the append-only trail. */
export interface GstReturnTransition {
  id: string;
  from_status: ReturnStatus | null;
  to_status: ReturnStatus;
  arn: string | null;
  note: string | null;
  total_taxable_minor: Minor;
  total_tax_minor: Minor;
  transitioned_by: string;
  transitioned_at: string;
}

/** One rate group inside a section — the grain GSTR-1 is actually declared at. */
export interface RateGroup {
  rate_pct: number;
  taxable_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  total_minor: Minor;
}

/** One document as B2B, B2CL, CDNR, CDNUR or EXP declares it. */
export interface ReturnInvoice {
  document_id: string;
  number: string;
  document_type: DocumentType;
  issued_date: string;
  place_of_supply: string | null;
  counterparty_gstin: string | null;
  counterparty_name: string;
  reverse_charge: boolean;
  invoice_value_minor: Minor;
  corrects_number: string | null;
  items: RateGroup[];
}

/** One aggregated B2C-small figure: place of supply x rate x inter/intra-state. */
export interface B2cs {
  place_of_supply: string | null;
  rate_pct: number;
  inter_state: boolean;
  taxable_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  total_minor: Minor;
  document_count: number;
}

/** One row of the HSN summary. */
export interface HsnSummary {
  hsn_sac: string | null;
  rate_pct: number;
  quantity: number;
  taxable_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  total_minor: Minor;
}

/** One declared document series — numbers issued and how many were cancelled. */
export interface DocumentSeries {
  document_type: DocumentType;
  from_number: string;
  to_number: string;
  total_count: number;
  cancelled_count: number;
  net_count: number;
}

/** Every section of one GSTR-1, and the shape the snapshot is stored in. */
export interface Gstr1Sections {
  b2b: ReturnInvoice[];
  b2cl: ReturnInvoice[];
  b2cs: B2cs[];
  cdnr: ReturnInvoice[];
  cdnur: ReturnInvoice[];
  exp: ReturnInvoice[];
  hsn: HsnSummary[];
  docs: DocumentSeries[];
}

/* --------------------------------------------------------------------- GSTR-3B
 * The summary return: outward liability netted against the input-tax-credit ledger. Every figure
 * is either summed straight off the invoices GSTR-1 reads too, or read off the ITC ledger. */

/** An amount broken across the four heads GST is payable under. `cess_minor` is always zero
 * today — no table in this platform carries a cess column yet. */
export interface TaxHeadAmounts {
  igst_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  cess_minor: Minor;
  total_minor: Minor;
}

/** Table 3.1. Only (a) and (b) are ever non-zero — see the finance service's `domain.py` for why
 * (c) nil/exempt, (d) reverse-charge inward and (e) non-GST outward always report zero today. */
export interface Gstr3bOutward {
  taxable_supplies: TaxHeadAmounts;
  outward_taxable_minor: Minor;
  zero_rated: TaxHeadAmounts;
  zero_rated_taxable_minor: Minor;
  nil_rated_taxable_minor: Minor;
  reverse_charge_inward_taxable_minor: Minor;
  non_gst_taxable_minor: Minor;
  /** Not a numbered row of the form: the taxable value of an outward reverse-charge supply this
   * organization raised. Excluded from (a) and from `Gstr3bPayment.liability` because the
   * recipient, not this organization, owes the tax on it. */
  outward_reverse_charge: TaxHeadAmounts;
  outward_reverse_charge_taxable_minor: Minor;
}

/**
 * Table 4, in the shape Notification 14/2022-CT gave it. 4A(5) is **gross** — including credit
 * blocked under s. 17(5) — 4B splits the reversal into the two rows the return itself asks for,
 * 4C is the net the ledger actually carries forward, and 4D is informational and always zero:
 * neither a Rule 37 re-claim nor a s. 16(4)/place-of-supply ineligibility is modelled.
 */
export interface Gstr3bItc {
  available: TaxHeadAmounts; // 4A(5), gross
  reversed_section_17_5: TaxHeadAmounts; // 4B(1)
  reversed_other: TaxHeadAmounts; // 4B(2)
  reclaimed: TaxHeadAmounts; // 4D(1) — always zero
  ineligible_other: TaxHeadAmounts; // 4D(2) — always zero
  total_reversed: TaxHeadAmounts; // 4B(1) + 4B(2)
  net_eligible: TaxHeadAmounts; // 4C = 4A - 4B
}

/** One application of credit against liability in the statutory order — IGST first against IGST,
 * then CGST, then SGST; CGST against CGST then IGST, never SGST; SGST mirrors it. */
export interface UtilisationLine {
  head: string;
  against: string;
  amount_minor: Minor;
}

/** Table 6.1 — the liability, how credit covered it, and what is left for cash. Late fee and
 * interest are out of scope for this slice. */
export interface Gstr3bPayment {
  liability: TaxHeadAmounts;
  utilisation: UtilisationLine[];
  credit_remaining: TaxHeadAmounts;
  cash_payable: TaxHeadAmounts;
  /** The magnitude of any negative liability handed in — a heavy-credit-note period. Informational
   * only: never netted into `cash_payable` and never spendable as credit. */
  excess_liability: TaxHeadAmounts;
}

/** Every table of one GSTR-3B, and the shape the snapshot is stored in. */
export interface Gstr3bSections {
  outward: Gstr3bOutward;
  itc: Gstr3bItc;
  itc_brought_forward: TaxHeadAmounts;
  payment: Gstr3bPayment;
}

/** The return with its sections and its whole status trail. `sections` is null on a `draft`; its
 * concrete shape follows `ret.return_type` — `Gstr1Sections` for `gstr1`/`gstr7`, `Gstr3bSections`
 * for `gstr3b`. */
export interface GstReturnDetail {
  ret: GstReturn;
  sections: Gstr1Sections | Gstr3bSections | null;
  transitions: GstReturnTransition[];
}

/** The return's one-line declaration. Every section sums to exactly this. */
export interface ReturnTotals {
  taxable_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  tax_minor: Minor;
  total_minor: Minor;
  document_count: number;
  line_count: number;
}

/** How one section moved between the snapshot and the book as it stands now. */
export interface SectionDelta {
  section: string;
  snapshot: ReturnTotals;
  current: ReturnTotals;
  delta: ReturnTotals;
  changed: boolean;
}

/** What has changed since the return was generated — never a rewrite of the return itself. */
export interface ReturnDiff {
  ret: GstReturn;
  current: ReturnTotals;
  snapshot: ReturnTotals;
  delta: ReturnTotals;
  sections: SectionDelta[];
  changed: boolean;
  computed_at: string;
}

/** `POST /finance/gst/returns`. */
export interface GenerateReturnIn {
  period: string;
  return_type?: ReturnType;
  gst_registration_id?: string | null;
}

/** `POST /finance/gst/returns/{id}/filing`. */
export interface FileReturnIn {
  arn: string;
  note?: string | null;
}

/* --------------------------------------------------------------------- e-Way bills */

/**
 * `submitting` and `cancelling` are a generation or a withdrawal the platform has asked for and not
 * yet seen answered — shown only while that is true.
 */
export type EwayStatus =
  | "pending"
  | "generated"
  | "failed"
  | "cancelled"
  | "expired"
  | "submitting"
  | "cancelling";

/** `contact_support` follows a withdrawal the platform could not confirm before the window shut. */
export type EwayRemedy = "cancel" | "contact_support";

/** One recorded attempt to raise an e-Way bill. Append-only: a retry is a further row. */
export interface EwayBill {
  id: string;
  invoice_id: string;
  status: EwayStatus;
  /** The status as of now — `expired` once `valid_until` has passed. */
  effective_status: EwayStatus;
  eway_bill_no: string | null;
  transport_mode: string;
  distance_km: number;
  vehicle_no: string | null;
  transporter_id: string | null;
  transport_doc_no: string | null;
  transport_doc_date: string | null;
  over_dimensional: boolean;
  consignment_value_minor: Minor;
  valid_from: string | null;
  valid_until: string | null;
  /** `cancel` while the 24-hour window is open, null once it has closed. Read-only in this app. */
  remedy: EwayRemedy | null;
  error_code: string | null;
  error: string | null;
  attempted_at: string;
}

/** One bill on a list page, with enough of its document to be recognisable. */
export interface EwayBillWithInvoice {
  bill: EwayBill;
  invoice_number: string;
  customer_name: string;
}

/** A document's current e-Way position, derived from its newest attempt. */
export interface EwayState {
  required: boolean;
  status: EwayStatus | null;
  eway_bill_no: string | null;
  valid_from: string | null;
  valid_until: string | null;
  terminal: boolean;
  cancellable_until: string | null;
  remedy: EwayRemedy | null;
  attempts: number;
}

/* --------------------------------------------------------------------- withholding: TDS / TCS */

export type WithholdingCode = "gst_tds_51" | "itax_194q" | "itax_206c_1h";
export type WithholdingBasis = "supply" | "aggregate";
export type WithholdingDirection = "deducted" | "collected";

/** One version of one withholding head. Global vocabulary, versioned by `effective_from`. */
export interface WithholdingRate {
  id: string;
  code: WithholdingCode;
  name: string;
  rate_pct: number;
  threshold_minor: Minor;
  basis: WithholdingBasis;
  jurisdiction: string;
  effective_from: string;
  effective_to: string | null;
}

/** One deduction or collection on one document — a row of the register. */
export interface InvoiceWithholding {
  id: string;
  invoice_id: string;
  code: WithholdingCode;
  direction: WithholdingDirection;
  withholding_rate_id: string;
  rate_pct: number;
  threshold_minor: Minor;
  basis: WithholdingBasis;
  basis_minor: Minor;
  amount_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
  counterparty_gstin: string | null;
  counterparty_name: string;
  resolved_on: string;
  period: string;
}

/** One register row on a list page, with enough of its document to be recognisable. */
export interface WithholdingWithInvoice {
  withholding: InvoiceWithholding;
  invoice_number: string;
  invoice_issued_date: string;
  invoice_total_minor: Minor;
}

/** One counterparty's line in the GSTR-7-style summary. */
export interface WithholdingDeductorRow {
  counterparty_gstin: string | null;
  counterparty_name: string;
  document_count: number;
  basis_minor: Minor;
  amount_minor: Minor;
  cgst_minor: Minor;
  sgst_minor: Minor;
  igst_minor: Minor;
}

/** One period's withholding, grouped the way the return declares it, and footed. */
export interface WithholdingSummary {
  period: string;
  code: WithholdingCode | null;
  direction: WithholdingDirection | null;
  rows: WithholdingDeductorRow[];
  document_count: number;
  total_basis_minor: Minor;
  total_amount_minor: Minor;
  total_cgst_minor: Minor;
  total_sgst_minor: Minor;
  total_igst_minor: Minor;
}
