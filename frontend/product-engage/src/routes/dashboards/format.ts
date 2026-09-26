import type { Money } from "@/api/analytics";

const PERCENT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

/**
 * Integer minor units as the currency's own notation — `₹3,842.50` — for display only. The divisor
 * comes from the currency's real fraction digits rather than an assumed two, so a zero-decimal
 * currency is not read a hundred times too small. Nothing is added or averaged in floating point:
 * each figure is one integer the service already folded.
 */
export function formatMoney(money: Money): string {
  const format = new Intl.NumberFormat("en-IN", { style: "currency", currency: money.currency });
  const digits = format.resolvedOptions().maximumFractionDigits ?? 2;
  return format.format(money.amount_minor / 10 ** digits);
}

/** `part` as a share of `whole`, one decimal — or `null` when there is no whole to be a share of. */
export function percentOf(part: number, whole: number): string | null {
  if (whole <= 0) return null;
  return `${PERCENT.format((part / whole) * 100)}%`;
}

/** `92.8% of sent`, or nothing at all when nothing was sent: a rate over zero is not zero. */
export function shareCaption(part: number, whole: number, of: string): string | undefined {
  const share = percentOf(part, whole);
  return share === null ? undefined : `${share} of ${of}`;
}

const DEVICE_LABELS: Record<string, string> = {
  desktop: "Desktop",
  mobile: "Mobile",
  tablet: "Tablet",
  bot: "Automated (bots and prefetch)",
  unknown: "Unknown",
};

export function deviceLabel(device: string): string {
  return DEVICE_LABELS[device] ?? `${device.charAt(0).toUpperCase()}${device.slice(1)}`;
}

/**
 * A link as a person reads it: host and path, with the query string and fragment left off. A
 * tracked destination often carries an identifier or a token in its query, and a dashboard is
 * shown over shoulders.
 */
export function linkLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${parsed.host}${path}`;
  } catch {
    return url;
  }
}
