/**
 * The tenant directory fixture: sixteen customer organizations across every product, plan and
 * lifecycle state, plus the subscriptions derived from them. This is the source of truth the rest
 * of the fixtures reference by `organization_id`.
 *
 * MRR is never stored on the org — it is summed from the org's active/past-due subscriptions
 * (ADR-0004 §4: "MRR is the running sum of activations and their pinned prices"), exactly as the
 * console's projection would compute it off the transition stream.
 */

import type { AccountStatus, Contact, OrganizationRow, Subscription } from "@/api/types";
import { PRODUCT_NAME, planVersionById, versionMonthlyMinor } from "./catalog";

interface SubSeed {
  product_key: string;
  plan_version_id: string;
  plan_name: string;
  is_bespoke: boolean;
  state: AccountStatus;
  seats: number;
  created_at: string;
}

interface OrgSeed {
  id: string;
  name: string;
  slug: string;
  region: string;
  status: AccountStatus;
  locked: boolean;
  created_at: string;
  last_activity_at: string | null;
  trial_ends_at: string | null;
  contact: Contact;
  subscriptions: SubSeed[];
}

const ORG_SEEDS: OrgSeed[] = [
  {
    id: "org_a1f09c",
    name: "Northwind Metals",
    slug: "northwind-metals",
    region: "Mumbai, IN",
    status: "active",
    locked: false,
    created_at: "2024-02-11T00:00:00Z",
    last_activity_at: "2026-08-23T14:20:00Z",
    trial_ends_at: null,
    contact: { name: "Rukmini Shah", email: "rukmini.shah@northwindmetals.in" },
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_bespoke_northwind_v1", plan_name: "Enterprise · Northwind", is_bespoke: true, state: "active", seats: 45, created_at: "2024-02-11T00:00:00Z" },
    ],
  },
  {
    id: "org_b2e14d",
    name: "Sundaram Auctioneers",
    slug: "sundaram-auctioneers",
    region: "Chennai, IN",
    status: "active",
    locked: false,
    created_at: "2024-09-03T00:00:00Z",
    last_activity_at: "2026-08-24T08:05:00Z",
    trial_ends_at: null,
    contact: { name: "Arvind Sundaram", email: "arvind@sundaramauctioneers.com" },
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_scale_v3", plan_name: "Scale", is_bespoke: false, state: "active", seats: 30, created_at: "2024-09-03T00:00:00Z" },
    ],
  },
  {
    id: "org_c3d22e",
    name: "Rajdhani Traders",
    slug: "rajdhani-traders",
    region: "New Delhi, IN",
    status: "active",
    locked: false,
    created_at: "2025-06-19T00:00:00Z",
    last_activity_at: "2026-08-22T17:41:00Z",
    trial_ends_at: null,
    contact: { name: "Naveen Khanna", email: "naveen.khanna@rajdhanitraders.in" },
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_growth_v4", plan_name: "Growth", is_bespoke: false, state: "active", seats: 18, created_at: "2025-06-19T00:00:00Z" },
    ],
  },
  {
    id: "org_d4c33f",
    name: "Kaveri Steel",
    slug: "kaveri-steel",
    region: "Coimbatore, IN",
    status: "active",
    locked: false,
    created_at: "2025-01-27T00:00:00Z",
    last_activity_at: "2026-08-24T06:12:00Z",
    trial_ends_at: null,
    contact: { name: "Lakshmi Narayanan", email: "lakshmi.n@kaveristeel.co.in" },
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_scale_v2", plan_name: "Scale", is_bespoke: false, state: "active", seats: 26, created_at: "2025-01-27T00:00:00Z" },
      { product_key: "crm", plan_version_id: "pv_crm_growth_v2", plan_name: "Growth", is_bespoke: false, state: "active", seats: 9, created_at: "2025-11-04T00:00:00Z" },
      { product_key: "suite", plan_version_id: "pv_suite_duo_v1", plan_name: "Suite Duo", is_bespoke: false, state: "active", seats: 9, created_at: "2026-08-26T00:00:00Z" },
    ],
  },
  {
    id: "org_e5b440",
    name: "Deccan Commodities",
    slug: "deccan-commodities",
    region: "Hyderabad, IN",
    status: "active",
    locked: false,
    created_at: "2024-05-14T00:00:00Z",
    last_activity_at: "2026-08-23T21:58:00Z",
    trial_ends_at: null,
    contact: { name: "Farhan Qureshi", email: "farhan.qureshi@deccancommodities.com" },
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_bespoke_deccan_v1", plan_name: "Enterprise · Deccan", is_bespoke: true, state: "active", seats: 60, created_at: "2024-05-14T00:00:00Z" },
      { product_key: "hrms", plan_version_id: "pv_hrms_scale_v2", plan_name: "Scale", is_bespoke: false, state: "active", seats: 60, created_at: "2025-07-19T00:00:00Z" },
    ],
  },
  {
    id: "org_f6a551",
    name: "Meridian HR Cloud",
    slug: "meridian-hr",
    region: "Bengaluru, IN",
    status: "active",
    locked: false,
    created_at: "2025-03-30T00:00:00Z",
    last_activity_at: "2026-08-24T09:33:00Z",
    trial_ends_at: null,
    contact: { name: "Ananya Prakash", email: "ananya@meridianhr.cloud" },
    subscriptions: [
      { product_key: "hrms", plan_version_id: "pv_hrms_growth_v3", plan_name: "Growth", is_bespoke: false, state: "active", seats: 22, created_at: "2025-03-30T00:00:00Z" },
    ],
  },
  {
    id: "org_07c662",
    name: "BlueOak Retail",
    slug: "blueoak-retail",
    region: "Pune, IN",
    status: "trial",
    locked: false,
    created_at: "2026-08-06T00:00:00Z",
    last_activity_at: "2026-08-24T11:02:00Z",
    trial_ends_at: "2026-09-05T00:00:00Z",
    contact: { name: "Devika Rao", email: "devika.rao@blueoakretail.in" },
    subscriptions: [
      { product_key: "crm", plan_version_id: "pv_crm_growth_v2", plan_name: "Growth", is_bespoke: false, state: "trial", seats: 12, created_at: "2026-08-06T00:00:00Z" },
    ],
  },
  {
    id: "org_18d773",
    name: "Corveto Logistics",
    slug: "corveto-logistics",
    region: "Ahmedabad, IN",
    status: "trial",
    locked: false,
    created_at: "2026-08-01T00:00:00Z",
    last_activity_at: "2026-08-23T19:47:00Z",
    trial_ends_at: "2026-08-30T00:00:00Z",
    contact: { name: "Yusuf Merchant", email: "yusuf@corvetologistics.com" },
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_starter_v1", plan_name: "Starter", is_bespoke: false, state: "trial", seats: 8, created_at: "2026-08-01T00:00:00Z" },
    ],
  },
  {
    id: "org_29e884",
    name: "Anta Foods",
    slug: "anta-foods",
    region: "Kolkata, IN",
    status: "trial",
    locked: false,
    created_at: "2026-08-13T00:00:00Z",
    last_activity_at: "2026-08-24T07:19:00Z",
    trial_ends_at: "2026-09-12T00:00:00Z",
    contact: { name: "Sourav Dutta", email: "sourav.dutta@antafoods.in" },
    subscriptions: [
      { product_key: "hrms", plan_version_id: "pv_hrms_starter_v2", plan_name: "Starter", is_bespoke: false, state: "trial", seats: 15, created_at: "2026-08-13T00:00:00Z" },
    ],
  },
  {
    id: "org_3af995",
    name: "Pioneer Alloys",
    slug: "pioneer-alloys",
    region: "Nagpur, IN",
    status: "past_due",
    locked: false,
    created_at: "2025-04-08T00:00:00Z",
    last_activity_at: "2026-08-21T13:26:00Z",
    trial_ends_at: null,
    contact: { name: "Harish Gokhale", email: "harish.gokhale@pioneeralloys.in" },
    subscriptions: [
      { product_key: "trading", plan_version_id: "pv_trading_growth_v4", plan_name: "Growth", is_bespoke: false, state: "past_due", seats: 20, created_at: "2025-04-08T00:00:00Z" },
    ],
  },
  {
    id: "org_4b0aa6",
    name: "Vertex Mining",
    slug: "vertex-mining",
    region: "Ranchi, IN",
    status: "suspended",
    locked: true,
    created_at: "2024-12-01T00:00:00Z",
    last_activity_at: "2026-07-30T10:04:00Z",
    trial_ends_at: null,
    contact: { name: "Pradeep Munda", email: "pradeep@vertexmining.co.in" },
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_scale_v3", plan_name: "Scale", is_bespoke: false, state: "suspended", seats: 28, created_at: "2024-12-01T00:00:00Z" },
    ],
  },
  {
    id: "org_5c1bb7",
    name: "Sahara Scrap Co",
    slug: "sahara-scrap",
    region: "Jaipur, IN",
    status: "suspended",
    locked: true,
    created_at: "2025-02-16T00:00:00Z",
    last_activity_at: "2026-08-04T16:38:00Z",
    trial_ends_at: null,
    contact: { name: "Imran Sheikh", email: "imran.sheikh@saharascrap.in" },
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_growth_v2", plan_name: "Growth", is_bespoke: false, state: "suspended", seats: 10, created_at: "2025-02-16T00:00:00Z" },
    ],
  },
  {
    id: "org_6d2cc8",
    name: "Orbit Aggregates",
    slug: "orbit-aggregates",
    region: "Indore, IN",
    status: "cancelled",
    locked: false,
    created_at: "2024-08-22T00:00:00Z",
    last_activity_at: "2026-07-12T09:15:00Z",
    trial_ends_at: null,
    contact: { name: "Meghna Joshi", email: "meghna.joshi@orbitaggregates.in" },
    subscriptions: [
      { product_key: "auctions", plan_version_id: "pv_auctions_growth_v1", plan_name: "Growth", is_bespoke: false, state: "cancelled", seats: 14, created_at: "2024-08-22T00:00:00Z" },
    ],
  },
  {
    id: "org_7e3dd9",
    name: "Lotus Manufacturing",
    slug: "lotus-mfg",
    region: "Surat, IN",
    status: "active",
    locked: false,
    created_at: "2025-05-11T00:00:00Z",
    last_activity_at: "2026-08-24T05:44:00Z",
    trial_ends_at: null,
    contact: { name: "Bhavesh Patel", email: "bhavesh.patel@lotusmfg.in" },
    subscriptions: [
      { product_key: "hrms", plan_version_id: "pv_hrms_scale_v2", plan_name: "Scale", is_bespoke: false, state: "active", seats: 40, created_at: "2025-05-11T00:00:00Z" },
    ],
  },
  {
    id: "org_8f4eea",
    name: "Zenith Polymers",
    slug: "zenith-polymers",
    region: "Vadodara, IN",
    status: "active",
    locked: false,
    created_at: "2025-08-28T00:00:00Z",
    last_activity_at: "2026-08-23T22:31:00Z",
    trial_ends_at: null,
    contact: { name: "Nikhil Desai", email: "nikhil.desai@zenithpolymers.in" },
    subscriptions: [
      { product_key: "crm", plan_version_id: "pv_crm_scale_v1", plan_name: "Scale", is_bespoke: false, state: "active", seats: 33, created_at: "2025-08-28T00:00:00Z" },
    ],
  },
  {
    id: "org_905ffb",
    name: "Everest Bearings",
    slug: "everest-bearings",
    region: "Ludhiana, IN",
    status: "active",
    locked: false,
    created_at: "2024-11-19T00:00:00Z",
    last_activity_at: "2026-08-24T10:26:00Z",
    trial_ends_at: null,
    contact: { name: "Gurpreet Singh", email: "gurpreet.singh@everestbearings.in" },
    subscriptions: [
      { product_key: "procure", plan_version_id: "pv_procure_enterprise_v1", plan_name: "Enterprise", is_bespoke: false, state: "active", seats: 55, created_at: "2024-11-19T00:00:00Z" },
    ],
  },
];

