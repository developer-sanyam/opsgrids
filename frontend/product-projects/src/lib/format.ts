/**
 * Formatting and clock helpers. Dates render in UTC so a stored calendar date shows as-is
 * regardless of the viewer's zone; money is handled as integer minor units and never as a float.
 */

/** The real "today", as an ISO calendar date. One `now()` for the whole app — injectable with a
 *  fixed `Date` in a test, and never the frozen fixture constant in live mode. */
export function today(date: Date = new Date()): string {
  return date.toISOString().slice(0, 10);
}

/** "2026-03-15" -> "15 Mar 2026". `null` (an undated milestone, a task with no due date) reads
 *  as an em dash rather than "Invalid Date". */
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

/** "2026-03-15" -> "15 Mar". */
export function formatDateShort(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  });
}

/** Whole days from `from` to `to` (negative when `to` is in the past). */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}

/** A short relative label for a due date measured from `now`: "Today", "in 3d", "5d ago". `null`
 *  (no due date set) reads as its own label rather than a bogus day count. */
export function relativeDue(due: string | null, now: string): string {
  if (!due) return "No due date";
  const d = daysBetween(now, due);
  if (d === 0) return "Today";
  if (d === 1) return "Tomorrow";
  if (d === -1) return "Yesterday";
  if (d > 0) return d < 14 ? `in ${d}d` : `in ${Math.round(d / 7)}w`;
  const past = Math.abs(d);
  return past < 14 ? `${past}d ago` : `${Math.round(past / 7)}w ago`;
}

/** 0.842 fraction -> "84%". */
export function formatPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/**
 * Integer minor units -> a localized currency amount, honouring the record's own `currency` —
 * never a hardcoded symbol. Money is never stored or computed as a float; this only formats for
 * display. Falls back to a plain "CODE amount" for a currency `Intl` doesn't recognize rather
 * than throwing mid-render.
 */
export function formatMoney(minorUnits: number, currency: string): string {
  const amount = minorUnits / 100;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}
