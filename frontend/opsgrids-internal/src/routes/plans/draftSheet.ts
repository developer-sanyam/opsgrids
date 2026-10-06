/**
 * The draft-version editor's model: a version's sheet as editable text, and back to the **whole**
 * `AdminVersionCreateIn` body.
 *
 * Catalog never merges a partial sheet — a body that names only a price drops every feature,
 * including `trial_days` (admin_synth H5) — so the editor always starts from a full version and
 * always sends one: every price, every feature (typed controls for `trial_days` and
 * `bundled_products`, everything else carried over verbatim), and every seat class, re-pointed at
 * its price by **position** in the list sent, because the new prices have no ids yet.
 *
 * Money goes through `lib/minorUnits` only: a currency's own exponent, digits as text, no float.
 */

import type {
  BillingIntervalWire,
  FeatureIn,
  PlanVersionOut,
  PriceIn,
  PriceKind,
  SeatClassIn,
  SeatClassOut,
  VersionCreateRequest,
} from "@/api/catalog";
import {
  microsToInput,
  minorToInput,
  parseAmountToMinor,
  parseRateToMicros,
} from "@/lib/minorUnits";

export const TRIAL_DAYS = "trial_days";
export const BUNDLED_PRODUCTS = "bundled_products";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PRODUCT_KEY = /^[a-z][a-z0-9_-]{0,39}$/;

export interface PriceDraft {
  /** Stable React key for the row. */
  key: string;
  /** The price this row was cloned from, so a seat class can follow it to its new position. */
  sourceId: string | null;
  kind: PriceKind;
  amount: string;
  unit: string;
  rate: string;
  meterKey: string;
}

export interface SheetDraft {
  currency: string;
  billingInterval: BillingIntervalWire;
  restrictedToOrgId: string;
  prices: PriceDraft[];
  /** Empty means "not declared" (the platform default trial applies). */
  trialDays: string;
  /** Comma-separated product keys; empty means "not a bundle". */
  bundledProducts: string;
  /** Every other feature, carried over exactly as the source version declared it. */
  otherFeatures: FeatureIn[];
  /** Carried over; each still names its price by the source price's id. */
  seatClasses: SeatClassOut[];
}

let rowSequence = 0;
export function newPriceRow(kind: PriceKind = "flat"): PriceDraft {
  rowSequence += 1;
  return {
    key: `price-${rowSequence}`,
    sourceId: null,
    kind,
    amount: "",
    unit: kind === "per_seat" ? "seat" : "",
    rate: "",
    meterKey: "",
  };
}

/** The editor's starting point: a copy of `source`, or an empty INR monthly sheet. */
export function draftFromVersion(source: PlanVersionOut | null): SheetDraft {
  if (source === null) {
    return {
      currency: "INR",
      billingInterval: "month",
      restrictedToOrgId: "",
      prices: [newPriceRow()],
      trialDays: "",
      bundledProducts: "",
      otherFeatures: [],
      seatClasses: [],
    };
  }
  const trial = source.features.find((f) => f.feature_key === TRIAL_DAYS);
  const bundle = source.features.find((f) => f.feature_key === BUNDLED_PRODUCTS);
  return {
    currency: source.currency,
    billingInterval: source.billing_interval,
    restrictedToOrgId: source.restricted_to_org_id ?? "",
    prices: source.prices.map((p) => {
      rowSequence += 1;
      return {
        key: `price-${rowSequence}`,
        sourceId: p.id,
        kind: p.kind,
        amount: p.amount_minor === null ? "" : minorToInput(p.amount_minor, source.currency),
        unit: p.unit ?? "",
        rate: p.unit_price_micros === null ? "" : microsToInput(p.unit_price_micros),
        meterKey: p.meter_key ?? "",
      };
    }),
    trialDays: typeof trial?.value === "number" ? String(trial.value) : "",
    bundledProducts: Array.isArray(bundle?.value) ? bundle.value.map(String).join(", ") : "",
    otherFeatures: source.features
      .filter((f) => f.feature_key !== TRIAL_DAYS && f.feature_key !== BUNDLED_PRODUCTS)
      .map((f) => ({ feature_key: f.feature_key, value: f.value })),
    seatClasses: source.seat_classes,
  };
}

/** Field path → message, keyed the way the editor names its controls (`prices.0.amount`). */
export type SheetErrors = Record<string, string>;

export type BuildResult =
  | { ok: true; request: VersionCreateRequest }
  | { ok: false; errors: SheetErrors };