/** Does this subscription state contribute to MRR? Trials and terminal states do not. */
function contributesMrr(state: AccountStatus): boolean {
  return state === "active" || state === "past_due";
}

function subMonthlyMinor(seed: SubSeed): number {
  const version = planVersionById(seed.plan_version_id);
  return version ? versionMonthlyMinor(version) : 0;
}

/** Every subscription across every tenant, flattened for the cross-tenant subscriptions list. */
export const SUBSCRIPTIONS: Subscription[] = ORG_SEEDS.flatMap((org) =>
  org.subscriptions.map((seed, i): Subscription => {
    const nextRenewal = seed.state === "cancelled" ? null : "2026-09-01T00:00:00Z";
    return {
      subscription_id: `sub_${org.id.replace("org_", "")}_${i + 1}`,
      organization_id: org.id,
      organization_name: org.name,
      product_key: seed.product_key,
      product_name: PRODUCT_NAME[seed.product_key] ?? seed.product_key,
      plan_name: seed.plan_name,
      plan_version_id: seed.plan_version_id,
      plan_version_no: planVersionById(seed.plan_version_id)?.version_no ?? 1,
      is_bespoke: seed.is_bespoke,
      state: seed.state,
      seats: seed.seats,
      amount_minor: subMonthlyMinor(seed),
      currency: "INR",
      trial_ends_at: seed.state === "trial" ? org.trial_ends_at : null,
      current_period_start: "2026-08-01T00:00:00Z",
      current_period_end: "2026-08-31T23:59:59Z",
      next_renewal_at: nextRenewal,
      cancel_at: seed.state === "cancelled" ? "2026-07-12T00:00:00Z" : null,
      created_at: seed.created_at,
      entitled_products: planVersionById(seed.plan_version_id)?.bundled_products ?? [seed.product_key],
    };
  }),
);

