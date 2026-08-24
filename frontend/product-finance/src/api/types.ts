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

/** One line on an invoice. `amount_minor` foots to `quantity × unit_price_minor`. */
export interface InvoiceLine {
  id: string;
  description: string;
  quantity: number;
  unit_price_minor: Minor;
  amount_minor: Minor;
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
