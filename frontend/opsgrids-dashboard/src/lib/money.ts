/**
 * Money arrives as integer minor units with an ISO currency (ADR-0000 §8) and is formatted, never
 * computed, here. `Intl` knows the minor-unit exponent per currency, which is why this does not
 * divide by 100: INR and USD have two, JPY has none, KWD has three.
 */
export function formatMoney(amountMinor: number, currency: string, locale = "en-IN"): string {
  const digits = minorUnitDigits(currency, locale);
  const major = amountMinor / 10 ** digits;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(major);
}

/**
 * The same figure with no minor-unit digits — `₹12,48,320`, not `₹12,48,320.00` — for a KPI or a
 * dense list where the paise are noise. Never used for a figure that reconciles to a ledger.
 */
export function formatMoneyWhole(amountMinor: number, currency: string, locale = "en-IN"): string {
  const digits = minorUnitDigits(currency, locale);
  const major = amountMinor / 10 ** digits;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
    minimumFractionDigits: 0,
  }).format(major);
}

function minorUnitDigits(currency: string, locale: string): number {
  try {
    return (
      new Intl.NumberFormat(locale, { style: "currency", currency }).resolvedOptions()
        .maximumFractionDigits ?? 2
    );
  } catch {
    return 2;
  }
}

/** A date from the wire, for people. */
export function formatDate(iso: string | null, locale = "en-IN"): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric" }).format(date);
}

/** Whole days from now to `iso`, negative when past. */
export function daysUntil(iso: string | null, now = new Date()): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.ceil((then - now.getTime()) / 86_400_000);
}
