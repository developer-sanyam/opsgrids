/**
 * Typed Finance API client.
 *
 * Live by default: every function calls the finance service through the gateway at the
 * same-origin `/api/v1/finance/...` prefix (Vite proxies it to `http://localhost:8100` in dev)
 * with the bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole app
 * from `fixtures.ts` with no login — handy for offline design work; fixture writes mutate the
 * in-memory fixture arrays so the screen behaves the same as live. Gated on `import.meta.env.DEV`,
 * so a stray production env var can never ship fixture mode.
 *
 * Every list function takes the server's own filters, sort and paging and passes them straight
 * through — nothing here re-filters, re-sorts or caps a result set the server already decided.
 */

import {
  addFixtureCustomer,
  customerName,
  CUSTOMERS,
  FIXTURE_CUSTOMERS,
  removeFixtureCustomer,
  updateFixtureCustomer,
} from "@/data/customers";
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
import { today as realToday } from "@/lib/format";
import { ApiError, apiRequest } from "@opsgrids/client";
import {
  CASHFLOW,
  CASH_BALANCE_MINOR,
  EWAY_BILLS,
  FILINGS_BY_INVOICE,
  fixtureReturnDetail,
  fixtureReturnDiff,
  GST_REGISTRATION,
  INVOICES,
  KPI_TRENDS,
  LEDGER_ACCOUNTS,
  PAYMENTS,
  PURCHASES,
  RETURNS,
  SELLER_GSTIN,
  TODAY,
  WITHHOLDINGS,
  WITHHOLDING_RATES,
  fixtureImsDetail,
  fixtureImsDocuments,
  recordFixtureDecision,
} from "./fixtures";
import type {
  Customer,
  CustomerCreate,
  CustomerStanding,
  CustomerSummary,
  CustomerUpdate,
  EInvoiceFiling,
  EwayBillWithInvoice,
  EwayState,
  FileReturnIn,
  GenerateReturnIn,
  GstRegistration,
  GstRegistrationUpsert,
  GstReturn,
  GstReturnDetail,
  Invoice,
  InvoiceIssueIn,
  InvoiceStatus,
  ImsAction,
  ImsDecision,
  ImsDocument,
  ImsDocumentDetail,
  ImsExpenseCategory,
  ImsReason,
  ImsStatus,
  LedgerReport,
  OverviewSummary,
  Page,
  Payment,
  PaymentCreate,
  PurchaseDocument,
  PurchaseDocumentCreate,
  ReturnDiff,
  ReturnStatus,
  ReturnType,
  WithholdingCode,
  WithholdingRate,
  WithholdingSummary,
  WithholdingWithInvoice,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** The real default page size for a paginated list screen. */
const DEFAULT_PAGE_SIZE = 25;
/** The backend's own ceiling (`platform_core/pagination.py`) — used where a screen genuinely
 *  needs the whole (small) working set for this org, such as the customer-name directory. */
const MAX_PAGE_SIZE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const s = query.toString();
  return s ? `?${s}` : "";
}

function paginate<T>(items: T[], page: number, size: number): Page<T> {
  const total = items.length;
  const pages = total === 0 ? 0 : Math.ceil(total / size);
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total, page, size, pages };
}

/* ------------------------------------------------------------------ invoices */

export type InvoiceSort = "issued_desc" | "issued_asc" | "amount_desc" | "amount_asc" | "due_asc";
export type InvoiceStatusFilter = InvoiceStatus | "all";

export interface ListInvoicesParams {
  search?: string;
  status?: InvoiceStatusFilter;
  sort?: InvoiceSort;
  customerId?: string;
  page?: number;
  size?: number;
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
  const {
    search = "",
    status = "all",
    sort = "issued_desc",
    customerId,
    page = 1,
    size = DEFAULT_PAGE_SIZE,
  } = params;
  if (USE_FIXTURES) {
    const q = search.trim().toLowerCase();
    const filtered = INVOICES.filter((inv) => {
      const matchesStatus = status === "all" || inv.status === status;
      const matchesCustomer = customerId === undefined || inv.customer_id === customerId;
      const matchesQuery =
        q === "" ||
        inv.number.toLowerCase().includes(q) ||
        invoiceCustomerName(inv).toLowerCase().includes(q);
      return matchesStatus && matchesCustomer && matchesQuery;
    });
    return settle(paginate(sortInvoices(filtered, sort), page, size));
  }

