/**
 * Typed Finance API client.
 *
 * Live by default: every function calls the finance service through the gateway at the
 * same-origin `/api/v1/finance/...` prefix (Vite proxies it to `http://localhost:8100` in dev)
 * with the bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole app
 * from `fixtures.ts` with no login — handy for offline design work.
 */

import { customerName, CUSTOMERS, FIXTURE_CUSTOMERS } from "@/data/customers";
import {
  agingBuckets,
  isOpenInvoice,
  needsAttention,
  netRevenueForMonth,
  outstandingMinor,
  totalOutstanding,
  totalOverdue,
} from "@/data/derive";
import { complianceSummary, isOutstanding, latestPeriod } from "@/data/compliance";
import type { ComplianceSummary } from "@/data/compliance";
import { apiRequest } from "@atmora/client";
import {
  CASHFLOW,
  CASH_BALANCE_MINOR,
  FILINGS_BY_INVOICE,
  INVOICES,
  KPI_TRENDS,
  LEDGER_ACCOUNTS,
  PAYMENTS,
  TODAY,
  fixtureImsDetail,
  fixtureImsDocuments,
  recordFixtureDecision,
} from "./fixtures";
import type {
  CustomerStanding,
  CustomerSummary,
  EInvoiceFiling,
  ImsAction,
  ImsDecision,
  ImsDocument,
  ImsDocumentDetail,
  ImsReason,
  ImsStatus,
  Invoice,
  InvoiceStatus,
  LedgerReport,
  OverviewSummary,
  Page,
  Payment,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) working set for this org. */
const FULL_PAGE = 100;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export type InvoiceSort = "issued_desc" | "issued_asc" | "amount_desc" | "amount_asc" | "due_asc";
export type InvoiceStatusFilter = InvoiceStatus | "all";

export interface ListInvoicesParams {
  search?: string;
  status?: InvoiceStatusFilter;
  sort?: InvoiceSort;
}

function sortInvoices(rows: Invoice[], sort: InvoiceSort): Invoice[] {
  const by = [...rows];
  switch (sort) {
    case "issued_asc":
      return by.sort((a, b) => (a.issued_date < b.issued_date ? -1 : 1));
    case "amount_desc":
      return by.sort((a, b) => b.total_minor - a.total_minor);
    case "amount_asc":
      return by.sort((a, b) => a.total_minor - b.total_minor);
    case "due_asc":
      return by.sort((a, b) => (a.due_date < b.due_date ? -1 : 1));
    case "issued_desc":
    default:
      return by.sort((a, b) => (a.issued_date < b.issued_date ? 1 : -1));
  }
}

export async function listInvoices(params: ListInvoicesParams = {}): Promise<Page<Invoice>> {
  const { search = "", status = "all", sort = "issued_desc" } = params;
  if (USE_FIXTURES) {
    const q = search.trim().toLowerCase();
    const filtered = INVOICES.filter((inv) => {
      const matchesStatus = status === "all" || inv.status === status;
      const matchesQuery =
        q === "" ||
        inv.number.toLowerCase().includes(q) ||
        invoiceCustomerName(inv).toLowerCase().includes(q);
      return matchesStatus && matchesQuery;
    });
    return settle({ items: sortInvoices(filtered, sort), total: filtered.length });
  }

  const query = new URLSearchParams({ sort, size: String(FULL_PAGE) });
  if (search.trim()) query.set("q", search.trim());
  if (status !== "all") query.set("status", status);
  return apiRequest<Page<Invoice>>(`/finance/invoices?${query.toString()}`);
}

export async function getInvoice(id: string): Promise<Invoice> {
  if (USE_FIXTURES) {
    const inv = INVOICES.find((i) => i.id === id);
    if (!inv) throw new Error(`Invoice ${id} was not found.`);
    return settle(inv);
  }
  return apiRequest<Invoice>(`/finance/invoices/${encodeURIComponent(id)}`);
}

/* --------------------------------------------------------------- GST compliance */

/**
 * Every recorded attempt at registering one document, newest first.
 *
 * Behind its own permission server-side (`finance.compliance.einvoice.read`): seeing the
 * receivable book and seeing what was reported to the tax authority are different questions, and
 * an organization may reasonably answer them for different people. A 403 here is therefore an
 * ordinary outcome, not a bug — the caller renders the error and the rest of the invoice stands.
 */
export async function listFilings(invoiceId: string): Promise<EInvoiceFiling[]> {
  if (USE_FIXTURES) return settle(FILINGS_BY_INVOICE.get(invoiceId) ?? []);
  return apiRequest<EInvoiceFiling[]>(
    `/finance/invoices/${encodeURIComponent(invoiceId)}/filings`,
  );
}

export type ImsStatusFilter = ImsStatus | "all";
export type ImsReasonFilter = ImsReason | "all";
export type ImsSort = "date_asc" | "date_desc" | "amount_desc" | "amount_asc";

