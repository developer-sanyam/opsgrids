/**
 * Staff plan-switching fixtures (ADR-0029): the picker, the preview and the confirm, kept honest to
 * the live rules rather than to what is convenient to demo.
 *
 * * a **trial** switches now, free;
 * * a **paying** subscription switches at its **next billing period** — there is no "now" for
 *   staff to ask for — and an **equal-price** change applies now at no charge;
 * * the **expected-state guard** refuses a stale confirm with `409 state_changed`;
 * * an `Idempotency-Key` that repeats replays the recorded result instead of changing twice.
 *
 * The comparison below (flat plus per-seat at quantity one, usage ignored) is a fixtures-mode
 * stand-in for subscription's own `_list_price_minor` rule; the live console never computes
 * direction — it shows what subscription's preview says.
 */

import { ApiError } from "@/api/http";
import type {
  ExpectedState,
  PlanChangeDirection,
  PlanChangePreview,
  PlanChangePreviewRequest,
  PlanChangeRequest,
  PlanChangeResult,
  PlanOption,
  PlanOptions,
  ProductPricing,
} from "@/api/types";
import { planVersionById, PLAN_VERSIONS } from "./catalog";
import type { FixturePlanVersion } from "./catalog";
import { editProductPricing, pricingForOrg } from "./pricing";

function refusal(status: number, code: string, message: string): ApiError {
  return new ApiError(status, message, {
    status,
    detail: message,
    request_id: "fixture-request",
    errors: [{ code, message }],
  });
}

function recurringMinor(version: FixturePlanVersion): number {
  return version.prices
    .filter((p) => p.kind === "flat" || p.kind === "per_seat")
    .reduce((sum, p) => sum + p.amount_minor, 0);
}

function contractInterval(version: FixturePlanVersion): "month" | "year" {
  return version.billing_interval === "yearly" ? "year" : "month";
}

function liveProduct(organizationId: string, productKey: string): ProductPricing {
  const pricing = pricingForOrg(organizationId);
  if (!pricing) throw new Error(`No organization ${organizationId}`);
  const product = pricing.products.find((p) => p.product_key === productKey);
  if (!product?.subscription) {
    throw refusal(404, "no_subscription", `This organization has no subscription for '${productKey}'`);
  }
  return product;
}

function expectedOf(product: ProductPricing): ExpectedState {
  const sub = product.subscription;
  if (!sub) throw new Error("no subscription");
  return {
    state: sub.state,
    plan_version_id: sub.plan_version_id,
    pending_plan_version_id: sub.pending_plan_version_id,
  };
}

/** `GET .../plan-options` in fixtures mode. */
export function planOptionsForOrg(organizationId: string, productKey: string): PlanOptions {
  const product = liveProduct(organizationId, productKey);
  const sub = product.subscription;
  if (!sub) throw new Error("no subscription");
  const currency = product.current_price?.currency ?? "INR";
  const interval = product.current_price?.billing_interval ?? null;

  const options: PlanOption[] = PLAN_VERSIONS.filter(
    (v) =>
      v.product_key === productKey &&
      v.state === "published" &&
      (v.restricted_to_org_id === null || v.restricted_to_org_id === organizationId),
  ).map((v) => {
    const wrongCurrency = v.currency !== currency;
    const wrongCadence = interval !== null && contractInterval(v) !== interval;
    return {
      plan_id: v.plan_id,
      plan_name: v.plan_name,
      plan_version_id: v.plan_version_id,
      version_no: v.version_no,
      billing_interval: contractInterval(v),
      currency: v.currency,
      is_bespoke: v.restricted_to_org_id !== null,
      recurring_minor: recurringMinor(v),
      prices: v.prices.map((p) => ({
        kind: p.kind,
        amount_minor: p.amount_minor,
        currency: p.currency,
        unit: p.unit,
        unit_price_micros: p.unit_price_micros,
        meter_key: p.meter_key,
      })),
      bundled_products: v.bundled_products ?? null,
      trial_days: null,
      is_current: v.plan_version_id === sub.plan_version_id,
      is_pending: v.plan_version_id === sub.pending_plan_version_id,
      blocked_code: wrongCurrency ? "currency" : wrongCadence ? "cadence" : null,
      blocked_reason: wrongCurrency
        ? `this subscription is billed in ${currency}; this plan is in ${v.currency}`
        : wrongCadence
          ? `this subscription renews every ${interval}; a ${contractInterval(v)} plan cannot replace it mid-subscription`
          : null,
    };
  });

  return {
    organization_id: organizationId,
    product_key: productKey,
    subscription: {
      id: sub.id,
      state: sub.state,
      plan_version_id: sub.plan_version_id,
      plan_name: sub.plan_name,
      pending_plan_version_id: sub.pending_plan_version_id,
      pending_plan_name: sub.pending_plan_name,
      currency,
      billing_interval: interval,
      trial_ends_at: sub.trial_ends_at,
      current_period_end: sub.current_period_end,
      change_blocked_code: sub.state === "trial" || sub.state === "active" ? null : "state",
    },
    options,
  };
}

