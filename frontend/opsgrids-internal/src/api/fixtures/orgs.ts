/**
 * The tenant directory fixture: sixteen customer organizations across every product, plan and
 * lifecycle state, plus the subscriptions derived from them.
 *
 * This mirrors a real split in the live system on purpose: `orgDirectory()` (name, slug, status,
 * country) is only ever read by `getOrganization()`, one id at a time — exactly like the real
 * `/admin/organizations/{id}` composite, which is the only place the console has a customer's
 * name. `buildOrgStates()` — the bulk `/admin/organizations` list — deliberately does **not**
 * expose the name, because the real endpoint doesn't either. A fixtures screen that tried to show
 * a directory name in a bulk list would silently diverge from what the live gateway can do; keeping
 * that gap here too is what caught it instead.
 */

import type { OrgStateOut, SubscriptionStateEntry } from "@/api/types";
import { planVersionById, versionMonthlyMinor } from "./catalog";

export interface FixtureSubscription {
  subscription_id: string;
  organization_id: string;
  product_key: string;
  plan_version_id: string;
  plan_name: string;
  is_bespoke: boolean;
  state: string;
  seats: number;
  amount_minor: number;
  currency: string;
  trial_ends_at: string | null;
  current_period_end: string;
  created_at: string;
  entitled_products: string[];
}

interface SubSeed {
  product_key: string;
  plan_version_id: string;
  plan_name: string;
  is_bespoke: boolean;
  state: string;
  seats: number;
  created_at: string;
}

interface OrgSeed {
  id: string;
  name: string;
  slug: string;
  country: string;
  status: string;
  locked: boolean;
  created_at: string;
  updated_at: string;
  trial_ends_at: string | null;
  subscriptions: SubSeed[];
}