export function subscriptionsForOrg(organizationId: string): Subscription[] {
  return SUBSCRIPTIONS.filter((s) => s.organization_id === organizationId);
}

/** The cross-tenant directory rows, with MRR summed from each org's contributing subscriptions. */
export const ORGANIZATIONS: OrganizationRow[] = ORG_SEEDS.map((org): OrganizationRow => {
  const subs = subscriptionsForOrg(org.id);
  const primary = org.subscriptions[0];
  const mrr = subs
    .filter((s) => contributesMrr(s.state))
    .reduce((sum, s) => sum + s.amount_minor, 0);
  const seats = subs.reduce((sum, s) => sum + s.seats, 0);
  const primaryKey = primary?.product_key ?? "hrms";
  return {
    organization_id: org.id,
    name: org.name,
    slug: org.slug,
    status: org.status,
    plan_name: primary?.plan_name ?? "—",
    primary_product_key: primaryKey,
    primary_product_name: PRODUCT_NAME[primaryKey] ?? primaryKey,
    mrr_minor: mrr,
    currency: "INR",
    seats,
    is_bespoke: subs.some((s) => s.is_bespoke),
    locked: org.locked,
    created_at: org.created_at,
    last_activity_at: org.last_activity_at,
    trial_ends_at: org.trial_ends_at,
    region: org.region,
    primary_contact: org.contact,
  };
});

const ORG_BY_ID = new Map(ORGANIZATIONS.map((o) => [o.organization_id, o]));

export function orgById(id: string): OrganizationRow | undefined {
  return ORG_BY_ID.get(id);
}
