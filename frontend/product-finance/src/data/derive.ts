/**
 * Pure rollups over invoices and payments — no I/O, no React — so every derived surface (KPIs,
 * AR aging, the receivable totals) comes from the same source and stays consistent. Amounts stay
 * in integer minor units throughout; nothing here divides by 100.
 */
import type { AgingBucket, Invoice } from "@/api/types";
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