const ORG_SEEDS: OrgSeed[] = [
  {
    id: "org_a1f09c",
    name: "Northwind Metals",
    slug: "northwind-metals",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2024-02-11T00:00:00Z",
    updated_at: "2026-08-23T14:20:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_bespoke_northwind_v1", plan_name: "Enterprise · Northwind", is_bespoke: true, state: "active", seats: 45, created_at: "2024-02-11T00:00:00Z" },
    ],
  },
  {
    id: "org_b2e14d",
    name: "Sundaram Auctioneers",
    slug: "sundaram-auctioneers",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2024-09-03T00:00:00Z",
    updated_at: "2026-08-24T08:05:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_scale_v3", plan_name: "Scale", is_bespoke: false, state: "active", seats: 30, created_at: "2024-09-03T00:00:00Z" },
    ],
  },
  {
    id: "org_c3d22e",
    name: "Rajdhani Traders",
    slug: "rajdhani-traders",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2025-06-19T00:00:00Z",
    updated_at: "2026-08-22T17:41:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_growth_v4", plan_name: "Growth", is_bespoke: false, state: "active", seats: 18, created_at: "2025-06-19T00:00:00Z" },
    ],
  },
  {
    id: "org_d4c33f",
    name: "Kaveri Steel",
    slug: "kaveri-steel",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2025-01-27T00:00:00Z",
    updated_at: "2026-08-24T06:12:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_growth_v2", plan_name: "Growth", is_bespoke: false, state: "active", seats: 26, created_at: "2025-01-27T00:00:00Z" },
      { product_key: "crm", plan_version_id: "pv_crm_growth_v2", plan_name: "Growth", is_bespoke: false, state: "active", seats: 9, created_at: "2025-11-04T00:00:00Z" },
    ],
  },
  {
    id: "org_e5b440",
    name: "Deccan Commodities",
    slug: "deccan-commodities",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2024-05-14T00:00:00Z",
    updated_at: "2026-08-23T21:58:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_enterprise_v2", plan_name: "Enterprise", is_bespoke: false, state: "active", seats: 60, created_at: "2024-05-14T00:00:00Z" },
      { product_key: "hrms", plan_version_id: "pv_hrms_scale_v2", plan_name: "Scale", is_bespoke: false, state: "active", seats: 60, created_at: "2025-07-19T00:00:00Z" },
    ],
  },
  {
    id: "org_f6a551",
    name: "Meridian HR Cloud",
    slug: "meridian-hr",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2025-03-30T00:00:00Z",
    updated_at: "2026-08-24T09:33:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "hrms", plan_version_id: "pv_hrms_growth_v3", plan_name: "Growth", is_bespoke: false, state: "active", seats: 22, created_at: "2025-03-30T00:00:00Z" },
    ],
  },
  {
    id: "org_07c662",
    name: "BlueOak Retail",
    slug: "blueoak-retail",
    country: "IN",
    status: "trial",
    locked: false,
    created_at: "2026-08-06T00:00:00Z",
    updated_at: "2026-08-24T11:02:00Z",
    trial_ends_at: "2026-09-05T00:00:00Z",
    subscriptions: [
      { product_key: "crm", plan_version_id: "pv_crm_growth_v2", plan_name: "Growth", is_bespoke: false, state: "trial", seats: 12, created_at: "2026-08-06T00:00:00Z" },
    ],
  },
  {
    id: "org_18d773",
    name: "Corveto Logistics",
    slug: "corveto-logistics",
    country: "IN",
    status: "trial",
    locked: false,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-23T19:47:00Z",
    trial_ends_at: "2026-08-30T00:00:00Z",
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_growth_v2", plan_name: "Starter", is_bespoke: false, state: "trial", seats: 8, created_at: "2026-08-01T00:00:00Z" },
    ],
  },
  {
    id: "org_3af995",
    name: "Pioneer Alloys",
    slug: "pioneer-alloys",
    country: "IN",
    status: "past_due",
    locked: false,
    created_at: "2025-04-08T00:00:00Z",
    updated_at: "2026-08-21T13:26:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_growth_v4", plan_name: "Growth", is_bespoke: false, state: "past_due", seats: 20, created_at: "2025-04-08T00:00:00Z" },
    ],
  },
  {
    id: "org_4b0aa6",
    name: "Vertex Mining",
    slug: "vertex-mining",
    country: "IN",
    status: "suspended",
    locked: true,
    created_at: "2024-12-01T00:00:00Z",
    updated_at: "2026-07-30T10:04:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_scale_v3", plan_name: "Scale", is_bespoke: false, state: "suspended", seats: 28, created_at: "2024-12-01T00:00:00Z" },
    ],
  },
  {
    id: "org_5c1bb7",
    name: "Sahara Scrap Co",
    slug: "sahara-scrap",
    country: "IN",
    status: "suspended",
    locked: true,
    created_at: "2025-02-16T00:00:00Z",
    updated_at: "2026-08-04T16:38:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_growth_v2", plan_name: "Growth", is_bespoke: false, state: "suspended", seats: 10, created_at: "2025-02-16T00:00:00Z" },
    ],
  },
  {
    id: "org_6d2cc8",
    name: "Orbit Aggregates",
    slug: "orbit-aggregates",
    country: "IN",
    status: "cancelled",
    locked: false,
    created_at: "2024-08-22T00:00:00Z",
    updated_at: "2026-07-12T09:15:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_scale_v3", plan_name: "Growth", is_bespoke: false, state: "cancelled", seats: 14, created_at: "2024-08-22T00:00:00Z" },
    ],
  },
  {
    id: "org_7e3dd9",
    name: "Lotus Manufacturing",
    slug: "lotus-mfg",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2025-05-11T00:00:00Z",
    updated_at: "2026-08-24T05:44:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "hrms", plan_version_id: "pv_hrms_scale_v2", plan_name: "Scale", is_bespoke: false, state: "active", seats: 40, created_at: "2025-05-11T00:00:00Z" },
    ],
  },
  {
    id: "org_8f4eea",
    name: "Zenith Polymers",
    slug: "zenith-polymers",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2025-08-28T00:00:00Z",
    updated_at: "2026-08-23T22:31:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "crm", plan_version_id: "pv_crm_scale_v1", plan_name: "Scale", is_bespoke: false, state: "active", seats: 33, created_at: "2025-08-28T00:00:00Z" },
    ],
  },
  {
    id: "org_905ffb",
    name: "Everest Bearings",
    slug: "everest-bearings",
    country: "IN",
    status: "active",
    locked: false,
    created_at: "2024-11-19T00:00:00Z",
    updated_at: "2026-08-24T10:26:00Z",
    trial_ends_at: null,
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_growth_v2", plan_name: "Enterprise", is_bespoke: false, state: "active", seats: 55, created_at: "2024-11-19T00:00:00Z" },
    ],
  },
];