export interface ListImsParams {
  search?: string;
  status?: ImsStatusFilter;
  reason?: ImsReasonFilter;
  sort?: ImsSort;
}

function sortIms(rows: ImsDocument[], sort: ImsSort): ImsDocument[] {
  const by = [...rows];
  switch (sort) {
    case "date_desc":
      return by.sort((a, b) => (a.document_date < b.document_date ? 1 : -1));
    case "amount_desc":
      return by.sort((a, b) => b.total_minor - a.total_minor);
    case "amount_asc":
      return by.sort((a, b) => a.total_minor - b.total_minor);
    case "date_asc":
    default:
      // Oldest first: a queue is worked forward, and the document closest to its return deadline
      // matters more than the one that arrived most recently.
      return by.sort((a, b) => (a.document_date < b.document_date ? -1 : 1));
  }
}

/** The reconciliation queue — documents suppliers filed against this organization. */
export async function listImsDocuments(params: ListImsParams = {}): Promise<Page<ImsDocument>> {
  const { search = "", status = "all", reason = "all", sort = "date_asc" } = params;
  if (USE_FIXTURES) {
    const q = search.trim().toLowerCase();
    const filtered = fixtureImsDocuments().filter((doc) => {
      const matchesStatus = status === "all" || doc.status === status;
      const matchesReason = reason === "all" || doc.suggestion?.reason === reason;
      const matchesQuery =
        q === "" ||
        doc.supplier_name.toLowerCase().includes(q) ||
        doc.supplier_gstin.toLowerCase().includes(q) ||
        doc.document_number.toLowerCase().includes(q);
      return matchesStatus && matchesReason && matchesQuery;
    });
    return settle({ items: sortIms(filtered, sort), total: filtered.length });
  }

  const query = new URLSearchParams({ sort, size: String(FULL_PAGE) });
  if (search.trim()) query.set("q", search.trim());
  if (status !== "all") query.set("status", status);
  if (reason !== "all") query.set("reason", reason);
  return apiRequest<Page<ImsDocument>>(`/finance/ims/documents?${query.toString()}`);
}

/** One inbound document with the evidence behind its suggestion and its whole decision history. */
export async function getImsDocument(id: string): Promise<ImsDocumentDetail> {
  if (USE_FIXTURES) {
    const detail = fixtureImsDetail(id);
    if (!detail) throw new Error(`Supplier document ${id} was not found.`);
    return settle(detail);
  }
  return apiRequest<ImsDocumentDetail>(`/finance/ims/documents/${encodeURIComponent(id)}`);
}

export interface DecideImsParams {
  action: ImsAction;
  note: string | null;
  /**
   * Who decided. Live, the service takes this from the bearer token and ignores anything a client
   * says — it travels here only so fixture mode can write a history row that names a real person
   * instead of inventing one.
   */
  decidedBy: string;
}

/**
 * Record what a person decided about one inbound document.
 *
 * A POST to a `decisions` collection rather than a PATCH of a status, because that is what this
 * is: a decision is appended to a history, never written over the last one. Behind its own
 * permission and marked dangerous server-side — accepting claims the input tax credit on the
 * document and rejecting declines it. Money either way, which is why nothing here is automatic.
 */
export async function decideImsDocument(
  documentId: string,
  { action, note, decidedBy }: DecideImsParams,
): Promise<ImsDecision> {
  if (USE_FIXTURES) {
    return settle(recordFixtureDecision(documentId, action, note, decidedBy), 320);
  }
  return apiRequest<ImsDecision>(
    `/finance/ims/documents/${encodeURIComponent(documentId)}/decisions`,
    { method: "POST", body: { action, note } },
  );
}

/** The compliance position the overview shows: filing for the period, plus the inbound backlog. */
export interface CompliancePosition {
  summary: ComplianceSummary;
  /**
   * Inbound documents still awaiting a decision, or `null` when the queue could not be read —
   * a missing count degrades to "unavailable" rather than taking the whole card down with it.
   */
  imsOutstanding: number | null;
}

export async function getCompliancePosition(): Promise<CompliancePosition> {
  const invoices = await listInvoices({ sort: "issued_desc" });
  // The period the book is actually working, read off the documents rather than off a clock.
  const period = latestPeriod(invoices.items) ?? new Date().toISOString().slice(0, 7);
  const summary = complianceSummary(invoices.items, period);

  let imsOutstanding: number | null = null;
  try {
    const queue = await listImsDocuments();
    imsOutstanding = queue.items.filter((doc) => isOutstanding(doc.status)).length;
  } catch {
    // A 403, an org with no IMS data, or a service that predates the queue. Not retried — an
    // authorization failure does not fix itself — and not fatal to the filing half of the card.
    imsOutstanding = null;
  }
  return { summary, imsOutstanding };
}

