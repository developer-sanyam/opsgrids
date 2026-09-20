/**
 * The words.
 *
 * The server sends facts — a minute count, a resolved ISO date, an enum, an integer minor amount —
 * and never prose (ADR-0010 §3). Every sentence a merchant reads is composed here, in one place,
 * so the admin and the buyer's chip cannot drift into describing the same answer differently.
 */

import { formatMinorExact } from "./money";
import type { FeesOut, PromiseOut, UnservedReason, ZoneMode, ZoneOut } from "./types";

export const MODE_LABEL: Record<ZoneMode, string> = {
  PROMISED: "Promised",
  SLOTTED: "Slot booking",
  UNSERVED: "Not served",
};

/**
 * The one sentence a merchant needs about each mode, written for the person choosing between them
 * rather than for the enum.
 */
export const MODE_DESCRIPTION: Record<ZoneMode, string> = {
  PROMISED: "The buyer is told when it will arrive and picks nothing.",
  SLOTTED:
    "Slot booking arrives with the cart. The buyer will pick a delivery window; nothing can offer one until there is a cart to book against.",
  UNSERVED: "The buyer is told no, deliberately. Use this for an area you have mapped and excluded.",
};

/**
 * The badge beside the disabled `SLOTTED` option. The *reason* is the first sentence of
 * `MODE_DESCRIPTION.SLOTTED`, not this — measured at 390px, a tag carrying the whole sentence took
 * 60% of the row and broke "Slot booking" across two lines. The badge says it is unavailable; the
 * description says why (ADR-0010 §3, and the consequence it records: "a merchant will see the mode
 * named in the UI and be told it is coming").
 *
 * A merchant who cannot see the mode asks for it; a greyed control with a reason answers them
 * without a support ticket.
 */
export const SLOTTED_UNAVAILABLE = "Not available yet";

export const REASON_LABEL: Record<UnservedReason, string> = {
  no_mapping: "Not mapped to any zone yet",
  excluded: "Mapped and deliberately excluded",
  zone_inactive: "The zone it belongs to is switched off",
};

/**
 * The same, but safe against a reason this build has never heard of.
 *
 * The buyer-facing half types `reason` as a plain `string` on the wire, and a fourth reason added
 * server-side would otherwise render as a blank line under "No — you don't deliver here" — the
 * worst possible place to silently lose a sentence. Showing the raw value is ugly and true.
 */
export function reasonLabel(reason: string): string {
  const known: Record<string, string> = REASON_LABEL;
  return known[reason] ?? reason;
}

