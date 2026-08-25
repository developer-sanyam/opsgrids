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

/** One attempt at registering a document with the Invoice Registration Portal. */
export type FilingStatus = "pending" | "filed" | "failed" | "cancelled";

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

/** The two corrections for a filed document. Mutually exclusive in time. */
export type FilingRemedy = "cancel" | "credit_note";

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

/** One AR-aging bucket: how much receivable sits in a given age band. */
export interface AgingBucket {
  key: string;
  label: string;
  amount_minor: Minor;
  count: number;
}

/** A month of cash movement for the cashflow chart. */
export interface CashflowPoint {
  label: string;
  inflow_minor: Minor;
  outflow_minor: Minor;
}

/** A headline KPI for the overview, pre-derived from the ledger. */
export interface OverviewKpi {
  key: string;
  label: string;
  /** Amount in minor units; the UI decides compact vs full. */
  value_minor: Minor;
  deltaPct: number;
  /** A short trend series (already scaled to whatever unit reads best) for the sparkline. */
  series: number[];
  caption: string;
}

/** Everything the Overview screen needs, in one payload. */
export interface OverviewSummary {
  period_label: string;
  kpis: OverviewKpi[];
  cashflow: CashflowPoint[];
  aging: AgingBucket[];
  /** Overdue invoices, largest outstanding first — the "needs attention" list. */
  attention: Invoice[];
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

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}