export async function listPayments(): Promise<Page<Payment>> {
  if (USE_FIXTURES) return settle({ items: PAYMENTS, total: PAYMENTS.length });
  return apiRequest<Page<Payment>>(`/finance/payments?size=${FULL_PAGE}`);
}

export async function getOverview(): Promise<OverviewSummary> {
  if (USE_FIXTURES) {
    const month = TODAY.slice(0, 7);
    const revenue = netRevenueForMonth(INVOICES, month);
    const receivables = totalOutstanding(INVOICES);
    const overdue = totalOverdue(INVOICES);
    const summary: OverviewSummary = {
      period_label: "August 2026",
      kpis: [
        {
          key: "revenue",
          label: "Revenue this period",
          value_minor: revenue,
          deltaPct: KPI_TRENDS.revenue.deltaPct,
          series: [...KPI_TRENDS.revenue.series],
          caption: "Net of credit notes",
        },
        {
          key: "receivables",
          label: "Outstanding receivables",
          value_minor: receivables,
          deltaPct: KPI_TRENDS.receivables.deltaPct,
          series: [...KPI_TRENDS.receivables.series],
          caption: "Across all open invoices",
        },
        {
          key: "overdue",
          label: "Overdue",
          value_minor: overdue,
          deltaPct: KPI_TRENDS.overdue.deltaPct,
          series: [...KPI_TRENDS.overdue.series],
          caption: "Past the due date",
        },
        {
          key: "cash",
          label: "Cash balance",
          value_minor: CASH_BALANCE_MINOR,
          deltaPct: KPI_TRENDS.cash.deltaPct,
          series: [...KPI_TRENDS.cash.series],
          caption: "Bank + cash on hand",
        },
      ],
      cashflow: [...CASHFLOW],
      aging: agingBuckets(INVOICES, TODAY),
      attention: needsAttention(INVOICES),
    };
    return settle(summary);
  }
  return apiRequest<OverviewSummary>(`/finance/overview`);
}

export type CustomerSort = "receivables_desc" | "overdue_desc" | "name_asc";

export interface ListCustomersParams {
  search?: string;
  sort?: CustomerSort;
}

/** Roll each customer's open invoices into a receivable position for the customers ledger. */
function buildCustomerSummaries(): CustomerSummary[] {
  return CUSTOMERS.map((customer) => {
    const invoices = INVOICES.filter(
      (inv) => inv.customer_id === customer.id && inv.document_type === "invoice",
    );
    const open = invoices.filter(isOpenInvoice);
    const receivables = open.reduce((sum, inv) => sum + outstandingMinor(inv), 0);
    const overdue = open
      .filter((inv) => inv.status === "overdue")
      .reduce((sum, inv) => sum + outstandingMinor(inv), 0);
    const lastInvoice = invoices
      .map((inv) => inv.issued_date)
      .sort()
      .at(-1);
    const standing: CustomerStanding =
      overdue > 0 ? "overdue" : receivables > 0 ? "open" : "clear";
    return {
      customer,
      receivables_minor: receivables,
      overdue_minor: overdue,
      invoice_count: invoices.length,
      last_invoice_date: lastInvoice ?? null,
      standing,
    };
  });
}

export async function listCustomers(params: ListCustomersParams = {}): Promise<Page<CustomerSummary>> {
  const { search = "", sort = "receivables_desc" } = params;
  if (USE_FIXTURES) {
    const q = search.trim().toLowerCase();
    const rows = buildCustomerSummaries().filter(
      (row) =>
        q === "" ||
        row.customer.name.toLowerCase().includes(q) ||
        row.customer.city.toLowerCase().includes(q),
    );
    rows.sort((a, b) => {
      if (sort === "name_asc") return a.customer.name.localeCompare(b.customer.name);
      if (sort === "overdue_desc") return b.overdue_minor - a.overdue_minor;
      return b.receivables_minor - a.receivables_minor;
    });
    return settle({ items: rows, total: rows.length });
  }

  const query = new URLSearchParams({ sort, size: String(FULL_PAGE) });
  if (search.trim()) query.set("q", search.trim());
  return apiRequest<Page<CustomerSummary>>(`/finance/customers?${query.toString()}`);
}

export async function getLedger(): Promise<LedgerReport> {
  if (USE_FIXTURES) {
    return settle({
      period_label: "FY 2026–27 · to date",
      as_of: TODAY,
      accounts: [...LEDGER_ACCOUNTS],
    });
  }
  return apiRequest<LedgerReport>(`/finance/ledger`);
}

/**
 * Only reached from the fixtures branch of `listInvoices`, where searching by customer name has
 * to resolve the id locally. Live, the service does that match server-side via `?q=`.
 */
function invoiceCustomerName(inv: Invoice): string {
  return customerName(FIXTURE_CUSTOMERS, inv.customer_id);
}
