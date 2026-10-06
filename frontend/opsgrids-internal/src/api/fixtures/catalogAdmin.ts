/**
 * An in-memory catalogue for fixtures mode — the Plans & prices page end to end with no gateway.
 *
 * Loaded only through `api/catalog.ts`'s dynamic `import()` (never statically), so a production
 * build drops it. It mimics the server's shapes and the few rules a screen can observe — public
 * audience by default, newest version first, a published version only withdrawn, only a draft
 * deleted — and nothing else: validation, idempotency and authorization are the server's.
 */

import type {
  Audience,
  DeletedVersionOut,
  ListPlansParams,
  PlanCreateRequest,
  PlanDetailOut,
  PlanListItemOut,
  PlanListOut,
  PlanState,
  PlanVersionOut,
  VersionCreateRequest,
  VersionState,
} from "../catalogTypes";

interface StoredPlan {
  id: string;
  product_key: string;
  code: string;
  name: string;
  state: PlanState;
  sort_order: number;
  created_at: string;
}

const NOW = "2026-10-01T09:30:00Z";
const BESPOKE_ORG = "0b6f3c1e-1d6e-4b8e-9b55-3f1c7c1a2d10";

let sequence = 0;
function nextId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${String(sequence).padStart(4, "0")}`;
}

const plans: StoredPlan[] = [
  { id: "plan-hrms-starter", product_key: "hrms", code: "starter", name: "People Starter", state: "active", sort_order: 10, created_at: NOW },
  { id: "plan-hrms-growth", product_key: "hrms", code: "growth", name: "People Growth", state: "active", sort_order: 20, created_at: NOW },
  { id: "plan-crm-scale", product_key: "crm", code: "scale", name: "CRM Scale", state: "active", sort_order: 10, created_at: NOW },
  { id: "plan-crm-legacy", product_key: "crm", code: "legacy", name: "CRM Legacy", state: "retired", sort_order: 90, created_at: NOW },
];

function version(
  planId: string,
  no: number,
  state: VersionState,
  flatMinor: number,
  extra: Partial<PlanVersionOut> = {},
): PlanVersionOut {
  const id = `${planId}-v${no}`;
  return {
    id,
    plan_id: planId,
    plan_name: null,
    product_key: null,
    plan_state: null,
    version_no: no,
    state,
    billing_interval: "month",
    currency: "INR",
    restricted_to_org_id: null,
    published_at: state === "draft" ? null : NOW,
    prices: [
      { id: `${id}-p1`, kind: "flat", amount_minor: flatMinor, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
    ],
    features: [{ feature_key: "trial_days", value: 14 }],
    seat_classes: [],
    ...extra,
  };
}

const versions: PlanVersionOut[] = [
  version("plan-hrms-starter", 1, "grandfathered", 49_900),
  version("plan-hrms-starter", 2, "published", 59_900),
  version("plan-hrms-growth", 1, "published", 1_49_900, {
    prices: [
      { id: "plan-hrms-growth-v1-p1", kind: "flat", amount_minor: 1_49_900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
      { id: "plan-hrms-growth-v1-p2", kind: "per_seat", amount_minor: 19_900, currency: "INR", unit: "seat", unit_price_micros: null, meter_key: null },
    ],
    seat_classes: [
      { code: "member", name: "Member", price_id: "plan-hrms-growth-v1-p2", included_count: null, can_write: true, sort_order: 10, description: null },
      { code: "viewer", name: "Viewer", price_id: null, included_count: null, can_write: false, sort_order: 20, description: "Read-only" },
    ],
  }),
  version("plan-hrms-growth", 2, "published", 1_20_000, { restricted_to_org_id: BESPOKE_ORG }),
  version("plan-hrms-growth", 3, "draft", 1_59_900),
  version("plan-crm-scale", 1, "published", 2_49_900, {
    prices: [
      { id: "plan-crm-scale-v1-p1", kind: "flat", amount_minor: 2_49_900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
      { id: "plan-crm-scale-v1-p2", kind: "usage", amount_minor: null, currency: "INR", unit: "email", unit_price_micros: 2_500, meter_key: "emails_sent" },
    ],
  }),
  version("plan-crm-legacy", 1, "published", 99_900),
];

function settle<T>(value: T, ms = 200): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), ms));
}

function audienceMatches(v: PlanVersionOut, audience: Audience): boolean {
  if (audience === "all") return true;
  return audience === "public" ? v.restricted_to_org_id === null : v.restricted_to_org_id !== null;
}

function withPlan(v: PlanVersionOut): PlanVersionOut {
  const plan = plans.find((p) => p.id === v.plan_id);
  return { ...v, plan_name: plan?.name ?? null, product_key: plan?.product_key ?? null, plan_state: plan?.state ?? null };
}

function row(plan: StoredPlan): PlanListItemOut {
  const mine = versions.filter((v) => v.plan_id === plan.id);
  const pub = mine.filter((v) => v.restricted_to_org_id === null);
  const current = pub
    .filter((v) => v.state === "published")
    .sort((a, b) => b.version_no - a.version_no)[0];
  return {
    ...plan,
    counts: {
      draft: pub.filter((v) => v.state === "draft").length,
      published: pub.filter((v) => v.state === "published").length,
      grandfathered: pub.filter((v) => v.state === "grandfathered").length,
      bespoke: mine.length - pub.length,
    },
    current: current ? withPlan(current) : null,
    latest_version_no: Math.max(0, ...mine.map((v) => v.version_no)),
  };
}

function requireVersion(versionId: string): PlanVersionOut {
  const found = versions.find((v) => v.id === versionId);
  if (!found) throw new Error("No such plan version");
  return found;
}

function requirePlan(planId: string): StoredPlan {
  const found = plans.find((p) => p.id === planId);
  if (!found) throw new Error("No such plan");
  return found;
}

export function fixtureListPlans(params: ListPlansParams): Promise<PlanListOut> {
  const audience = params.audience ?? "public";
  const items = plans
    .filter((p) => !params.productKey || p.product_key === params.productKey)
    .filter((p) => !params.state || p.state === params.state)
    .filter((p) => {
      const mine = versions.filter((v) => v.plan_id === p.id);
      if (audience === "all") return true;
      if (audience === "restricted") return mine.some((v) => v.restricted_to_org_id !== null);
      return mine.length === 0 || mine.some((v) => v.restricted_to_org_id === null);
    })
    .sort((a, b) => a.product_key.localeCompare(b.product_key) || a.sort_order - b.sort_order)
    .map(row);
  return settle({ items, next_cursor: null });
}

export function fixtureGetPlan(
  planId: string,
  params: { audience?: Audience; cursor?: string | undefined; limit?: number },
): Promise<PlanDetailOut> {
  const plan = requirePlan(planId);
  const summary = row(plan);
  const list = versions
    .filter((v) => v.plan_id === planId && audienceMatches(v, params.audience ?? "public"))
    .sort((a, b) => b.version_no - a.version_no)
    .map((v) => ({
      id: v.id,
      plan_id: v.plan_id,
      version_no: v.version_no,
      state: v.state,
      billing_interval: v.billing_interval,
      currency: v.currency,
      restricted_to_org_id: v.restricted_to_org_id,
      published_at: v.published_at,
      created_at: NOW,
      price_count: v.prices.length,
      feature_count: v.features.length,
    }));
  return settle({
    ...plan,
    updated_at: NOW,
    counts: summary.counts,
    latest_version_no: summary.latest_version_no,
    versions: list,
    next_cursor: null,
  });
}

export function fixtureGetVersion(versionId: string): Promise<PlanVersionOut> {
  return settle(withPlan(requireVersion(versionId)));
}

export function fixtureCreatePlan(request: PlanCreateRequest): Promise<PlanListItemOut> {
  const plan: StoredPlan = {
    id: nextId("plan"),
    product_key: request.product_key,
    code: request.code,
    name: request.name,
    state: "active",
    sort_order: request.sort_order,
    created_at: new Date().toISOString(),
  };
  plans.push(plan);
  return settle(row(plan), 400);
}

export function fixtureSetPlanState(planId: string, state: PlanState): Promise<PlanListItemOut> {
  const plan = requirePlan(planId);
  plan.state = state;
  return settle(row(plan), 400);
}

export function fixtureCreateVersion(planId: string, request: VersionCreateRequest): Promise<PlanVersionOut> {
  requirePlan(planId);
  const no = Math.max(0, ...versions.filter((v) => v.plan_id === planId).map((v) => v.version_no)) + 1;
  const id = nextId("version");
  const prices = request.prices.map((p, index) => ({
    id: `${id}-p${index + 1}`,
    kind: p.kind,
    amount_minor: p.amount_minor ?? null,
    currency: p.currency,
    unit: p.unit ?? null,
    unit_price_micros: p.unit_price_micros ?? null,
    meter_key: p.meter_key ?? null,
  }));
  const created: PlanVersionOut = {
    id,
    plan_id: planId,
    plan_name: null,
    product_key: null,
    plan_state: null,
    version_no: no,
    state: "draft",
    billing_interval: request.billing_interval,
    currency: request.currency,
    restricted_to_org_id: request.restricted_to_org_id,
    published_at: null,
    prices,
    features: request.features,
    seat_classes: request.seat_classes.map((sc) => ({
      code: sc.code,
      name: sc.name,
      price_id: sc.price_index === null ? null : (prices[sc.price_index]?.id ?? null),
      included_count: sc.included_count,
      can_write: sc.can_write,
      sort_order: sc.sort_order,
      description: sc.description,
    })),
  };
  versions.push(created);
  return settle(withPlan(created), 400);
}

export function fixtureSetVersionState(versionId: string, state: VersionState): Promise<PlanVersionOut> {
  const found = requireVersion(versionId);
  found.state = state;
  if (state === "published") found.published_at = new Date().toISOString();
  return settle(withPlan(found), 400);
}

export function fixtureDeleteVersion(versionId: string): Promise<DeletedVersionOut> {
  const found = requireVersion(versionId);
  versions.splice(versions.indexOf(found), 1);
  return settle({ id: found.id, plan_id: found.plan_id, version_no: found.version_no, deleted: true }, 400);
}
