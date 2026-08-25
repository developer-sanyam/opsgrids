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
import { apiRequest } from "./http";
import {
  CASHFLOW,
  CASH_BALANCE_MINOR,
  INVOICES,
  KPI_TRENDS,
  LEDGER_ACCOUNTS,
  PAYMENTS,
  TODAY,
} from "./fixtures";
import type {
  CustomerStanding,
  CustomerSummary,
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