function buildPrice(row: PriceDraft, index: number, currency: string, errors: SheetErrors): PriceIn | null {
  const at = `prices.${index}`;
  if (row.kind === "usage") {
    const rate = parseRateToMicros(row.rate);
    if (!rate.ok) errors[`${at}.rate`] = rate.error;
    if (!row.meterKey.trim()) errors[`${at}.meterKey`] = "A usage price needs the meter it bills.";
    if (!rate.ok || !row.meterKey.trim()) return null;
    return {
      kind: "usage",
      currency,
      unit: row.unit.trim() || null,
      unit_price_micros: rate.value,
      meter_key: row.meterKey.trim(),
      amount_minor: null,
    };
  }
  const amount = parseAmountToMinor(row.amount, currency);
  if (!amount.ok) errors[`${at}.amount`] = amount.error;
  if (row.kind === "per_seat" && !row.unit.trim()) {
    errors[`${at}.unit`] = "A per-seat price needs a unit, such as seat.";
  }
  if (!amount.ok || (row.kind === "per_seat" && !row.unit.trim())) return null;
  return {
    kind: row.kind,
    currency,
    amount_minor: amount.value,
    unit: row.kind === "per_seat" ? row.unit.trim() : null,
    unit_price_micros: null,
    meter_key: null,
  };
}

function buildSeatClasses(draft: SheetDraft, errors: SheetErrors): SeatClassIn[] {
  return draft.seatClasses.map((sc) => {
    let priceIndex: number | null = null;
    if (sc.price_id !== null) {
      const index = draft.prices.findIndex((p) => p.sourceId === sc.price_id);
      const row = draft.prices[index];
      if (index < 0 || row === undefined) {
        errors[`seatClasses.${sc.code}`] =
          `Seat class “${sc.name}” is charged at a price you removed — keep that price or start from another version.`;
      } else if (row.kind !== "per_seat") {
        errors[`seatClasses.${sc.code}`] = `Seat class “${sc.name}” must stay charged at a per-seat price.`;
      } else {
        priceIndex = index;
      }
    }
    return {
      code: sc.code,
      name: sc.name,
      price_index: priceIndex,
      included_count: sc.included_count,
      can_write: sc.can_write,
      sort_order: sc.sort_order,
      description: sc.description,
    };
  });
}

/** Validate the draft and build the full request body, or say what is wrong, field by field. */
export function buildVersionRequest(draft: SheetDraft, reason: string): BuildResult {
  const errors: SheetErrors = {};
  const currency = draft.currency;

  if (draft.prices.length === 0) errors.prices = "Add at least one price.";
  const prices = draft.prices.map((row, index) => buildPrice(row, index, currency, errors));

  const features: FeatureIn[] = [...draft.otherFeatures];
  const trial = draft.trialDays.trim();
  if (trial !== "") {
    if (!/^\d{1,3}$/.test(trial) || Number(trial) > 365) {
      errors.trialDays = "Enter whole days from 0 to 365, or leave it empty.";
    } else {
      features.push({ feature_key: TRIAL_DAYS, value: Number(trial) });
    }
  }
  const bundle = draft.bundledProducts
    .split(",")
    .map((key) => key.trim())
    .filter((key) => key !== "");
  if (bundle.length > 0) {
    const bad = bundle.find((key) => !PRODUCT_KEY.test(key));
    if (bad !== undefined) errors.bundledProducts = `“${bad}” is not a product key.`;
    else if (new Set(bundle).size !== bundle.length) errors.bundledProducts = "A product is named twice.";
    else features.push({ feature_key: BUNDLED_PRODUCTS, value: bundle });
  }

  const org = draft.restrictedToOrgId.trim();
  if (org !== "" && !UUID.test(org)) errors.restrictedToOrgId = "Enter the organization's id (a UUID), or leave it empty.";

  const seatClasses = buildSeatClasses(draft, errors);

  const trimmedReason = reason.trim();
  if (trimmedReason.length < 3) errors.reason = "Say why, in at least 3 characters.";
  else if (trimmedReason.length > 500) errors.reason = "Keep the reason under 500 characters.";

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    request: {
      billing_interval: draft.billingInterval,
      currency,
      restricted_to_org_id: org === "" ? null : org,
      prices: prices.filter((p): p is PriceIn => p !== null),
      features,
      seat_classes: seatClasses,
      reason: trimmedReason,
    },
  };
}

/** Whether a version prices anything at exactly zero — publish then needs an explicit yes. */
export function hasZeroAmount(version: PlanVersionOut): boolean {
  return version.prices.some((p) =>
    p.kind === "usage" ? p.unit_price_micros === 0 : p.amount_minor === 0,
  );
}
