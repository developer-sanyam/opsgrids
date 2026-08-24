/** Formatting helpers shared across the console. Dates render in UTC so a stored instant shows
 * identically regardless of the operator's own timezone. */

/** "2026-03-15" or ISO instant -> "15 Mar 2026". */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** ISO instant -> "15 Mar 2026, 4:20 PM". */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

/**
 * A signed, human relative distance from `now` (default: the fixed "today" of the fixtures). Past
 * reads "3 days ago"; future reads "in 12 days". Used for last-activity and grant-expiry columns.
 */
export function relativeDays(iso: string | null, now: Date = new Date("2026-08-24T00:00:00Z")): string {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";
  const ms = then.getTime() - now.getTime();
  const days = Math.round(ms / 86_400_000);
  if (days === 0) return "today";
  const abs = Math.abs(days);
  const unit = abs === 1 ? "day" : "days";
  if (abs < 45) return days < 0 ? `${abs} ${unit} ago` : `in ${abs} ${unit}`;
  const months = Math.round(abs / 30);
  const munit = months === 1 ? "month" : "months";
  return days < 0 ? `${months} ${munit} ago` : `in ${months} ${munit}`;
}

/** Whole-number days between `iso` and now — negative if past. */
export function daysUntil(iso: string | null, now: Date = new Date("2026-08-24T00:00:00Z")): number | null {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  return Math.round((then.getTime() - now.getTime()) / 86_400_000);
}

/** 94.2 -> "94.2%". */
export function formatPercent(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

/** Format a delta like 4.2 -> "+4.2%" / -1.4 -> "−1.4%" (true minus sign). */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

/** Initials for an avatar fallback: "Northwind Metals" -> "NM". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** "1,284" — plain grouped integer for seat counts and event tallies. */
export function formatInt(value: number): string {
  return new Intl.NumberFormat("en-IN").format(value);
}
