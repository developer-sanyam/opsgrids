/**
 * Formatting helpers. Dates render in UTC so a stored calendar date shows as-is regardless of the
 * viewer's zone; money is handled as integer minor units (paise) and never as a float.
 */

/** "2026-03-15" -> "15 Mar 2026". */
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

/** A short relative label for a due date measured from `now`: "Today", "in 3d", "5d ago". */
export function relativeDue(due: string, now: string): string {
  const d = daysBetween(now, due);
  if (d === 0) return "Today";
  if (d === 1) return "Tomorrow";
  if (d === -1) return "Yesterday";
  if (d > 0) return d < 14 ? `in ${d}d` : `in ${Math.round(d / 7)}w`;
  const past = Math.abs(d);
  return past < 14 ? `${past}d ago` : `${Math.round(past / 7)}w ago`;
}

/** Initials for an avatar fallback: "Aarav Sharma" -> "AS". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Format a delta like 4.2 -> "+4.2%" / -1.4 -> "−1.4%". */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

/** 0.842 fraction -> "84%". */
export function formatPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/**
 * Integer minor units (paise) -> a compact Indian-notation amount: "₹1.24 Cr", "₹45.0 L",
 * "₹80.0 K". Money is never stored or computed as a float; this only formats for display.
 */
export function formatMoney(minorUnits: number): string {
  const rupees = minorUnits / 100;
  if (rupees >= 1_00_00_000) return `₹${(rupees / 1_00_00_000).toFixed(2)} Cr`;
  if (rupees >= 1_00_000) return `₹${(rupees / 1_00_000).toFixed(1)} L`;
  if (rupees >= 1_000) return `₹${(rupees / 1_000).toFixed(1)} K`;
  return `₹${rupees.toFixed(0)}`;
}
