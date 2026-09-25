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

/** The same figure in short notation — `₹13.2L`, `₹97K` — for a KPI card too narrow for the
 *  grouped form. Never used where the exact figure is what the screen is for. */
export function formatMoneyCompact(amountMinor: number, currency: string, locale = "en-IN"): string {
  const digits = minorUnitDigits(currency, locale);
  const major = amountMinor / 10 ** digits;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    notation: "compact",
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  }).format(major);
}

/**
 * `formatMoneyWhole`, unless the grouped figure is long enough to risk clipping a KPI card at
 * phone width, in which case it compacts instead — the containment rule every dashboard card
 * follows (owner brief: "long numbers switch to compact money, never overflow"). The mockups'
 * own demo figures (`₹12,48,320`) are short enough to stay grouped; this only ever changes the
 * real, larger figures a growing business produces.
 */
export function formatMoneyKpi(amountMinor: number, currency: string, locale = "en-IN"): string {
  const whole = formatMoneyWhole(amountMinor, currency, locale);
  return whole.length > 12 ? formatMoneyCompact(amountMinor, currency, locale) : whole;
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

/** `Intl`'s own "short" month abbreviates September as "Sept" in some English locales and "Sep" in
 * others — a column of dates whose width depends on the reader's browser. Fixed here the same way
 * `@opsgrids/ui`'s `lib/datetime.ts` fixes it: a literal list, not CLDR's. */
const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A date from the wire, for people — `12 Sep 2026`, always a three-letter month. */
export function formatDate(iso: string | null, locale = "en-IN"): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const parts = new Intl.DateTimeFormat(locale, { day: "numeric", year: "numeric" }).formatToParts(date);
  const day = parts.find((part) => part.type === "day")?.value ?? String(date.getDate());
  const year = parts.find((part) => part.type === "year")?.value ?? String(date.getFullYear());
  const month = SHORT_MONTHS[date.getMonth()] ?? "";
  return `${day} ${month} ${year}`;
}

/** Whole days from now to `iso`, negative when past. */
export function daysUntil(iso: string | null, now = new Date()): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.ceil((then - now.getTime()) / 86_400_000);
}
