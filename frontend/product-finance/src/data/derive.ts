/**
 * Pure rollups over invoices and payments — no I/O, no React — so every derived surface (KPIs,
 * AR aging, the receivable totals) comes from the same source and stays consistent. Amounts stay
 * in integer minor units throughout; nothing here divides by 100.
 */
import type { AgingBucket, CurrencyRevenueHistory, Invoice, MonthlyRevenuePoint, Payment } from "@/api/types";
import type { Minor } from "@/lib/money";
import { daysBetween } from "@/lib/format";

/** What is still owed on a document: its total less whatever has been paid, never below zero. */
export function outstandingMinor(inv: Invoice): Minor {
  return Math.max(0, inv.total_minor - inv.amount_paid_minor);
}

/** An invoice that still carries a receivable — a real invoice (not a credit note) still open. */
export function isOpenInvoice(inv: Invoice): boolean {
  return (
    inv.document_type === "invoice" &&
    (inv.status === "sent" || inv.status === "overdue")
  );
}

/** Days past due as of `today`; 0 or negative means not yet due. */
export function daysOverdue(inv: Invoice, today: string): number {
  return daysBetween(inv.due_date, today);
}

/** Total receivable outstanding across all open invoices. */
export function totalOutstanding(invoices: readonly Invoice[]): Minor {
  return invoices.filter(isOpenInvoice).reduce((sum, inv) => sum + outstandingMinor(inv), 0);
}

/** Outstanding on invoices already past their due date. */
export function totalOverdue(invoices: readonly Invoice[]): Minor {
  return invoices
    .filter((inv) => isOpenInvoice(inv) && inv.status === "overdue")
    .reduce((sum, inv) => sum + outstandingMinor(inv), 0);
}

/**
 * Net revenue recognised in a given calendar month (`YYYY-MM`): issued invoices less credit
 * notes. Drafts and voids are not recognised. Credit-note totals are negative, so a plain sum of
 * signed totals gives the net figure.
 */
export function netRevenueForMonth(invoices: readonly Invoice[], month: string): Minor {
  return invoices
    .filter(
      (inv) =>
        inv.status !== "draft" &&
        inv.status !== "void" &&
        inv.issued_date.startsWith(month),
    )
    .reduce((sum, inv) => sum + inv.total_minor, 0);
}

const AGING_BANDS: { key: string; label: string; min: number; max: number }[] = [
  { key: "current", label: "Current", min: -Infinity, max: 0 },
  { key: "d1_30", label: "1–30 days", min: 1, max: 30 },
  { key: "d31_60", label: "31–60 days", min: 31, max: 60 },
  { key: "d61_90", label: "61–90 days", min: 61, max: 90 },
  { key: "d90p", label: "90+ days", min: 91, max: Infinity },
];

/** AR aging: open receivable split by how far past due each invoice is. */
export function agingBuckets(invoices: readonly Invoice[], today: string): AgingBucket[] {
  return AGING_BANDS.map((band) => {
    const rows = invoices.filter((inv) => {
      if (!isOpenInvoice(inv)) return false;
      const d = daysOverdue(inv, today);
      return d >= band.min && d <= band.max;
    });
    return {
      key: band.key,
      label: band.label,
      amount_minor: rows.reduce((sum, inv) => sum + outstandingMinor(inv), 0),
      count: rows.length,
    };
  });
}

/** Overdue invoices, largest outstanding first — the "needs attention" queue. */
export function needsAttention(invoices: readonly Invoice[]): Invoice[] {
  return invoices
    .filter((inv) => isOpenInvoice(inv) && inv.status === "overdue")
    .sort((a, b) => outstandingMinor(b) - outstandingMinor(a));
}

/* -------------------------------------------------------- revenue history (fixture mode) -----
 * Mirrors `services/finance/app/domain.revenue_history` exactly, so the offline demo
 * (`VITE_USE_FIXTURES`) shows the same real arithmetic the live gateway computes — nothing here is
 * an authored trend constant. Live mode never calls this: it reads `revenue_history` straight off
 * `GET /finance/overview`.
 */

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
] as const;

/** `"2026-08-24"` → `"2026-08"`. */
function monthKey(dateIso: string): string {
  return dateIso.slice(0, 7);
}

