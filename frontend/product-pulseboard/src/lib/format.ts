/**
 * Formatting helpers for PulseBoard. Money is handled as integer minor units with an ISO
 * currency (ADR: no floats), so every amount that reaches the UI is an integer and a currency
 * code — never a pre-rounded float.
 */

/** "2026-08-24" -> "24 Aug 2026". */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** "2026-08-24" -> "24 Aug" (compact, for dense columns). */
export function formatDayMonth(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  });
}

/**
 * Integer minor units + ISO currency -> a whole-currency string, e.g. (18_000_000, "USD")
 * -> "$180,000". Amounts on this platform are large project budgets, so cents are dropped for
 * readability; the underlying value stays exact in minor units.
 */
export function formatMoney(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

/** A compact money label for tight tiles: (18_000_000, "USD") -> "$180K", (2_400_000_00,…) -> "$2.4M". */
export function formatMoneyCompact(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(minor / 100);
}

/** Whole hours or one decimal: 6 -> "6h", 6.5 -> "6.5h", 0 -> "0h". */
export function formatHours(hours: number): string {
  const rounded = Math.round(hours * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}h`;
}