/** Does this subscription state contribute to MRR? Trials and terminal states do not. */
function contributesMrr(state: string): boolean {
  return state === "active" || state === "past_due";
}

function subMonthlyMinor(seed: SubSeed): number {
  const version = planVersionById(seed.plan_version_id);
  return version ? versionMonthlyMinor(version) : 0;
}

/** Every subscription across every tenant, flattened — the fixtures-mode source for both
 *  `/admin/subscriptions` and the per-org subscription rollups the pricing fixture reads. */
export const SUBSCRIPTIONS: FixtureSubscription[] = ORG_SEEDS.flatMap((org) =>
  org.subscriptions.map((seed, i): FixtureSubscription => ({
    subscription_id: `sub_${org.id.replace("org_", "")}_${i + 1}`,
    organization_id: org.id,
    product_key: seed.product_key,
    plan_version_id: seed.plan_version_id,
    plan_name: seed.plan_name,
    is_bespoke: seed.is_bespoke,
    state: seed.state,
    seats: seed.seats,
    amount_minor: subMonthlyMinor(seed),
    currency: "INR",
    trial_ends_at: seed.state === "trial" ? org.trial_ends_at : null,
    current_period_end: "2026-08-31T23:59:59Z",
    created_at: seed.created_at,
    entitled_products: planVersionById(seed.plan_version_id)?.bundled_products ?? [seed.product_key],
  })),
);

export function subscriptionsForOrg(organizationId: string): FixtureSubscription[] {
  return SUBSCRIPTIONS.filter((s) => s.organization_id === organizationId);
}

/** The directory identity `GET /admin/organizations/{id}` composes in from account-service — the
 *  only place a name is ever visible. */
export interface OrgDirectoryEntry {
  organization_id: string;
  slug: string;
  name: string;
  status: string;
  country: string | null;
  created_at: string;
}

const ORG_SEED_BY_ID = new Map(ORG_SEEDS.map((o) => [o.id, o]));

export function orgDirectory(organizationId: string): OrgDirectoryEntry | undefined {
  const seed = ORG_SEED_BY_ID.get(organizationId);
  if (!seed) return undefined;
  return {
    organization_id: seed.id,
    slug: seed.slug,
    name: seed.name,
    status: seed.status,
    country: seed.country,
    created_at: seed.created_at,
  };
}

/** One organization's commercial snapshot — `OrgStateOut`, exactly what `/admin/organizations`
 *  and the `commercial` half of `/admin/organizations/{id}` carry. No name: the real projection
 *  does not have one either. */
export function orgStateFor(organizationId: string): OrgStateOut | undefined {
  const seed = ORG_SEED_BY_ID.get(organizationId);
  if (!seed) return undefined;
  const subs = subscriptionsForOrg(organizationId);
  const mrrMinor = subs.filter((s) => contributesMrr(s.state)).reduce((sum, s) => sum + s.amount_minor, 0);
  const activeProducts = subs.filter((s) => s.state === "active" || s.state === "trial").map((s) => s.product_key);
  const subscriptionStates: Record<string, SubscriptionStateEntry> = {};
  for (const s of subs) {
    subscriptionStates[s.product_key] = {
      state: s.state,
      plan_version_id: s.plan_version_id,
      amount_minor: s.amount_minor,
      currency: s.currency,
      trial_ends_at: s.trial_ends_at,
    };
  }
  return {
    organization_id: seed.id,
    mrr_minor: mrrMinor,
    currency: "INR",
    active_products: activeProducts,
    subscription_states: subscriptionStates,
    trial_ends_at: seed.trial_ends_at,
    locked: seed.locked,
    updated_at: seed.updated_at,
  };
}

export function buildOrgStates(): OrgStateOut[] {
  return ORG_SEEDS.map((seed) => orgStateFor(seed.id)!);
}

export function allOrgIds(): string[] {
  return ORG_SEEDS.map((s) => s.id);
}