  const query = buildQuery({
    sort,
    page,
    size,
    q: search.trim() || undefined,
    status: status !== "all" ? status : undefined,
    customer_id: customerId,
  });
  return apiRequest<Page<Invoice>>(`/finance/invoices${query}`);
}

export async function getInvoice(id: string): Promise<Invoice> {
  if (USE_FIXTURES) {
    const inv = INVOICES.find((i) => i.id === id);
    if (!inv) throw new Error(`Invoice ${id} was not found.`);
    return settle(inv);
  }
  return apiRequest<Invoice>(`/finance/invoices/${encodeURIComponent(id)}`);
}

let fixtureInvoiceSeq = INVOICES.length + 1;

/** A simplified estimate of the GST split, for the fixture-mode preview and record only — the
 *  live path always renders the server's own totals, never a client computation. */
function estimateSplit(amountMinor: number, ratePct: number, placeOfSupply: string) {
  const tax = Math.round((amountMinor * ratePct) / 100);
  const intraState = placeOfSupply === SELLER_GSTIN.slice(0, 2);
  const half = Math.trunc(Math.abs(tax) / 2);
  const cgst = tax < 0 ? -half : half;
  return {
    cgst_minor: intraState ? cgst : 0,
    sgst_minor: intraState ? tax - cgst : 0,
    igst_minor: intraState ? 0 : tax,
    tax_minor: tax,
  };
}

/**
 * Issue an invoice, or a credit note against one — `POST /finance/invoices`. The server allocates
 * the number and derives the lifecycle status and every tax figure; this function never sends a
 * computed total, only what the form actually asked for.
 */
