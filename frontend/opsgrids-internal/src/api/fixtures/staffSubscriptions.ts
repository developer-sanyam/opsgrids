/**
 * Staff Add product and Extend trial fixtures (ADR-0030), kept honest to the live rules:
 *
 * * Add product offers only products the organization has **no row for, in any state**; refuses a
 *   currency other than its live subscriptions' (`org_currency_mismatch`), an existing row
 *   (`already_subscribed`) and a paid start that was not acknowledged (`charge_not_acknowledged`);
 *   a trial is the default.
 * * Extend trial is **absolute** and guarded by the end the operator saw (`state_changed`); refuses
 *   a subscription that is not trialling (`not_trial`), an end that is not later and in the future
 *   (`trial_not_extended`), and one past the cap (`trial_cap`).
 * * An `Idempotency-Key` that repeats replays the recorded result; the same key for a different
 *   request is `idempotency_key_reused`.
 *
 * Both mutate the fixtures pricing store, so the next read shows what changed — the round trip the
 * live gateway gives.
 */

import { ApiError } from "@/api/http";
import type {
  AddProductOption,
  AddProductOptions,
  AddProductRequest,
  AddProductResult,
  ExtendTrialRequest,
  ExtendTrialResult,
} from "@/api/types";
import { PLAN_VERSIONS, PRODUCTS, planVersionById } from "./catalog";
import type { FixturePlanVersion } from "./catalog";
import { editProductPricing, fixtureTrialCap, pricingForOrg } from "./pricing";

const DAY_MS = 86_400_000;
/** The fixtures-mode stand-in for a version's trial length when it names none. */
const FIXTURE_DEFAULT_TRIAL_DAYS = 14;
const LIVE_STATES = new Set(["trial", "active", "past_due", "suspended"]);

function refusal(status: number, code: string, message: string): ApiError {
  return new ApiError(status, message, {
    status,
    detail: message,
    request_id: "fixture-request",
    errors: [{ code, message }],
  });
}

function interval(version: FixturePlanVersion): "month" | "year" {
  return version.billing_interval === "yearly" ? "year" : "month";
}

function recurringMinor(version: FixturePlanVersion): number {
  return version.prices
    .filter((p) => p.kind === "flat" || p.kind === "per_seat")
    .reduce((sum, p) => sum + p.amount_minor, 0);
}

function pricing(organizationId: string) {
  const entry = pricingForOrg(organizationId);
  if (!entry) throw refusal(404, "not_found", "No such organization");
  return entry;
}

function liveCurrencies(organizationId: string): string[] {
  const currencies = pricing(organizationId)
    .products.filter((p) => p.subscription !== null && LIVE_STATES.has(p.subscription.state))
    .map((p) => p.current_price?.currency ?? "INR");
  return [...new Set(currencies)].sort();
}

function entitledNow(organizationId: string): Set<string> {
  return new Set(
    pricing(organizationId)
      .products.filter((p) => p.subscription !== null && LIVE_STATES.has(p.subscription.state))
      .flatMap((p) => p.subscription?.entitled_products ?? []),
  );
}

function optionFor(organizationId: string, version: FixturePlanVersion): AddProductOption {
  const currencies = liveCurrencies(organizationId);
  const wrongCurrency = currencies.length > 0 && !currencies.includes(version.currency);
  const entitled = entitledNow(organizationId);
  return {
    plan_id: version.plan_id,
    plan_name: version.plan_name,
    plan_version_id: version.plan_version_id,
    version_no: version.version_no,
    billing_interval: interval(version),
    currency: version.currency,
    is_bespoke: version.restricted_to_org_id !== null,
    recurring_minor: recurringMinor(version),
    prices: version.prices.map((p) => ({
      kind: p.kind,
      amount_minor: p.amount_minor,
      currency: p.currency,
      unit: p.unit,
      unit_price_micros: p.unit_price_micros,
      meter_key: p.meter_key,
    })),
    bundled_products: version.bundled_products ?? null,
    trial_days: null,
    offers_trial: true,
    overlapping_products: (version.bundled_products ?? []).filter((m) => entitled.has(m)),
    blocked_code: wrongCurrency ? "org_currency_mismatch" : null,
    blocked_reason: wrongCurrency
      ? `this organization is billed in ${currencies.join(", ")}; this plan is in ${version.currency}`
      : null,
  };
}

