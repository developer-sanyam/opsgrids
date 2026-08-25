/**
 * The two formatters the design system itself needs. Product formatting — money, dates,
 * SLA clocks, domain labels — stays in the app that owns those rules; only what a shared
 * component renders lives here.
 */

/** Initials for an avatar fallback: "Sofia Marchetti" -> "SM". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** A signed percentage for a delta pill: `4.2` -> "+4.2%", `-1.35` -> "−1.4%" (true minus). */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}
