/**
 * Formatting helpers for the Operations product. Money is carried everywhere as **integer minor
 * units** (paise for INR) with an ISO currency, per the platform's money rule — never a float.
 * These are the only place minor units become a display string.
 */

import {
  formatCalendarDate as formatDate,
  formatInstant,
  formatMoney as formatMajorMoney,
} from "@opsgrids/ui";

export { formatDate };

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

/** Compact money for a headline KPI: crore and lakh for INR ("₹1.3 Cr", "₹4.2 L"), whole rupees below. */
export function formatMoneyCompact(minor: number, currency = "INR"): string {
  return formatMajorMoney(minor, currency, { compact: true });
}

/** A grouped integer: 12500 -> "12,500". */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

/** "2026-08-24" -> "24 Aug 2026". Returns "—" for a missing date. */

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