function targetFor(organizationId: string, planVersionId: string): FixturePlanVersion {
  const version = planVersionById(planVersionId);
  // Another organization's bespoke version is the same 404 as one that does not exist.
  if (!version || (version.restricted_to_org_id !== null && version.restricted_to_org_id !== organizationId)) {
    throw refusal(404, "not_found", "No such plan version");
  }
  return version;
}

function refused(expected: ExpectedState, code: string, detail: string): PlanChangePreview {
  return {
    expected,
    action: "switch",
    direction: null,
    effective: null,
    effective_at: null,
    would_clear_pending: false,
    entitled_added: [],
    entitled_removed: [],
    usage_priced: false,
    usage_closed_now: false,
    refusal_code: code,
    refusal_detail: detail,
  };
}

function hasUsagePrice(version: FixturePlanVersion | undefined): boolean {
  return version?.prices.some((p) => p.kind === "usage") ?? false;
}

/** `POST .../plan-change:preview` in fixtures mode. */
export function previewFixturePlanChange(
  organizationId: string,
  productKey: string,
  request: PlanChangePreviewRequest,
): PlanChangePreview {
  const product = liveProduct(organizationId, productKey);
  const sub = product.subscription;
  if (!sub) throw new Error("no subscription");
  const expected = expectedOf(product);
  const target = targetFor(organizationId, request.plan_version_id);

  if (sub.state !== "trial" && sub.state !== "active") {
    return refused(
      expected,
      "state",
      `a ${sub.state} subscription cannot change plan; only a trial or an active one can`,
    );
  }
  if (target.plan_version_id === sub.plan_version_id) {
    if (sub.pending_plan_version_id === null) {
      return refused(expected, "already_pinned", "the subscription is already pinned to that plan version");
    }
    return {
      ...refused(expected, "", ""),
      action: "withdraw_scheduled",
      effective: "now",
      would_clear_pending: true,
      refusal_code: null,
      refusal_detail: null,
    };
  }
  if (target.product_key !== productKey) {
    return refused(expected, "product_mismatch", `plan version belongs to product '${target.product_key}', not '${productKey}'`);
  }
  const currency = product.current_price?.currency ?? "INR";
  if (target.currency !== currency) {
    return refused(expected, "currency", `cannot change currency from ${currency} to ${target.currency}`);
  }
  const interval = product.current_price?.billing_interval ?? null;
  if (interval !== null && contractInterval(target) !== interval) {
    return refused(expected, "cadence", `cannot change from a ${interval} plan to a ${contractInterval(target)} one mid-subscription`);
  }
  // The live console re-reads what catalog offers this organization and refuses anything else.
  const offered = planOptionsForOrg(organizationId, productKey).options;
  if (!offered.some((o) => o.plan_version_id === target.plan_version_id)) {
    return refused(
      expected,
      "not_offerable",
      "that plan version is no longer on offer for this organization: a newer version has replaced it, or its plan was retired. Choose again from the plans on offer now.",
    );
  }

  const current = planVersionById(sub.plan_version_id);
  const currentMinor = current ? recurringMinor(current) : (product.current_price?.amount_minor ?? 0);
  const targetMinor = recurringMinor(target);
  const direction: PlanChangeDirection =
    targetMinor > currentMinor ? "upgrade" : targetMinor < currentMinor ? "downgrade" : "equal";

  // Trial: now, free. Paying: the next period — unless nothing is owed (equal), which is now.
  const paying = sub.state === "active";
  const deferred = paying && direction !== "equal";
  const members = target.bundled_products ?? [productKey];
  return {
    expected,
    action: "switch",
    direction,
    effective: deferred ? "next_period" : "now",
    effective_at: deferred ? sub.current_period_end : null,
    would_clear_pending: sub.pending_plan_version_id !== null,
    entitled_added: members.filter((m) => !sub.entitled_products.includes(m)),
    entitled_removed: sub.entitled_products.filter((m) => !members.includes(m)),
    usage_priced: hasUsagePrice(target) || hasUsagePrice(current),
    // Applying now on a paying subscription closes the usage so far at the current plan's rates.
    usage_closed_now: !deferred && paying && hasUsagePrice(current),
    refusal_code: null,
    refusal_detail: null,
  };
}

