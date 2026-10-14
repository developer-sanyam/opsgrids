/**
 * Formatting helpers for the Operations product. Money is carried everywhere as **integer minor
 * units** (paise for INR) with an ISO currency, per the platform's money rule — never a float.
 * These are the only place minor units become a display string.
 */

import { formatInstant, formatMoney as formatMajorMoney, toMajor } from "@opsgrids/ui";

const SYMBOLS: Record<string, string> = {
  INR: "₹",
  USD: "$",
  EUR: "€",
  GBP: "£",
};

function symbolFor(currency: string): string {
  return SYMBOLS[currency] ?? `${currency} `;
}

/**
 * Full money: `1245000000` paise -> "₹1,24,50,000". Indian digit grouping via `en-IN`, no minor
 * digits (procurement figures are whole-rupee at this altitude). `minor` is integer minor units.
 */
export function formatMoney(minor: number, currency = "INR"): string {
  return formatMajorMoney(minor, currency);
}

/**
 * Money with the paise: "₹12,450.75". Used on a line item where the exact figure matters.
 */
export function formatMoneyPrecise(minor: number, currency = "INR"): string {
  return formatMajorMoney(minor, currency, { precise: true });
}

/**
 * Compact money for a headline KPI: "₹1.25 Cr", "₹4.2 L", "₹9.8K". Deterministic Indian scale
 * (crore / lakh / thousand) rather than locale-dependent compact notation.
 */
export function formatMoneyCompact(minor: number, currency = "INR"): string {
  const major = toMajor(minor, currency);
  const sym = symbolFor(currency);
  const abs = Math.abs(major);
  const sign = major < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}${sym}${(abs / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sign}${sym}${(abs / 1e5).toFixed(2)} L`;
  if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
  return `${sign}${sym}${abs.toFixed(0)}`;
}

/** A grouped integer: 12500 -> "12,500". */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

/** "2026-08-24" -> "24 Aug 2026". Returns "—" for a missing date. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** "24 Aug 2026 to open-ended", "Always" when neither end is set — an effective-date window. */
export function formatDateRange(from: string | null, to: string | null): string {
  if (!from && !to) return "Always";
  return `${from ? formatDate(from) : "Any time"} to ${to ? formatDate(to) : "open-ended"}`;
}

/** Date and time together, the viewer's own local zone — for a timestamp where *when today* it
 *  happened matters (a sync attempt, a health check), not just which day. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return formatInstant(iso, null, { locations: {}, organizationZone: zone }, "datetime").value;
}



/**
 * Whole-day difference between an ISO date and a reference "today", both read in UTC so a stored
 * calendar date compares cleanly. Positive = in the future, negative = overdue.
 */
export function daysBetween(iso: string, today: string): number {
  const a = Date.parse(`${iso}T00:00:00Z`);
  const b = Date.parse(`${today}T00:00:00Z`);
  return Math.round((a - b) / 86_400_000);
}

/** "in 3 days" / "today" / "2 days overdue" for a delivery date relative to today. */
export function relativeDue(iso: string | null, today: string): string {
  if (!iso) return "—";
  const d = daysBetween(iso, today);
  if (d === 0) return "Due today";
  if (d === 1) return "Due tomorrow";
  if (d > 1) return `In ${d} days`;
  if (d === -1) return "1 day overdue";
  return `${Math.abs(d)} days overdue`;
}