export async function issueInvoice(input: InvoiceIssueIn): Promise<Invoice> {
  if (USE_FIXTURES) {
    const buyer = FIXTURE_CUSTOMERS.get(input.customer_id) ?? null;
    const placeOfSupply = input.place_of_supply ?? buyer?.gstin?.slice(0, 2) ?? SELLER_GSTIN.slice(0, 2);
    const lines = input.lines.map((line, i) => {
      const amount = Math.round(line.unit_price_minor * line.quantity);
      const split = estimateSplit(amount, line.tax_rate_pct ?? input.tax_rate_pct, placeOfSupply);
      return {
        id: `fixture-line-${fixtureInvoiceSeq}-${i}`,
        description: line.description,
        quantity: line.quantity,
        unit_price_minor: line.unit_price_minor,
        amount_minor: amount,
        hsn_sac: line.hsn_sac ?? null,
        tax_rate_pct: line.tax_rate_pct ?? input.tax_rate_pct,
        cgst_minor: split.cgst_minor,
        sgst_minor: split.sgst_minor,
        igst_minor: split.igst_minor,
      };
    });
    const subtotal = lines.reduce((sum, l) => sum + l.amount_minor, 0);
    const cgst = lines.reduce((sum, l) => sum + l.cgst_minor, 0);
    const sgst = lines.reduce((sum, l) => sum + l.sgst_minor, 0);
    const igst = lines.reduce((sum, l) => sum + l.igst_minor, 0);
    const tax = cgst + sgst + igst;
    const prefix = input.document_type === "credit_note" ? "CN" : "INV";
    const number = `${prefix}-FIX-${String(fixtureInvoiceSeq++).padStart(4, "0")}`;
    const invoice: Invoice = {
      id: `fixture-${number}`,
      number,
      document_type: input.document_type,
      customer_id: input.customer_id,
      issued_date: input.issued_date,
      due_date: input.due_date,
      status: "sent",
      currency: input.currency ?? "INR",
      lines,
      subtotal_minor: subtotal,
      tax_rate_pct: input.tax_rate_pct,
      tax_minor: tax,
      total_minor: subtotal + tax,
      amount_paid_minor: 0,
      notes: input.notes ?? null,
      corrects_invoice_id: input.corrects_invoice_id ?? null,
      place_of_supply: placeOfSupply,
      supply_type: input.supply_type ?? (buyer?.gstin ? "b2b" : "b2c"),
      reverse_charge: input.reverse_charge ?? false,
      seller_gstin: SELLER_GSTIN,
      buyer_gstin: buyer?.gstin ?? null,
      cgst_minor: cgst,
      sgst_minor: sgst,
      igst_minor: igst,
      einvoice: { required: (input.supply_type ?? (buyer?.gstin ? "b2b" : "b2c")) !== "b2c", status: null, attempts: 0 },
    };
    INVOICES.unshift(invoice);
    return settle(invoice, 360);
  }
  return apiRequest<Invoice>("/finance/invoices", { method: "POST", body: input });
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
  page?: number;
  size?: number;
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
  const { search = "", status = "all", reason = "all", sort = "date_asc", page = 1, size = DEFAULT_PAGE_SIZE } = params;
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
    return settle(paginate(sortIms(filtered, sort), page, size));
  }

  const query = buildQuery({
    sort,
    page,
    size,
    q: search.trim() || undefined,
    status: status !== "all" ? status : undefined,
    reason: reason !== "all" ? reason : undefined,
  });
  return apiRequest<Page<ImsDocument>>(`/finance/ims/documents${query}`);
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
   * What the document was for. Decides whether accepting it accrues any input tax credit — a
   * category s. 17(5) blocks accrues nothing, which the person accepting has to say rather than
   * the platform guessing from a document that carries no HSN or description to guess from.
   */
  expenseCategory: ImsExpenseCategory;
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
  { action, note, expenseCategory, decidedBy }: DecideImsParams,
): Promise<ImsDecision> {
  if (USE_FIXTURES) {
    return settle(
      recordFixtureDecision(documentId, action, note, expenseCategory, decidedBy),
      320,
    );
  }
  return apiRequest<ImsDecision>(
    `/finance/ims/documents/${encodeURIComponent(documentId)}/decisions`,
    { method: "POST", body: { action, note, expense_category: expenseCategory } },
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
  // The rollup this card draws is an aggregate over the whole book, not a page of it — the
  // largest size the backend allows, same as the customer directory below.
  const invoices = await listInvoices({ sort: "issued_desc", size: MAX_PAGE_SIZE });
  // The period the book is actually working, read off the documents rather than off a clock.
  const period = latestPeriod(invoices.items) ?? realToday().slice(0, 7);
  const summary = complianceSummary(invoices.items, period);

  let imsOutstanding: number | null = null;
  try {
    const queue = await listImsDocuments({ size: MAX_PAGE_SIZE });
    imsOutstanding = queue.items.filter((doc) => isOutstanding(doc.status)).length;
  } catch {
    // A 403, an org with no IMS data, or a service that predates the queue. Not retried — an
    // authorization failure does not fix itself — and not fatal to the filing half of the card.
    imsOutstanding = null;
  }
  return { summary, imsOutstanding };
}

/* ------------------------------------------------------------------- GST registration */

/** `GET /finance/gst/registration`. 404 means none configured — callers treat that as an
 *  empty state, not an error, by catching `ApiError` with `status === 404`. */
export async function getGstRegistration(): Promise<GstRegistration> {
  if (USE_FIXTURES) return settle(GST_REGISTRATION);
  return apiRequest<GstRegistration>("/finance/gst/registration");
}

export async function setGstRegistration(input: GstRegistrationUpsert): Promise<GstRegistration> {
  if (USE_FIXTURES) {
    const updated: GstRegistration = {
      ...GST_REGISTRATION,
      gstin: input.gstin,
      state_code: input.gstin.slice(0, 2),
      legal_name: input.legal_name,
      trade_name: input.trade_name ?? "",
      registration_type: input.registration_type ?? "regular",
      is_primary: input.is_primary ?? true,
    };
    Object.assign(GST_REGISTRATION, updated);
    return settle(GST_REGISTRATION, 320);
  }
  return apiRequest<GstRegistration>("/finance/gst/registration", { method: "PUT", body: input });
}

/* ------------------------------------------------------------------------ GST returns */

export type ReturnStatusFilter = ReturnStatus | "all";
export type ReturnTypeFilter = ReturnType | "all";
export type ReturnSort = "period_desc" | "period_asc";

export interface ListReturnsParams {
  period?: string;
  status?: ReturnStatusFilter;
  returnType?: ReturnTypeFilter;
  sort?: ReturnSort;
  page?: number;
  size?: number;
}

export async function listReturns(params: ListReturnsParams = {}): Promise<Page<GstReturn>> {
  const { period, status = "all", returnType = "all", sort = "period_desc", page = 1, size = DEFAULT_PAGE_SIZE } = params;
  if (USE_FIXTURES) {
    const filtered = RETURNS.filter(
      (r) =>
        (period === undefined || period === "" || r.period === period) &&
        (status === "all" || r.status === status) &&
        (returnType === "all" || r.return_type === returnType),
    );
    const sorted = [...filtered].sort((a, b) =>
      sort === "period_asc" ? (a.period < b.period ? -1 : 1) : a.period < b.period ? 1 : -1,
    );
    return settle(paginate(sorted, page, size));
  }
  const query = buildQuery({
    period,
    status: status !== "all" ? status : undefined,
    return_type: returnType !== "all" ? returnType : undefined,
    sort,
    page,
    size,
  });
  return apiRequest<Page<GstReturn>>(`/finance/gst/returns${query}`);
}

export async function getReturn(id: string): Promise<GstReturnDetail> {
  if (USE_FIXTURES) {
    const detail = fixtureReturnDetail(id);
    if (!detail) throw new Error(`Return ${id} was not found.`);
    return settle(detail);
  }
  return apiRequest<GstReturnDetail>(`/finance/gst/returns/${encodeURIComponent(id)}`);
}

export async function getReturnDiff(id: string): Promise<ReturnDiff> {
  if (USE_FIXTURES) {
    const diff = fixtureReturnDiff(id);
    if (!diff) throw new Error(`Return ${id} was not found.`);
    return settle(diff);
  }
  return apiRequest<ReturnDiff>(`/finance/gst/returns/${encodeURIComponent(id)}/diff`);
}

export async function generateReturn(input: GenerateReturnIn): Promise<GstReturn> {
  if (USE_FIXTURES) {
    const existing = RETURNS.find(
      (r) => r.period === input.period && r.return_type === (input.return_type ?? "gstr1"),
    );
    if (existing?.status === "filed") {
      throw new ApiError(409, "This period is already filed.", {
        detail: "This period is already filed and cannot be regenerated.",
      });
    }
    const ret: GstReturn = existing ?? {
      id: `ret-fixture-${input.period}-${input.return_type ?? "gstr1"}`,
      gst_registration_id: GST_REGISTRATION.id,
      gstin: GST_REGISTRATION.gstin,
      state_code: GST_REGISTRATION.state_code,
      period: input.period,
      return_type: input.return_type ?? "gstr1",
      status: "draft",
      total_taxable_minor: 0,
      total_cgst_minor: 0,
      total_sgst_minor: 0,
      total_igst_minor: 0,
      document_count: 0,
      line_count: 0,
      generated_at: null,
      filed_at: null,
      arn: null,
    };
    ret.status = "generated";
    ret.generated_at = new Date().toISOString();
    if (!existing) RETURNS.unshift(ret);
    return settle(ret, 360);
  }
  return apiRequest<GstReturn>("/finance/gst/returns", { method: "POST", body: input });
}

export async function fileReturn(id: string, input: FileReturnIn): Promise<GstReturn> {
  if (USE_FIXTURES) {
    const ret = RETURNS.find((r) => r.id === id);
    if (!ret) throw new Error(`Return ${id} was not found.`);
    if (ret.status === "filed") {
      throw new ApiError(409, "Already filed.", { detail: "This return has already been filed." });
    }
    ret.status = "filed";
    ret.filed_at = new Date().toISOString();
    ret.arn = input.arn;
    return settle(ret, 360);
  }
  return apiRequest<GstReturn>(`/finance/gst/returns/${encodeURIComponent(id)}/filing`, {
    method: "POST",
    body: input,
  });
}

/* ---------------------------------------------------------------------------- e-Way bills */

export type EwayStatusFilter = EwayBillWithInvoice["bill"]["status"] | "all";

export interface ListEwayBillsParams {
  status?: EwayStatusFilter;
  invoiceId?: string;
  page?: number;
  size?: number;
}

export async function listEwayBills(params: ListEwayBillsParams = {}): Promise<Page<EwayBillWithInvoice>> {
  const { status = "all", invoiceId, page = 1, size = DEFAULT_PAGE_SIZE } = params;
  if (USE_FIXTURES) {
    const filtered = EWAY_BILLS.filter(
      (row) =>
        (status === "all" || row.bill.status === status) &&
        (invoiceId === undefined || row.bill.invoice_id === invoiceId),
    );
    return settle(paginate(filtered, page, size));
  }
  const query = buildQuery({
    status: status !== "all" ? status : undefined,
    invoice_id: invoiceId,
    page,
    size,
  });
  return apiRequest<Page<EwayBillWithInvoice>>(`/finance/eway-bills${query}`);
}

export async function getEwayState(invoiceId: string): Promise<EwayState> {
  if (USE_FIXTURES) {
    const row = EWAY_BILLS.find((r) => r.bill.invoice_id === invoiceId);
    return settle(
      row
        ? {
            required: true,
            status: row.bill.status,
            eway_bill_no: row.bill.eway_bill_no,
            valid_from: row.bill.valid_from,
            valid_until: row.bill.valid_until,
            terminal: row.bill.status === "failed",
            cancellable_until: null,
            remedy: row.bill.remedy,
            attempts: 1,
          }
        : { required: false, status: null, eway_bill_no: null, valid_from: null, valid_until: null, terminal: false, cancellable_until: null, remedy: null, attempts: 0 },
    );
  }
  return apiRequest<EwayState>(`/finance/invoices/${encodeURIComponent(invoiceId)}/eway`);
}

/* --------------------------------------------------------------------- withholding: TDS / TCS */

export interface ListWithholdingsParams {
  period?: string;
  code?: WithholdingCode | "all";
  invoiceId?: string;
  page?: number;
  size?: number;
}

export async function listWithholdings(
  params: ListWithholdingsParams = {},
): Promise<Page<WithholdingWithInvoice>> {
  const { period, code = "all", invoiceId, page = 1, size = DEFAULT_PAGE_SIZE } = params;
  if (USE_FIXTURES) {
    const filtered = WITHHOLDINGS.filter(
      (row) =>
        (period === undefined || period === "" || row.withholding.period === period) &&
        (code === "all" || row.withholding.code === code) &&
        (invoiceId === undefined || row.withholding.invoice_id === invoiceId),
    );
    return settle(paginate(filtered, page, size));
  }
  const query = buildQuery({ period, code: code !== "all" ? code : undefined, invoice_id: invoiceId, page, size });
  return apiRequest<Page<WithholdingWithInvoice>>(`/finance/gst/withholdings${query}`);
}

export async function getWithholdingSummary(period: string, code?: WithholdingCode): Promise<WithholdingSummary> {
  if (USE_FIXTURES) {
    return settle({
      period,
      code: code ?? null,
      direction: null,
      rows: [],
      document_count: 0,
      total_basis_minor: 0,
      total_amount_minor: 0,
      total_cgst_minor: 0,
      total_sgst_minor: 0,
      total_igst_minor: 0,
    });
  }
  const query = buildQuery({ period, code });
  return apiRequest<WithholdingSummary>(`/finance/gst/withholdings/summary${query}`);
}

export async function listWithholdingRates(): Promise<WithholdingRate[]> {
  if (USE_FIXTURES) return settle(WITHHOLDING_RATES);
  return apiRequest<WithholdingRate[]>("/finance/gst/withholding-rates");
}

/* ------------------------------------------------------------------------------ payments */

export interface ListPaymentsParams {
  page?: number;
  size?: number;
}

export async function listPayments(params: ListPaymentsParams = {}): Promise<Page<Payment>> {
  const { page = 1, size = DEFAULT_PAGE_SIZE } = params;
  if (USE_FIXTURES) return settle(paginate(PAYMENTS, page, size));
  return apiRequest<Page<Payment>>(`/finance/payments${buildQuery({ page, size })}`);
}

let fixturePaymentSeq = PAYMENTS.length + 1;

/**
 * Record a payment received and allocate it to invoices — `POST /finance/payments`. Append-only:
 * there is no edit, delete or "unapply". Settling an invoice in full moves it to `paid` in the
 * same transaction server-side; fixture mode mirrors that so the screen behaves the same offline.
 */
export async function recordPayment(input: PaymentCreate): Promise<Payment> {
  if (USE_FIXTURES) {
    const allocations = input.allocations.map((a) => {
      const invoice = INVOICES.find((inv) => inv.id === a.invoice_id);
      if (!invoice) throw new Error("That invoice could not be found.");
      if (a.amount_minor > outstandingMinor(invoice)) {
        throw new ApiError(422, "Allocation exceeds the outstanding amount.", {
          detail: "This allocation is more than the invoice's outstanding amount.",
          errors: [{ field: "allocations", code: "exceeds_outstanding", message: "This allocation is more than the invoice's outstanding amount." }],
        });
      }
      invoice.amount_paid_minor += a.amount_minor;
      if (invoice.amount_paid_minor >= invoice.total_minor) invoice.status = "paid";
      return { invoice_id: invoice.id, invoice_number: invoice.number, amount_minor: a.amount_minor };
    });
    const payment: Payment = {
      id: `fixture-pmt-${fixturePaymentSeq++}`,
      reference: input.reference,
      customer_id: input.customer_id,
      received_date: input.received_date,
      method: input.method,
      amount_minor: allocations.reduce((sum, a) => sum + a.amount_minor, 0),
      allocations,
      note: input.note ?? null,
    };
    PAYMENTS.unshift(payment);
    return settle(payment, 360);
  }
  return apiRequest<Payment>("/finance/payments", { method: "POST", body: input });
}

/* ----------------------------------------------------------------------------- overview */

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
          sample: true,
        },
      ],
      cashflow: [...CASHFLOW],
      aging: agingBuckets(INVOICES, TODAY),
      attention: needsAttention(INVOICES),
      cashflow_sample: true,
    };
    return settle(summary);
  }
  return apiRequest<OverviewSummary>(`/finance/overview`);
}

