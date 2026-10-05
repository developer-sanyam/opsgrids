/**
 * Per-client pricing fixture (the owner's "no catalogue prices, price is agreed per client" call).
 * Composes each organization's per-product pricing picture from the existing subscription and
 * catalog fixtures, and keeps a small in-memory store so a fixtures-mode "Set price" mutates what
 * the next read sees — the same round trip the live gateway gives, without a live gateway.
 *
 * Deliberately independent of `SUBSCRIPTIONS`: setting a fixture price updates this store's own
 * copy of the subscription's `plan_version_id`, not the shared subscriptions fixture, so the
 * Subscriptions and Account-detail subscription cards keep showing the originally seeded amount.
 * That is a fixtures-only simplification — the live projection updates the subscription's pinned
 * price for real.
 */

import type {
  BillingInterval,
  OrganizationPricing,
  PricingEffect,
  PricingHistoryEntry,
  ProductCurrentPrice,
  ProductPricing,
  ProductPublicPrice,
  SetPriceRequest,
  SetPriceResult,
} from "@/api/types";
import { PRODUCTS, planVersionById } from "./catalog";
import type { FixtureSubscription } from "./orgs";
import { orgDirectory, subscriptionsForOrg } from "./orgs";

function toContractInterval(interval: "monthly" | "yearly"): BillingInterval {
  return interval === "yearly" ? "year" : "month";
}

/** The plan each product's public list price is quoted from — what a similarly-sized, non-bespoke
 *  customer is on today. One flat, published, non-restricted version per product. */
const REFERENCE_PLAN_VERSION: Record<string, string> = {
  hrms: "pv_hrms_growth_v3",
  crm: "pv_crm_growth_v2",
  auctions: "pv_auctions_scale_v3",
  procure: "pv_procure_growth_v2",
  trading: "pv_trading_growth_v4",
  suite: "pv_suite_duo_v1",
};

function publicPriceFor(productKey: string): ProductPublicPrice | null {
  const versionId = REFERENCE_PLAN_VERSION[productKey];
  const version = versionId ? planVersionById(versionId) : undefined;
  if (!version) return null;
  const flat = version.prices.find((p) => p.kind === "flat") ?? version.prices[0];
  if (!flat) return null;
  return {
    plan_version_id: version.plan_version_id,
    amount_minor: flat.amount_minor,
    currency: flat.currency,
    billing_interval: toContractInterval(version.billing_interval),
  };
}

/** A couple of hand-authored, multi-entry histories so "newest first" has more than one row to
 *  prove itself on. Every other subscribed product falls back to `fallbackHistory` below. */
const SEEDED_HISTORY: Record<string, PricingHistoryEntry[]> = {
  // Amounts mirror the real `amount_minor` on the matching `PLAN_VERSIONS` entry in `catalog.ts`
  // (whose `flat(id, whole)` takes *rupees*, not paise) — kept in sync rather than recomputed, so a
  // typo here is caught by `pricing.test.ts` rather than only by reading the two files side by side.
  "org_a1f09c:trading": [
    {
      plan_version_id: "pv_trading_bespoke_northwind_v1",
      amount_minor: 42_000_000, // ₹4,20,000
      currency: "INR",
      billing_interval: "month",
      created_at: "2025-12-09T10:15:00Z",
      created_by_email_masked: "p***a@opsgrids.com",
      reason: "Negotiated enterprise rate — 3-year commit",
    },
    {
      plan_version_id: "pv_trading_enterprise_v2",
      amount_minor: 28_000_000, // ₹2,80,000
      currency: "INR",
      billing_interval: "month",
      created_at: "2024-02-11T09:00:00Z",
      created_by_email_masked: "r***i@opsgrids.com",
      reason: "Initial list price applied at signup",
    },
  ],
  "org_e5b440:trading": [
    {
      plan_version_id: "pv_trading_bespoke_deccan_v1",
      amount_minor: 28_000_000, // ₹2,80,000
      currency: "INR",
      billing_interval: "month",
      created_at: "2026-06-25T11:40:00Z",
      created_by_email_masked: "p***a@opsgrids.com",
      reason: "Matched a competitor's renewal offer to retain the account",
    },
  ],
};

/** A bespoke subscription necessarily came from a "set price" action, so it always carries at
 *  least one history row even when nothing was hand-authored above. A public, list-priced
 *  subscription that nobody has ever touched keeps an empty history — the empty state is real. */
function fallbackHistory(sub: FixtureSubscription | undefined): PricingHistoryEntry[] {
  if (!sub || !sub.is_bespoke) return [];
  const version = planVersionById(sub.plan_version_id);
  return [
    {
      plan_version_id: sub.plan_version_id,
      amount_minor: sub.amount_minor,
      currency: sub.currency,
      billing_interval: toContractInterval(version?.billing_interval ?? "monthly"),
      created_at: sub.created_at,
      created_by_email_masked: "p***a@opsgrids.com",
      reason: "Bespoke price set at contract signing",
    },
  ];
}

function currentPriceFor(sub: FixtureSubscription | undefined): ProductCurrentPrice | null {
  if (!sub) return null;
  const version = planVersionById(sub.plan_version_id);
  return {
    plan_version_id: sub.plan_version_id,
    is_bespoke: sub.is_bespoke,
    amount_minor: sub.amount_minor,
    currency: sub.currency,
    billing_interval: toContractInterval(version?.billing_interval ?? "monthly"),
  };
}

/** The fixtures-mode stand-in for the subscription service's `trial_max_ends_at`: its default cap
 *  of 90 days from the trial's start (ADR-0030). The live console never computes this — it shows
 *  what the subscription service states. */
