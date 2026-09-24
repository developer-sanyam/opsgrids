/**
 * Internal catalog seed data — products, plans and immutable plan versions. Nothing here is a wire
 * type any more: catalog-service's real `/admin/catalog` prefix only carries write routes (create
 * plan, patch, retire, create/publish version), never a `GET` that lists them, so there is no live
 * "browse the catalog" contract for a fixture to imitate. This module survives purely as seed data
 * the other fixtures (`orgs.ts`, `pricing.ts`) reference to keep prices and plan names internally
 * consistent — see `routes/Plans.tsx` for what the console shows instead of a catalog browser.
 *
 * Amounts are integer minor units (paise). `rupees(x)` converts a whole-rupee figure to paise.
 */

export const rupees = (whole: number): number => Math.round(whole * 100);

const INR = "INR";

export interface FixtureProduct {
  product_key: string;
  name: string;
  description: string;
}

export interface FixturePrice {
  price_id: string;
  kind: "flat" | "per_seat" | "usage";
  amount_minor: number;
  currency: string;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

export interface FixturePlanVersion {
  plan_version_id: string;
  plan_id: string;
  product_key: string;
  plan_name: string;
  version_no: number;
  state: "draft" | "published" | "grandfathered";
  billing_interval: "monthly" | "yearly";
  currency: string;
  restricted_to_org_id: string | null;
  restricted_to_org_name: string | null;
  prices: FixturePrice[];
  bundled_products?: string[];
}

export const PRODUCTS: FixtureProduct[] = [
  { product_key: "hrms", name: "OpsGrids HR", description: "People, attendance and payroll." },
  { product_key: "crm", name: "OpsGrids CRM", description: "Sales pipeline, deals and activities." },
  { product_key: "auctions", name: "OpsGrids Auctions", description: "B2B forward, reverse and Dutch auctions." },
  { product_key: "procure", name: "OpsGrids Procure", description: "Procure-to-pay: requisition to purchase order." },
  { product_key: "trading", name: "OpsGrids Trading Desk", description: "Commodity trading, risk and settlement." },
  { product_key: "suite", name: "OpsGrids Suite", description: "Bundles: one subscription, several products." },
];

export const PRODUCT_NAME: Record<string, string> = Object.fromEntries(
  PRODUCTS.map((p) => [p.product_key, p.name]),
);

const flat = (id: string, whole: number): FixturePrice => ({
  price_id: id,
  kind: "flat",
  amount_minor: rupees(whole),
  currency: INR,
  unit: null,
  unit_price_micros: null,
  meter_key: null,
});

const perSeat = (id: string, wholePerSeat: number): FixturePrice => ({
  price_id: id,
  kind: "per_seat",
  amount_minor: rupees(wholePerSeat),
  currency: INR,
  unit: "seat",
  unit_price_micros: null,
  meter_key: null,
});

export const PLAN_VERSIONS: FixturePlanVersion[] = [
  {
    plan_version_id: "pv_hrms_growth_v3",
    plan_id: "pl_hrms_growth",
    product_key: "hrms",
    plan_name: "Growth",
    version_no: 3,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_hrms_growth_v3_flat", 45000), perSeat("pr_hrms_growth_v3_seat", 400)],
  },
  {
    plan_version_id: "pv_hrms_scale_v2",
    plan_id: "pl_hrms_scale",
    product_key: "hrms",
    plan_name: "Scale",
    version_no: 2,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_hrms_scale_v2_flat", 120000)],
  },
  {
    plan_version_id: "pv_hrms_starter_v2",
    plan_id: "pl_hrms_starter",
    product_key: "hrms",
    plan_name: "Starter",
    version_no: 2,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_hrms_starter_v2_flat", 18000)],
  },
  {
    plan_version_id: "pv_crm_growth_v2",
    plan_id: "pl_crm_growth",
    product_key: "crm",
    plan_name: "Growth",
    version_no: 2,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_crm_growth_v2_flat", 45000)],
  },
  {
    plan_version_id: "pv_crm_scale_v1",
    plan_id: "pl_crm_scale",
    product_key: "crm",
    plan_name: "Scale",
    version_no: 1,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_crm_scale_v1_flat", 120000)],
  },
  {
    plan_version_id: "pv_auctions_scale_v3",
    plan_id: "pl_auctions_scale",
    product_key: "auctions",
    plan_name: "Scale",
    version_no: 3,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_auctions_scale_v3_flat", 120000)],
  },
  {
    plan_version_id: "pv_procure_growth_v2",
    plan_id: "pl_procure_growth",
    product_key: "procure",
    plan_name: "Growth",
    version_no: 2,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_procure_growth_v2_flat", 45000)],
  },
  {
    plan_version_id: "pv_trading_growth_v4",
    plan_id: "pl_trading_growth",
    product_key: "trading",
    plan_name: "Growth",
    version_no: 4,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_trading_growth_v4_flat", 45000)],
  },
  {
    plan_version_id: "pv_trading_enterprise_v2",
    plan_id: "pl_trading_enterprise",
    product_key: "trading",
    plan_name: "Enterprise",
    version_no: 2,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [flat("pr_trading_enterprise_v2_flat", 280000)],
  },
  // --- Bespoke, audience-of-one versions (per-customer pricing, ADR-0004 §2) ---
  {
    plan_version_id: "pv_trading_bespoke_northwind_v1",
    plan_id: "pl_trading_enterprise",
    product_key: "trading",
    plan_name: "Enterprise · Northwind",
    version_no: 1,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: "org_a1f09c",
    restricted_to_org_name: "Northwind Metals",
    prices: [flat("pr_trading_bespoke_northwind_v1_flat", 420000)],
  },
  {
    plan_version_id: "pv_suite_duo_v1",
    plan_id: "pl_suite_duo",
    product_key: "suite",
    plan_name: "Suite Duo",
    version_no: 1,
    state: "published",
    billing_interval: "monthly",
    currency: INR,
    restricted_to_org_id: null,
    restricted_to_org_name: null,
    prices: [perSeat("pr_suite_duo_v1_seat", 582)],
    bundled_products: ["hrms", "crm"],
  },
];

const VERSION_BY_ID = new Map(PLAN_VERSIONS.map((v) => [v.plan_version_id, v]));

export function planVersionById(id: string): FixturePlanVersion | undefined {
  return VERSION_BY_ID.get(id);
}

/** The headline (flat or first) monthly price of a version, in minor units. */
export function versionMonthlyMinor(version: FixturePlanVersion): number {
  const flatPrice = version.prices.find((p) => p.kind === "flat");
  return flatPrice?.amount_minor ?? version.prices[0]?.amount_minor ?? 0;
}