/* ----------------------------------------------------------------------------- customers */

export type CustomerSort = "receivables_desc" | "overdue_desc" | "name_asc";

export interface ListCustomersParams {
  search?: string;
  sort?: CustomerSort;
  page?: number;
  size?: number;
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
  const { search = "", sort = "receivables_desc", page = 1, size = DEFAULT_PAGE_SIZE } = params;
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
    return settle(paginate(rows, page, size));
  }

  const query = buildQuery({ sort, page, size, q: search.trim() || undefined });
  return apiRequest<Page<CustomerSummary>>(`/finance/customers${query}`);
}

export async function getCustomer(id: string): Promise<Customer> {
  if (USE_FIXTURES) {
    const customer = FIXTURE_CUSTOMERS.get(id);
    if (!customer) throw new Error(`Customer ${id} was not found.`);
    return settle(customer);
  }
  return apiRequest<Customer>(`/finance/customers/${encodeURIComponent(id)}`);
}

export async function createCustomer(input: CustomerCreate): Promise<Customer> {
  if (USE_FIXTURES) return settle(addFixtureCustomer(input), 320);
  return apiRequest<Customer>("/finance/customers", { method: "POST", body: input });
}

export async function updateCustomer(id: string, patch: CustomerUpdate): Promise<Customer> {
  if (USE_FIXTURES) {
    const updated = updateFixtureCustomer(id, patch);
    if (!updated) throw new Error(`Customer ${id} was not found.`);
    return settle(updated, 320);
  }
  return apiRequest<Customer>(`/finance/customers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/**
 * Archive a customer — `DELETE /finance/customers/{id}`, soft (`deleted_at`). Refused with 409
 * while any invoice or payment still names this customer: a document that names a party is
 * evidence, and evidence is never reassigned.
 */
export async function archiveCustomer(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const referenced =
      INVOICES.some((inv) => inv.customer_id === id) || PAYMENTS.some((p) => p.customer_id === id);
    if (referenced) {
      throw new ApiError(409, "Customer is referenced by a document.", {
        detail: "This customer is named on at least one invoice or payment and cannot be archived.",
      });
    }
    removeFixtureCustomer(id);
    await settle(undefined, 280);
    return;
  }
  await apiRequest<void>(`/finance/customers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* ----------------------------------------------------------------------------- purchases */

export interface ListPurchasesParams {
  search?: string;
  page?: number;
  size?: number;
}

export async function listPurchases(params: ListPurchasesParams = {}): Promise<Page<PurchaseDocument>> {
  const { search = "", page = 1, size = DEFAULT_PAGE_SIZE } = params;
  if (USE_FIXTURES) {
    const q = search.trim().toLowerCase();
    const filtered = PURCHASES.filter(
      (p) =>
        q === "" ||
        p.supplier_name.toLowerCase().includes(q) ||
        p.supplier_gstin.toLowerCase().includes(q) ||
        p.number.toLowerCase().includes(q),
    );
    return settle(paginate(filtered, page, size));
  }
  const query = buildQuery({ q: search.trim() || undefined, page, size });
  return apiRequest<Page<PurchaseDocument>>(`/finance/purchases${query}`);
}

let fixturePurchaseSeq = PURCHASES.length + 1;

export async function recordPurchase(input: PurchaseDocumentCreate): Promise<PurchaseDocument> {
  if (USE_FIXTURES) {
    const split = estimateSplit(input.taxable_minor, input.tax_rate_pct, input.supplier_gstin.slice(0, 2));
    const purchase: PurchaseDocument = {
      id: `fixture-pur-${fixturePurchaseSeq++}`,
      supplier_gstin: input.supplier_gstin,
      supplier_name: input.supplier_name ?? "",
      number: input.number,
      document_type: input.document_type ?? "invoice",
      document_date: input.document_date,
      currency: input.currency ?? "INR",
      taxable_minor: input.taxable_minor,
      tax_rate_pct: input.tax_rate_pct,
      cgst_minor: split.cgst_minor,
      sgst_minor: split.sgst_minor,
      igst_minor: split.igst_minor,
      total_minor: input.taxable_minor + split.tax_minor,
      notes: input.notes ?? null,
    };
    PURCHASES.unshift(purchase);
    return settle(purchase, 320);
  }
  return apiRequest<PurchaseDocument>("/finance/purchases", { method: "POST", body: input });
}

/* ---------------------------------------------------------------------------------- ledger */

export async function getLedger(): Promise<LedgerReport> {
  if (USE_FIXTURES) {
    return settle({
      period_label: "FY 2026–27 · to date",
      as_of: TODAY,
      accounts: [...LEDGER_ACCOUNTS],
      sample: true,
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

export { MAX_PAGE_SIZE };