/** Recorded results by `Idempotency-Key`, so a repeat replays instead of changing twice. */
const RECORDED = new Map<string, { fingerprint: string; result: PlanChangeResult }>();

/** `POST .../plan-change` in fixtures mode. */
export function applyFixturePlanChange(
  organizationId: string,
  productKey: string,
  request: PlanChangeRequest,
  idempotencyKey: string,
): PlanChangeResult {
  const key = `${organizationId}:${idempotencyKey}`;
  const fingerprint = JSON.stringify([productKey, request.plan_version_id, request.expected]);
  const recorded = RECORDED.get(key);
  if (recorded) {
    if (recorded.fingerprint !== fingerprint) {
      throw refusal(409, "idempotency_key_reused", "Idempotency-Key was already used for a different request");
    }
    return { ...recorded.result, replayed: true };
  }

  const product = liveProduct(organizationId, productKey);
  const sub = product.subscription;
  if (!sub) throw new Error("no subscription");
  const live = expectedOf(product);
  if (
    live.state !== request.expected.state ||
    live.plan_version_id !== request.expected.plan_version_id ||
    live.pending_plan_version_id !== request.expected.pending_plan_version_id
  ) {
    throw refusal(409, "state_changed", "this subscription changed since you looked at it; refresh and review it again");
  }
  const preview = previewFixturePlanChange(organizationId, productKey, {
    plan_version_id: request.plan_version_id,
    timing: request.timing,
  });
  if (preview.refusal_code) {
    throw refusal(422, preview.refusal_code, preview.refusal_detail ?? "refused");
  }
  const target = targetFor(organizationId, request.plan_version_id);

  editProductPricing(organizationId, productKey, (p) => {
    if (!p.subscription) return;
    if (preview.action === "withdraw_scheduled") {
      p.subscription = { ...p.subscription, pending_plan_version_id: null, pending_plan_name: null };
    } else if (preview.effective === "next_period") {
      p.subscription = {
        ...p.subscription,
        pending_plan_version_id: target.plan_version_id,
        pending_plan_name: target.plan_name,
      };
    } else {
      p.subscription = {
        ...p.subscription,
        plan_version_id: target.plan_version_id,
        plan_name: target.plan_name,
        pending_plan_version_id: null,
        pending_plan_name: null,
        entitled_products: target.bundled_products ?? [productKey],
      };
      p.current_price = {
        plan_version_id: target.plan_version_id,
        is_bespoke: target.restricted_to_org_id !== null,
        amount_minor: recurringMinor(target),
        currency: target.currency,
        billing_interval: contractInterval(target),
      };
    }
  });

  const after = expectedOf(liveProduct(organizationId, productKey));
  const scheduled = after.pending_plan_version_id === target.plan_version_id;
  const result: PlanChangeResult = {
    effective: scheduled ? "next_period" : "now",
    state: after.state,
    plan_version_id: after.plan_version_id,
    pending_plan_version_id: after.pending_plan_version_id,
    effective_at: scheduled ? sub.current_period_end : null,
    replayed: false,
  };
  RECORDED.set(key, { fingerprint, result });
  return result;
}

/** Test-only: each test starts from the seeded state, not the last test's recorded keys. */
export function resetPlanChangeFixtures(): void {
  RECORDED.clear();
}
