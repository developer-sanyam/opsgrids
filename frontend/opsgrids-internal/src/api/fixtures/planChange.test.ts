import { beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "@/api/http";
import type { PlanChangeRequest } from "@/api/types";
import { applyFixturePlanChange, planOptionsForOrg, previewFixturePlanChange, resetPlanChangeFixtures } from "./planChange";
import { pricingForOrg, resetPricingFixtures } from "./pricing";

/**
 * The fixtures are a stand-in for the live server (ADR-0029), so they must obey its rules: a
 * trial switches now, a paying subscription at its next period, a stale confirm is refused, and a
 * repeated key replays — otherwise fixtures mode would demonstrate behaviour the product does not
 * have.
 */

const GROWTH_PAYING = "org_f6a551"; // hrms · Growth · active
const TRIAL = "org_07c662"; // crm · Growth · trial
const PAST_DUE = "org_3af995"; // trading · Growth · past_due
const NORTHWIND = "org_a1f09c"; // trading · bespoke
const OTHER_TRADING = "org_e5b440"; // trading · Enterprise · active

beforeEach(() => {
  resetPricingFixtures();
  resetPlanChangeFixtures();
});

function request(org: string, product: string, to: string, overrides: Partial<PlanChangeRequest> = {}): PlanChangeRequest {
  const sub = pricingForOrg(org)?.products.find((p) => p.product_key === product)?.subscription;
  if (!sub) throw new Error("no subscription");
  return {
    plan_version_id: to,
    timing: "auto",
    reason: "because",
    expected: {
      state: sub.state,
      plan_version_id: sub.plan_version_id,
      pending_plan_version_id: sub.pending_plan_version_id,
    },
    ...overrides,
  };
}

function refusalOf(fn: () => unknown): ApiError {
  try {
    fn();
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected a refusal");
}

describe("planOptionsForOrg", () => {
  it("lists the published versions of the product, marking the current one", () => {
    const options = planOptionsForOrg(GROWTH_PAYING, "hrms");
    expect(options.subscription.plan_name).toBe("Growth");
    expect(options.options.map((o) => o.plan_name).sort()).toEqual(["Growth", "Scale", "Starter"]);
    expect(options.options.find((o) => o.is_current)?.plan_name).toBe("Growth");
    expect(options.options.every((o) => o.blocked_code === null)).toBe(true);
    expect(options.subscription.change_blocked_code).toBeNull();
  });

  it("says a subscription that cannot change plan cannot", () => {
    expect(planOptionsForOrg(PAST_DUE, "trading").subscription.change_blocked_code).toBe("state");
  });

  it("is a coded 404 for a product the organization has no subscription for", () => {
    const error = refusalOf(() => planOptionsForOrg(GROWTH_PAYING, "crm"));
    expect([error.status, error.code]).toEqual([404, "no_subscription"]);
  });

  it("never offers another organization's bespoke version", () => {
    const northwind = planOptionsForOrg(NORTHWIND, "trading").options.map((o) => o.plan_version_id);
    const other = planOptionsForOrg(OTHER_TRADING, "trading").options.map((o) => o.plan_version_id);
    expect(northwind).toContain("pv_trading_bespoke_northwind_v1");
    expect(other).not.toContain("pv_trading_bespoke_northwind_v1");
  });
});

describe("previewFixturePlanChange", () => {
  it("sends a paying subscription's upgrade to its next period", () => {
    const preview = previewFixturePlanChange(GROWTH_PAYING, "hrms", { plan_version_id: "pv_hrms_scale_v2", timing: "auto" });
    expect([preview.direction, preview.effective]).toEqual(["upgrade", "next_period"]);
    expect(preview.effective_at).toBe("2026-08-31T23:59:59Z");
    expect(preview.refusal_code).toBeNull();
  });

  it("sends a paying subscription's downgrade to its next period", () => {
    const preview = previewFixturePlanChange(GROWTH_PAYING, "hrms", { plan_version_id: "pv_hrms_starter_v2", timing: "auto" });
    expect([preview.direction, preview.effective]).toEqual(["downgrade", "next_period"]);
  });

  it("switches a trial now, with no date", () => {
    const preview = previewFixturePlanChange(TRIAL, "crm", { plan_version_id: "pv_crm_scale_v1", timing: "auto" });
    expect([preview.direction, preview.effective, preview.effective_at]).toEqual(["upgrade", "now", null]);
  });

  it.each([
    [PAST_DUE, "trading", "pv_trading_enterprise_v2", "state"],
    [GROWTH_PAYING, "hrms", "pv_hrms_growth_v3", "already_pinned"],
    [GROWTH_PAYING, "hrms", "pv_crm_growth_v2", "product_mismatch"],
  ])("answers a refusal as data: %s %s -> %s is %s", (org, product, to, code) => {
    const preview = previewFixturePlanChange(org, product, { plan_version_id: to, timing: "auto" });
    expect(preview.refusal_code).toBe(code);
    expect(preview.direction).toBeNull();
  });

  it("is a 404 for an unknown version and for another organization's bespoke version, identically", () => {
    const unknown = refusalOf(() => previewFixturePlanChange(OTHER_TRADING, "trading", { plan_version_id: "pv_nope", timing: "auto" }));
    const foreign = refusalOf(() =>
      previewFixturePlanChange(OTHER_TRADING, "trading", { plan_version_id: "pv_trading_bespoke_northwind_v1", timing: "auto" }),
    );
    expect([unknown.status, foreign.status]).toEqual([404, 404]);
    expect(unknown.message).toBe(foreign.message);
    expect(unknown.code).toBe(foreign.code);
  });
});

describe("applyFixturePlanChange", () => {
  it("schedules a paying subscription's change, and the next read shows it", () => {
    const result = applyFixturePlanChange(GROWTH_PAYING, "hrms", request(GROWTH_PAYING, "hrms", "pv_hrms_scale_v2"), "key-0001");
    expect(result).toMatchObject({ effective: "next_period", pending_plan_version_id: "pv_hrms_scale_v2", replayed: false });
    const sub = pricingForOrg(GROWTH_PAYING)?.products.find((p) => p.product_key === "hrms")?.subscription;
    expect(sub?.plan_version_id).toBe("pv_hrms_growth_v3");
    expect(sub?.pending_plan_name).toBe("Scale");
  });

  it("switches a trial now and updates the price it shows", () => {
    const result = applyFixturePlanChange(TRIAL, "crm", request(TRIAL, "crm", "pv_crm_scale_v1"), "key-0002");
    expect(result).toMatchObject({ effective: "now", plan_version_id: "pv_crm_scale_v1", pending_plan_version_id: null });
    const product = pricingForOrg(TRIAL)?.products.find((p) => p.product_key === "crm");
    expect(product?.subscription?.plan_name).toBe("Scale");
    expect(product?.current_price?.amount_minor).toBe(12_000_000);
  });

  it("withdraws a scheduled change when pointed back at the current plan", () => {
    applyFixturePlanChange(GROWTH_PAYING, "hrms", request(GROWTH_PAYING, "hrms", "pv_hrms_scale_v2"), "key-0003");
    const result = applyFixturePlanChange(
      GROWTH_PAYING,
      "hrms",
      request(GROWTH_PAYING, "hrms", "pv_hrms_growth_v3"),
      "key-0004",
    );
    expect(result).toMatchObject({ effective: "now", pending_plan_version_id: null });
  });

  it("replays a repeated key instead of changing twice", () => {
    const body = request(GROWTH_PAYING, "hrms", "pv_hrms_scale_v2");
    const first = applyFixturePlanChange(GROWTH_PAYING, "hrms", body, "key-0005");
    const again = applyFixturePlanChange(GROWTH_PAYING, "hrms", body, "key-0005");
    expect(again).toEqual({ ...first, replayed: true });
  });

  it("refuses the same key for a different request", () => {
    applyFixturePlanChange(GROWTH_PAYING, "hrms", request(GROWTH_PAYING, "hrms", "pv_hrms_scale_v2"), "key-0006");
    const error = refusalOf(() =>
      applyFixturePlanChange(GROWTH_PAYING, "hrms", request(GROWTH_PAYING, "hrms", "pv_hrms_starter_v2"), "key-0006"),
    );
    expect([error.status, error.code]).toEqual([409, "idempotency_key_reused"]);
  });

  it("refuses a stale expected state with 409 state_changed and changes nothing", () => {
    const stale = request(GROWTH_PAYING, "hrms", "pv_hrms_scale_v2", {
      expected: { state: "active", plan_version_id: "pv_hrms_starter_v2", pending_plan_version_id: null },
    });
    const error = refusalOf(() => applyFixturePlanChange(GROWTH_PAYING, "hrms", stale, "key-0007"));
    expect([error.status, error.code]).toEqual([409, "state_changed"]);
    const sub = pricingForOrg(GROWTH_PAYING)?.products.find((p) => p.product_key === "hrms")?.subscription;
    expect(sub?.pending_plan_version_id).toBeNull();
  });

  it("refuses what the preview refuses, as a coded 422", () => {
    const error = refusalOf(() =>
      applyFixturePlanChange(PAST_DUE, "trading", request(PAST_DUE, "trading", "pv_trading_enterprise_v2"), "key-0008"),
    );
    expect([error.status, error.code]).toEqual([422, "state"]);
  });
});