export const FIXTURE_TRIAL_CAP_DAYS = 90;

export function fixtureTrialCap(trialStart: string): string {
  return new Date(Date.parse(trialStart) + FIXTURE_TRIAL_CAP_DAYS * 86_400_000).toISOString();
}

function baseProductPricing(organizationId: string, productKey: string, productName: string): ProductPricing {
  const sub = subscriptionsForOrg(organizationId).find((s) => s.product_key === productKey);
  const key = `${organizationId}:${productKey}`;
  return {
    product_key: productKey,
    product_name: productName,
    subscription: sub
      ? {
          id: sub.subscription_id,
          state: sub.state,
          plan_version_id: sub.plan_version_id,
          plan_name: planVersionById(sub.plan_version_id)?.plan_name ?? null,
          trial_ends_at: sub.trial_ends_at,
          current_period_end: sub.current_period_end,
          pending_plan_version_id: null,
          pending_plan_name: null,
          cancel_at: null,
          entitled_products: planVersionById(sub.plan_version_id)?.bundled_products ?? [productKey],
          current_period_start: sub.created_at,
          trial_max_ends_at: sub.state === "trial" ? fixtureTrialCap(sub.created_at) : null,
        }
      : null,
    current_price: currentPriceFor(sub),
    public_price: publicPriceFor(productKey),
    history: SEEDED_HISTORY[key] ?? fallbackHistory(sub),
  };
}

function buildOrganizationPricing(organizationId: string): OrganizationPricing | null {
  const org = orgDirectory(organizationId);
  if (!org) return null;
  return {
    organization_id: organizationId,
    products: PRODUCTS.map((p) => baseProductPricing(organizationId, p.product_key, p.name)),
  };
}

/** The mutable fixtures-mode store — composed lazily on first read, then mutated in place by
 *  `applyFixturePrice` so the next read reflects the change. */
const PRICING_STORE = new Map<string, OrganizationPricing>();

function ensurePricing(organizationId: string): OrganizationPricing | null {
  let entry = PRICING_STORE.get(organizationId);
  if (!entry) {
    const built = buildOrganizationPricing(organizationId);
    if (!built) return null;
    entry = built;
    PRICING_STORE.set(organizationId, entry);
  }
  return entry;
}

/** `GET .../pricing` in fixtures mode — a defensive copy, so nothing outside this module can mutate
 *  the store by editing the object a caller was handed. */
export function pricingForOrg(organizationId: string): OrganizationPricing | null {
  const entry = ensurePricing(organizationId);
  if (!entry) return null;
  return {
    organization_id: entry.organization_id,
    products: entry.products.map((p) => ({ ...p, history: [...p.history] })),
  };
}

let versionCounter = 0;

/**
 * `POST .../pricing` in fixtures mode: clones the product's reference plan into a new pinned
 * version, records the history row, and — mirroring what `apply_to_subscription` asks for — either
 * pins the org's subscription to it now or leaves it for later. The `now` / `next_period` /
 * `not_subscribed` split here is a fixtures approximation of the real rule (which the live
 * console-service decides), not a claim about it.
 */
export function applyFixturePrice(organizationId: string, request: SetPriceRequest): SetPriceResult {
  const entry = ensurePricing(organizationId);
  if (!entry) throw new Error(`No organization ${organizationId}`);
  const product = entry.products.find((p) => p.product_key === request.product_key);
  if (!product) throw new Error(`No product ${request.product_key} for ${organizationId}`);

  versionCounter += 1;
  const planVersionId = `pv_bespoke_${organizationId.replace("org_", "")}_${request.product_key}_${versionCounter}`;

  const historyEntry: PricingHistoryEntry = {
    plan_version_id: planVersionId,
    amount_minor: request.amount_minor,
    currency: request.currency,
    billing_interval: request.billing_interval,
    created_at: new Date().toISOString(),
    created_by_email_masked: "p***a@opsgrids.com",
    reason: request.reason,
  };

  const hasSubscription = product.subscription !== null;
  const effective: PricingEffect = !hasSubscription
    ? "not_subscribed"
    : request.apply_to_subscription
      ? "now"
      : "next_period";

  product.history = [historyEntry, ...product.history];
  product.current_price = {
    plan_version_id: planVersionId,
    is_bespoke: true,
    amount_minor: request.amount_minor,
    currency: request.currency,
    billing_interval: request.billing_interval,
  };
  if (hasSubscription && request.apply_to_subscription && product.subscription) {
    product.subscription = { ...product.subscription, plan_version_id: planVersionId };
  }

  return {
    plan_version_id: planVersionId,
    applied_to_subscription_id:
      hasSubscription && request.apply_to_subscription ? (product.subscription?.id ?? null) : null,
    effective,
  };
}

/**
 * Edit one product's live pricing entry in place — how the plan-switch fixtures (`planChange.ts`)
 * make a switch visible to the next read, the same round trip the live gateway gives. Returns
 * false when the organization or the product has no entry to edit.
 */
export function editProductPricing(
  organizationId: string,
  productKey: string,
  edit: (product: ProductPricing) => void,
): boolean {
  const entry = ensurePricing(organizationId);
  const product = entry?.products.find((p) => p.product_key === productKey);
  if (!product) return false;
  edit(product);
  return true;
}

/** Test-only: fixtures modules are singletons for the app's lifetime, but a test suite needs each
 *  test to start from the seeded state rather than the last test's mutations. */
export function resetPricingFixtures(): void {
  PRICING_STORE.clear();
  versionCounter = 0;
}
