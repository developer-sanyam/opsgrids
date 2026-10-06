import { beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "@/api/http";
import { pricingForOrg, resetPricingFixtures } from "./pricing";
import {
  addProductOptionsForOrg,
  applyFixtureAddProduct,
  applyFixtureExtendTrial,
  resetStaffSubscriptionFixtures,
} from "./staffSubscriptions";

/**
 * The fixtures stand in for the live server (ADR-0031), so they obey its rules: only products with
 * no row are offered, a paid start must be acknowledged, a repeated key replays, a trial extension
 * is absolute and guarded by the end the operator saw — otherwise fixtures mode would demonstrate
 * behaviour the product does not have.
 */

const GROWTH_PAYING = "org_f6a551"; // hrms · active
const TRIAL = "org_07c662"; // crm · trial

beforeEach(() => {
  resetPricingFixtures();
  resetStaffSubscriptionFixtures();
});

function codeOf(fn: () => unknown): string | null {
  try {
    fn();
  } catch (error) {
    if (error instanceof ApiError) return error.code;
    throw error;
  }
  return null;
}

describe("add product", () => {
  it("offers only products the organization has no row for", () => {
    const options = addProductOptionsForOrg(GROWTH_PAYING);
    expect(options.existing.map((e) => e.product_key)).toContain("hrms");
    expect(options.products.map((p) => p.product_key)).not.toContain("hrms");
    expect(options.currency).toBe("INR");
  });

  it("names a bundle's members the organization already pays for", () => {
    const suite = addProductOptionsForOrg(GROWTH_PAYING).products.find((p) => p.product_key === "suite");
    expect(suite?.versions[0]?.overlapping_products).toEqual(["hrms"]);
  });

  it("names the live bundle that already entitles a product offered on its own", () => {
    const suite = addProductOptionsForOrg(TRIAL).products.find((p) => p.product_key === "suite");
    const version = suite?.versions[0];
    if (!version) throw new Error("no suite version");
    expect(addProductOptionsForOrg(TRIAL).products.find((p) => p.product_key === "hrms")?.entitled_through).toEqual([]);
    applyFixtureAddProduct(
      TRIAL,
      { product_key: "suite", plan_version_id: version.plan_version_id, trial: true, acknowledge_immediate_charge: false, reason: "r" },
      "suite-key",
    );
    const hrms = addProductOptionsForOrg(TRIAL).products.find((p) => p.product_key === "hrms");
    expect(hrms?.entitled_through).toEqual(["suite"]);
  });

  it("refuses an unacknowledged paid start, and a product it already has", () => {
    const version = addProductOptionsForOrg(GROWTH_PAYING).products.find((p) => p.product_key === "crm")?.versions[0];
    if (!version) throw new Error("no crm version");
    const paid = {
      product_key: "crm",
      plan_version_id: version.plan_version_id,
      trial: false,
      acknowledge_immediate_charge: false,
      reason: "r",
    };
    expect(codeOf(() => applyFixtureAddProduct(GROWTH_PAYING, paid, "k1"))).toBe("charge_not_acknowledged");
    const hrms = { ...paid, product_key: "hrms", plan_version_id: "pv_hrms_growth_v3", trial: true };
    expect(codeOf(() => applyFixtureAddProduct(GROWTH_PAYING, hrms, "k2"))).toBe("already_subscribed");
  });

  it("starts a trial by default, shows it on the next read, and replays a repeated key", () => {
    const version = addProductOptionsForOrg(GROWTH_PAYING).products.find((p) => p.product_key === "crm")?.versions[0];
    if (!version) throw new Error("no crm version");
    const request = {
      product_key: "crm",
      plan_version_id: version.plan_version_id,
      trial: true,
      acknowledge_immediate_charge: false,
      reason: "r",
    };
    const first = applyFixtureAddProduct(GROWTH_PAYING, request, "key-1");
    expect(first.state).toBe("trial");
    expect(first.charge_claimed).toBe(false);
    const sub = pricingForOrg(GROWTH_PAYING)?.products.find((p) => p.product_key === "crm")?.subscription;
    expect(sub?.state).toBe("trial");
    expect(sub?.trial_max_ends_at).not.toBeNull();

    expect(applyFixtureAddProduct(GROWTH_PAYING, request, "key-1")).toEqual({ ...first, replayed: true });
    expect(codeOf(() => applyFixtureAddProduct(GROWTH_PAYING, { ...request, trial: false, acknowledge_immediate_charge: true }, "key-1"))).toBe(
      "idempotency_key_reused",
    );
    expect(addProductOptionsForOrg(GROWTH_PAYING).products.map((p) => p.product_key)).not.toContain("crm");
  });
});

describe("extend trial", () => {
  function trialSub() {
    const sub = pricingForOrg(TRIAL)?.products.find((p) => p.product_key === "crm")?.subscription;
    if (!sub?.trial_ends_at || !sub.trial_max_ends_at) throw new Error("no trial");
    return sub;
  }

  it("moves the end, is guarded by the end the operator saw, and replays", () => {
    const sub = trialSub();
    const target = new Date(Date.now() + 5 * 86_400_000).toISOString();
    const request = { new_trial_ends_at: target, expected_trial_ends_at: sub.trial_ends_at ?? "", reason: "r" };
    const result = applyFixtureExtendTrial(TRIAL, "crm", request, "key-1");
    expect(result.trial_ends_at).toBe(target);
    expect(trialSub().trial_ends_at).toBe(target);
    expect(applyFixtureExtendTrial(TRIAL, "crm", request, "key-1").replayed).toBe(true);
    // A new key with the old view is a stale confirm, never a second extension.
    expect(codeOf(() => applyFixtureExtendTrial(TRIAL, "crm", request, "key-2"))).toBe("state_changed");
  });

  it("refuses past the cap, an end that is not later, and a subscription that is not trialling", () => {
    const sub = trialSub();
    const expected = sub.trial_ends_at ?? "";
    const pastCap = new Date(Date.parse(sub.trial_max_ends_at ?? "") + 1000).toISOString();
    expect(
      codeOf(() => applyFixtureExtendTrial(TRIAL, "crm", { new_trial_ends_at: pastCap, expected_trial_ends_at: expected, reason: "r" }, "a")),
    ).toBe("trial_cap");
    expect(
      codeOf(() => applyFixtureExtendTrial(TRIAL, "crm", { new_trial_ends_at: expected, expected_trial_ends_at: expected, reason: "r" }, "b")),
    ).toBe("trial_not_extended");
    const paying = pricingForOrg(GROWTH_PAYING)?.products.find((p) => p.product_key === "hrms")?.subscription;
    expect(
      codeOf(() =>
        applyFixtureExtendTrial(
          GROWTH_PAYING,
          "hrms",
          { new_trial_ends_at: pastCap, expected_trial_ends_at: paying?.trial_ends_at ?? "", reason: "r" },
          "c",
        ),
      ),
    ).not.toBeNull();
  });
});
