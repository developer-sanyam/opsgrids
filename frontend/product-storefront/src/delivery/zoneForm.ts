/**
 * The zone form's state and its validation, as pure functions.
 *
 * Separated from the component because this is the part worth testing and the part that has to
 * **mirror the server's rules without replacing them** — ADR-0010 §3's refusal of `SLOTTED` until
 * slice 2 is enforced by the writer, and repeated here so the merchant is told before they fill in
 * a form the server will reject.
 *
 * Money never leaves this file as anything but integer minor units.
 */

import { minorToInput, parseMinor } from "./money";
import type { PromiseKind, ZoneCreate, ZoneMode, ZoneOut } from "./types";

/** The form's own shape: every field a string, because that is what an input holds. */
export interface ZoneFormValues {
  code: string;
  name: string;
  mode: ZoneMode;
  promiseKind: PromiseKind;
  promiseMinutes: string;
  promiseOffsetDays: string;
  promiseCutoffTime: string;
  deliveryFee: string;
  freeDeliveryAbove: string;
  minOrder: string;
  isActive: boolean;
  buyerNote: string;
}

export type ZoneField = keyof ZoneFormValues;

export type ZoneErrors = Partial<Record<ZoneField, string>>;

export const EMPTY_ZONE: ZoneFormValues = {
  code: "",
  name: "",
  mode: "PROMISED",
  promiseKind: "DURATION",
  promiseMinutes: "",
  promiseOffsetDays: "",
  promiseCutoffTime: "",
  deliveryFee: "",
  freeDeliveryAbove: "",
  minOrder: "",
  isActive: true,
  buyerNote: "",
};

/** `ZONE-CODE`: the merchant's handle, and what the CSV import's `zone_code` column names. */
const CODE_PATTERN = /^[A-Z0-9][A-Z0-9-]{0,31}$/;
const TIME_PATTERN = /^([01]\d|2[0-3]):([0-5]\d)$/;

function parseCount(raw: string): number | null | "bad" {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return "bad";
  const value = Number(trimmed);
  return Number.isSafeInteger(value) ? value : "bad";
}

/**
 * Validate the whole form.
 *
 * Every message names the rule rather than the failure: "Zone codes are letters, numbers and
 * hyphens" tells the merchant what to type, where "Invalid code" makes them guess.
 */
export function validateZone(values: ZoneFormValues): ZoneErrors {
  const errors: ZoneErrors = {};

  if (values.name.trim() === "") errors.name = "Give the zone a name buyers would recognise.";
  const code = values.code.trim().toUpperCase();
  if (code === "") errors.code = "A zone needs a code — it is what the CSV import refers to.";
  else if (!CODE_PATTERN.test(code)) {
    errors.code = "Letters, numbers and hyphens only, starting with a letter or number.";
  }

  if (values.mode === "SLOTTED") {
    errors.mode = "Slot booking arrives with the cart. Pick Promised or Not served for now.";
  }

  if (values.mode === "PROMISED") {
    if (values.promiseKind === "DURATION") {
      const minutes = parseCount(values.promiseMinutes);
      if (minutes === "bad" || minutes === null) {
        errors.promiseMinutes = "How many minutes? A whole number, like 30.";
      } else if (minutes < 1 || minutes > 1440) {
        errors.promiseMinutes = "Between 1 minute and 24 hours. Past a day, use a delivery date.";
      }
    } else {
      const days = parseCount(values.promiseOffsetDays);
      if (days === "bad" || days === null) {
        errors.promiseOffsetDays = "How many days after the order? A whole number — 0 means same day.";
      } else if (days > 60) {
        errors.promiseOffsetDays = "60 days at most.";
      }
      const cutoff = values.promiseCutoffTime.trim();
      if (cutoff !== "" && !TIME_PATTERN.test(cutoff)) {
        errors.promiseCutoffTime = "A 24-hour time, like 16:00.";
      }
    }
  }

  const fee = parseMinor(values.deliveryFee);
  if (!fee.ok) errors.deliveryFee = fee.message;
  const free = parseMinor(values.freeDeliveryAbove);
  if (!free.ok) errors.freeDeliveryAbove = free.message;
  const minimum = parseMinor(values.minOrder);
  if (!minimum.ok) errors.minOrder = minimum.message;

  if (fee.ok && free.ok && fee.minor !== null && free.minor !== null && free.minor <= fee.minor) {
    errors.freeDeliveryAbove = "The free-delivery threshold has to be above the fee itself.";
  }

  if (values.buyerNote.length > 200) {
    errors.buyerNote = "200 characters at most.";
  }

  return errors;
}

/**
 * Turn a validated form into the request body (`ZoneCreate`, which `ZonePatch` mirrors).
 *
 * Two rules, both from the published schema rather than from taste:
 *
 * - **Fields that do not apply to the chosen mode go as `null`**, not left at whatever the merchant
 *   typed before they changed their mind. A `PROMISED`/`DURATION` zone carrying a stale
 *   `promise_offset_days` is a row that contradicts itself.
 * - **An empty money box is `0`, not `null` or omitted.** `delivery_fee_minor` and
 *   `min_order_minor` are non-nullable integers, and on a `PATCH` an omitted field means "leave it
 *   alone" — so omitting a fee the merchant just cleared would keep the old amount, and they would
 *   watch it keep printing on the buyer's card. Only `free_delivery_above_minor` is genuinely
 *   nullable, where `null` means "no threshold" (a threshold of `0` would ship everything free).
 */
export function toZoneInput(values: ZoneFormValues): ZoneCreate {
  const promised = values.mode === "PROMISED";
  const duration = promised && values.promiseKind === "DURATION";
  const dated = promised && values.promiseKind === "DATE_OFFSET";
  const fee = parseMinor(values.deliveryFee);
  const free = parseMinor(values.freeDeliveryAbove);
  const minimum = parseMinor(values.minOrder);
  const cutoff = values.promiseCutoffTime.trim();
  const note = values.buyerNote.trim();

  return {
    code: values.code.trim().toUpperCase(),
    name: values.name.trim(),
    mode: values.mode,
    promise_kind: promised ? values.promiseKind : null,
    promise_minutes: duration ? Number(values.promiseMinutes.trim()) : null,
    promise_offset_days: dated ? Number(values.promiseOffsetDays.trim()) : null,
    promise_cutoff_time: dated && cutoff !== "" ? cutoff : null,
    delivery_fee_minor: fee.ok ? (fee.minor ?? 0) : 0,
    free_delivery_above_minor: free.ok ? free.minor : null,
    min_order_minor: minimum.ok ? (minimum.minor ?? 0) : 0,
    is_active: values.isActive,
    buyer_note: note === "" ? null : note,
  };
}

/** Load an existing zone into the form. Money comes back out as rupees, only here. */
export function fromZone(zone: ZoneOut): ZoneFormValues {
  return {
    code: zone.code,
    name: zone.name,
    mode: zone.mode,
    promiseKind: zone.promise_kind ?? "DURATION",
    promiseMinutes: zone.promise_minutes === null ? "" : String(zone.promise_minutes),
    promiseOffsetDays: zone.promise_offset_days === null ? "" : String(zone.promise_offset_days),
    promiseCutoffTime: zone.promise_cutoff_time ?? "",
    deliveryFee: minorToInput(zone.delivery_fee_minor),
    freeDeliveryAbove: minorToInput(zone.free_delivery_above_minor),
    minOrder: minorToInput(zone.min_order_minor),
    isActive: zone.is_active,
    buyerNote: zone.buyer_note ?? "",
  };
}