/** `GET .../add-product-options` in fixtures mode. */
export function addProductOptionsForOrg(organizationId: string): AddProductOptions {
  const entry = pricing(organizationId);
  const existing = entry.products
    .filter((p) => p.subscription !== null)
    .map((p) => ({ product_key: p.product_key, state: p.subscription?.state ?? "unknown" }))
    .sort((a, b) => a.product_key.localeCompare(b.product_key));
  const taken = new Set(existing.map((e) => e.product_key));
  const currencies = liveCurrencies(organizationId);
  const products = PRODUCTS.filter((p) => !taken.has(p.product_key))
    .map((p) => ({
      product_key: p.product_key,
      product_name: p.name,
      versions: PLAN_VERSIONS.filter(
        (v) =>
          v.product_key === p.product_key &&
          v.state === "published" &&
          (v.restricted_to_org_id === null || v.restricted_to_org_id === organizationId),
      ).map((v) => optionFor(organizationId, v)),
    }))
    .filter((p) => p.versions.length > 0);
  return {
    organization_id: organizationId,
    currency: currencies.length === 1 ? (currencies[0] ?? null) : null,
    existing,
    products,
  };
}

const RECORDED_ADDS = new Map<string, { fingerprint: string; result: AddProductResult }>();
const RECORDED_EXTENSIONS = new Map<string, { fingerprint: string; result: ExtendTrialResult }>();
let subscriptionCounter = 0;

function replayOrNull<T>(
  store: Map<string, { fingerprint: string; result: T }>,
  key: string,
  fingerprint: string,
): T | null {
  const recorded = store.get(key);
  if (!recorded) return null;
  if (recorded.fingerprint !== fingerprint) {
    throw refusal(409, "idempotency_key_reused", "Idempotency-Key was already used for a different request");
  }
  return recorded.result;
}

/** `POST .../subscriptions` in fixtures mode. */
export function applyFixtureAddProduct(
  organizationId: string,
  request: AddProductRequest,
  idempotencyKey: string,
): AddProductResult {
  if (!request.trial && !request.acknowledge_immediate_charge) {
    throw refusal(
      422,
      "charge_not_acknowledged",
      "A paid subscription starts billing now: its first period is claimed for payment at once, and no payment mandate is checked first. Confirm that before adding it, or add it as a trial.",
    );
  }
  const key = `${organizationId}:${idempotencyKey}`;
  const fingerprint = JSON.stringify(["add_product", request.product_key, request.plan_version_id, request.trial]);
  const replay = replayOrNull(RECORDED_ADDS, key, fingerprint);
  if (replay) return { ...replay, replayed: true };

  const entry = pricing(organizationId);
  const product = entry.products.find((p) => p.product_key === request.product_key);
  if (product?.subscription) {
    throw refusal(
      409,
      "already_subscribed",
      `this organization already has a subscription for '${request.product_key}' (${product.subscription.state})`,
    );
  }
  const version = planVersionById(request.plan_version_id);
  if (
    !version ||
    version.product_key !== request.product_key ||
    (version.restricted_to_org_id !== null && version.restricted_to_org_id !== organizationId)
  ) {
    throw refusal(404, "not_found", "No such plan version");
  }
  const currencies = liveCurrencies(organizationId);
  if (currencies.length > 0 && !currencies.includes(version.currency)) {
    throw refusal(
      422,
      "org_currency_mismatch",
      `this organization's subscriptions are billed in ${currencies.join(", ")}; a ${version.currency} plan cannot be added beside them`,
    );
  }

  const now = new Date();
  subscriptionCounter += 1;
  const subscriptionId = `sub_added_${organizationId.replace("org_", "")}_${subscriptionCounter}`;
  const start = now.toISOString();
  const periodEnd = new Date(now.getTime() + (interval(version) === "year" ? 365 : 30) * DAY_MS).toISOString();
  const trialEnds = request.trial ? new Date(now.getTime() + FIXTURE_DEFAULT_TRIAL_DAYS * DAY_MS).toISOString() : null;
  const members = version.bundled_products ?? [request.product_key];

  editProductPricing(organizationId, request.product_key, (p) => {
    p.subscription = {
      id: subscriptionId,
      state: request.trial ? "trial" : "active",
      plan_version_id: version.plan_version_id,
      plan_name: version.plan_name,
      trial_ends_at: trialEnds,
      current_period_end: periodEnd,
      pending_plan_version_id: null,
      pending_plan_name: null,
      cancel_at: null,
      entitled_products: members,
      current_period_start: start,
      trial_max_ends_at: request.trial ? fixtureTrialCap(start) : null,
    };
    p.current_price = {
      plan_version_id: version.plan_version_id,
      is_bespoke: version.restricted_to_org_id !== null,
      amount_minor: recurringMinor(version),
      currency: version.currency,
      billing_interval: interval(version),
    };
  });

  const result: AddProductResult = {
    subscription_id: subscriptionId,
    product_key: request.product_key,
    state: request.trial ? "trial" : "active",
    plan_version_id: version.plan_version_id,
    currency: version.currency,
    trial_ends_at: trialEnds,
    current_period_start: start,
    current_period_end: periodEnd,
    entitled_products: members,
    charge_claimed: !request.trial,
    replayed: false,
  };
  RECORDED_ADDS.set(key, { fingerprint, result });
  return result;
}