/** `("2026-08", -1)` → `"2026-07"`; `("2026-01", -1)` → `"2025-12"`. */
function addMonths(month: string, delta: number): string {
  const [yearPart = "1970", monthPart = "01"] = month.split("-");
  const zeroBased = Number(yearPart) * 12 + (Number(monthPart) - 1) + delta;
  const year = Math.floor(zeroBased / 12);
  const monthIndex = ((zeroBased % 12) + 12) % 12;
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}`;
}

/** `"2026-08"` → `"August 2026"`. */
function monthLabel(month: string): string {
  const [yearPart = "", monthPart = "1"] = month.split("-");
  return `${MONTH_NAMES[Number(monthPart) - 1] ?? month} ${yearPart}`;
}

/**
 * Percentage change vs. a prior period, to one decimal place. `null` — never `0` — when the prior
 * period has nothing to compare against: a percentage computed against zero is a divide-by-zero
 * dressed up as a number, and an invented delta is worse than no delta.
 */
export function deltaPct(current: Minor, previous: Minor): number | null {
  if (previous === 0) return null;
  return Math.round(((current - previous) / Math.abs(previous)) * 1000) / 10;
}

/** What a document has paid by the end of `monthEnd` (`"YYYY-MM-31"`, a sentinel past any real
 *  calendar day, so string comparison against ISO dates is all that's needed). */
function paidByMonthEnd(payments: readonly Payment[], invoiceId: string, monthEnd: string): Minor {
  return payments
    .filter((p) => p.received_date <= monthEnd)
    .reduce(
      (sum, p) =>
        sum + p.allocations.filter((a) => a.invoice_id === invoiceId).reduce((s, a) => s + a.amount_minor, 0),
      0,
    );
}

/**
 * The real 12-month revenue/receivables history behind the Overview KPIs and its monthly chart:
 * net revenue issued, cash received and receivables outstanding, per month, all derived from this
 * tenant's own invoices and payments. This organization issues only in INR, so there is exactly
 * one currency's worth to return — `services/finance/app/domain.revenue_history` returns one
 * `CurrencyRevenueHistory` per currency actually issued or paid in.
 */
export function revenueHistory(
  invoices: readonly Invoice[],
  payments: readonly Payment[],
  anchorMonth: string,
  span = 12,
): CurrencyRevenueHistory {
  const months: string[] = [];
  for (let i = span - 1; i >= 0; i -= 1) months.push(addMonths(anchorMonth, -i));
  const previousMonth = addMonths(anchorMonth, -1);

  const live = invoices.filter((inv) => inv.status !== "draft" && inv.status !== "void");

  const points: MonthlyRevenuePoint[] = months.map((month) => {
    const monthEnd = `${month}-31`;
    const revenue_minor = live
      .filter((inv) => monthKey(inv.issued_date) === month)
      .reduce((sum, inv) => sum + inv.total_minor, 0);
    const received_minor = payments
      .filter((p) => monthKey(p.received_date) === month)
      .reduce((sum, p) => sum + p.allocations.reduce((s, a) => s + a.amount_minor, 0), 0);
    const outstanding_minor = live
      .filter((inv) => inv.document_type === "invoice" && inv.issued_date <= monthEnd)
      .reduce((sum, inv) => sum + Math.max(0, inv.total_minor - paidByMonthEnd(payments, inv.id, monthEnd)), 0);
    return { month, label: monthLabel(month), revenue_minor, received_minor, outstanding_minor };
  });

  const byMonth = new Map(points.map((p) => [p.month, p] as const));
  const zero: MonthlyRevenuePoint = { month: anchorMonth, label: monthLabel(anchorMonth), revenue_minor: 0, received_minor: 0, outstanding_minor: 0 };
  const current = byMonth.get(anchorMonth) ?? zero;
  const previous = byMonth.get(previousMonth);

  return {
    currency: "INR",
    revenue_current_minor: current.revenue_minor,
    revenue_previous_minor: previous?.revenue_minor ?? 0,
    revenue_delta_pct: deltaPct(current.revenue_minor, previous?.revenue_minor ?? 0),
    received_current_minor: current.received_minor,
    received_previous_minor: previous?.received_minor ?? 0,
    received_delta_pct: deltaPct(current.received_minor, previous?.received_minor ?? 0),
    outstanding_current_minor: current.outstanding_minor,
    outstanding_previous_minor: previous?.outstanding_minor ?? 0,
    outstanding_delta_pct: deltaPct(current.outstanding_minor, previous?.outstanding_minor ?? 0),
    months: points,
  };
}