/** "in about 30 minutes" / "in about 1 hour 15 minutes". */
export function durationSentence(minutes: number): string {
  if (minutes < 60) return `in about ${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const hourPart = hours === 1 ? "1 hour" : `${hours} hours`;
  if (rest === 0) return `in about ${hourPart}`;
  return `in about ${hourPart} ${rest} minutes`;
}

/**
 * "Thursday 25 September" from an ISO `YYYY-MM-DD`.
 *
 * Formatted in UTC from the parts, *not* in the browser's zone. The server has already resolved
 * the date in the store's timezone (ADR-0010 §3: a buyer in Singapore looking at a Mumbai store
 * must see the Mumbai answer); re-interpreting it locally is precisely how that gets undone — a
 * `YYYY-MM-DD` parsed as UTC midnight and rendered in `America/Los_Angeles` is the day before.
 */
export function formatPromiseDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, year, month, day] = match;
  if (year === undefined || month === undefined || day === undefined) return iso;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(date);
}

/** "18 Sep" — the column form, same UTC-from-parts discipline as `formatPromiseDate`. */
export function formatShortDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, year, month, day] = match;
  if (year === undefined || month === undefined || day === undefined) return iso;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(date);
}

/** The mode, safe against an enum value this build has never heard of (the schema types it `string`). */
export function modeLabel(mode: string): string {
  const known: Record<string, string> = MODE_LABEL;
  return known[mode] ?? mode;
}

/** The promise a zone makes, as the buyer would hear it, or `null` when it makes none. */
export function zonePromiseSentence(zone: ZoneOut): string | null {
  if (zone.mode !== "PROMISED") return null;
  if (zone.promise_kind === "DURATION" && zone.promise_minutes !== null) {
    return durationSentence(zone.promise_minutes);
  }
  if (zone.promise_kind === "DATE_OFFSET" && zone.promise_offset_days !== null) {
    const days = zone.promise_offset_days;
    const dayPart = days === 1 ? "the next day" : `${days} days later`;
    return zone.promise_cutoff_time === null
      ? `${dayPart}`
      : `${dayPart}, if ordered before ${zone.promise_cutoff_time}`;
  }
  return null;
}

/**
 * The promise in an answer, already resolved by the server. This is the sentence the tester prints.
 *
 * `PromiseOut` is a count **or** a date and never prose (ADR-0010 §3) — the arithmetic happened in
 * the store's timezone server-side, and the wording is the client's to choose.
 */
export function answerPromiseSentence(promise: PromiseOut | null): string | null {
  if (promise === null) return null;
  if (promise.kind === "DURATION" && promise.minutes !== null) {
    return durationSentence(promise.minutes);
  }
  if (promise.kind === "DATE_OFFSET" && promise.date !== null) {
    return `by ${formatPromiseDate(promise.date)}`;
  }
  return null;
}

/**
 * The two money lines from a `FeesOut`, or `null` when the answer carries no fees at all.
 *
 * `display_only` comes from the server (ADR-0010 §8: stored and displayed in slice 1, enforced in
 * slice 4). Driving the caveat off the flag rather than hard-coding it means the screen stops
 * claiming "nothing charges these" the moment the pricing engine does, with no frontend change.
 */
export function feeLines(fees: FeesOut | null): {
  fee: string | null;
  minimum: string | null;
  displayOnly: boolean;
} | null {
  if (fees === null) return null;
  const fee = feeSentence(fees.delivery_fee_minor, fees.free_delivery_above_minor, fees.currency);
  const minimum = minimumOrderSentence(fees.min_order_minor, fees.currency);
  if (fee === null && minimum === null) return null;
  return { fee, minimum, displayOnly: fees.display_only ?? true };
}

/**
 * The fee line — **what a buyer would be shown, not what anything charges.**
 *
 * ADR-0010 §8: fees and minimum order value are stored and displayed in slice 1 and enforced in
 * slice 4. There is no pricing engine, no cart and no checkout, so nothing on the platform can
 * apply these numbers to an order. Every screen that prints them says so in the label; this
 * function only composes the amounts.
 */
export function feeSentence(
  fee: number,
  freeAbove: number | null,
  currency: string,
): string | null {
  // Zero with no threshold is "no fee configured", which is not a claim worth printing. Zero *with*
  // a threshold would be a contradiction, and the threshold is dropped rather than shown.
  if (fee === 0 && freeAbove === null) return null;
  const feePart = fee === 0 ? "Free delivery" : `${formatMinorExact(fee, currency)} delivery`;
  if (freeAbove === null || fee === 0) return feePart;
  return `${feePart}, free over ${formatMinorExact(freeAbove, currency)}`;
}

export function minimumOrderSentence(minimum: number, currency: string): string | null {
  if (minimum === 0) return null;
  return `${formatMinorExact(minimum, currency)} minimum order`;
}

/**
 * The demand counter's `outcome`, in words.
 *
 * ADR-0010 §12's worked example writes `SERVED`; §3 names three unserved reasons. Which vocabulary
 * the column actually holds is not stated, so known values are labelled and anything else is shown
 * verbatim rather than mislabelled — a report that renamed an outcome it did not recognise would
 * be worse than one that admitted it.
 */
export function demandOutcomeLabel(outcome: string): string {
  const known: Record<string, string> = {
    SERVED: "Served",
    UNSERVED: "Turned away",
    NO_MAPPING: "Not mapped",
    EXCLUDED: "Excluded",
    ZONE_INACTIVE: "Zone switched off",
    no_mapping: "Not mapped",
    excluded: "Excluded",
    zone_inactive: "Zone switched off",
  };
  return known[outcome] ?? outcome;
}

/** Is this demand row a postal code the merchant turned away? The report leads with these. */
export function isUnmetDemand(outcome: string): boolean {
  return outcome.toUpperCase() !== "SERVED";
}
