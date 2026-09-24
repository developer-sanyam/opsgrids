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