/** `POST .../subscriptions/{product_key}/extend-trial` in fixtures mode. */
export function applyFixtureExtendTrial(
  organizationId: string,
  productKey: string,
  request: ExtendTrialRequest,
  idempotencyKey: string,
): ExtendTrialResult {
  const key = `${organizationId}:${idempotencyKey}`;
  const fingerprint = JSON.stringify([
    "extend_trial",
    productKey,
    Date.parse(request.new_trial_ends_at),
    Date.parse(request.expected_trial_ends_at),
  ]);
  const replay = replayOrNull(RECORDED_EXTENSIONS, key, fingerprint);
  if (replay) return { ...replay, replayed: true };

  const product = pricing(organizationId).products.find((p) => p.product_key === productKey);
  const sub = product?.subscription;
  if (!sub) throw refusal(404, "no_subscription", `This organization has no subscription for '${productKey}'`);
  const current = sub.trial_ends_at ? Date.parse(sub.trial_ends_at) : null;
  if (current !== Date.parse(request.expected_trial_ends_at)) {
    throw refusal(409, "state_changed", "this subscription's trial end changed since you looked at it; refresh and review it again");
  }
  if (sub.state !== "trial") {
    throw refusal(422, "not_trial", `a ${sub.state} subscription is not trialling; only a trial can be extended`);
  }
  const target = Date.parse(request.new_trial_ends_at);
  if ((current !== null && target <= current) || target <= Date.now()) {
    throw refusal(422, "trial_not_extended", "the new trial end must be later than the trial already ends, and in the future");
  }
  if (sub.trial_max_ends_at && target > Date.parse(sub.trial_max_ends_at)) {
    throw refusal(422, "trial_cap", `a trial may run at most 90 days in total; this one can end no later than ${sub.trial_max_ends_at}`);
  }

  const newEnd = new Date(target).toISOString();
  editProductPricing(organizationId, productKey, (p) => {
    if (p.subscription) p.subscription = { ...p.subscription, trial_ends_at: newEnd };
  });
  const result: ExtendTrialResult = {
    subscription_id: sub.id,
    product_key: productKey,
    state: "trial",
    trial_ends_at: newEnd,
    previous_trial_ends_at: sub.trial_ends_at,
    replayed: false,
  };
  RECORDED_EXTENSIONS.set(key, { fingerprint, result });
  return result;
}

/** Test-only: each test starts from no recorded keys. */
export function resetStaffSubscriptionFixtures(): void {
  RECORDED_ADDS.clear();
  RECORDED_EXTENSIONS.clear();
}
