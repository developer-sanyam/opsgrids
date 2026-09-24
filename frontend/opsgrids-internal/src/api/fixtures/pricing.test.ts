import { beforeEach, describe, expect, it } from "vitest";

import { applyFixturePrice, pricingForOrg, resetPricingFixtures } from "./pricing";

const NORTHWIND = "org_a1f09c"; // bespoke trading subscription, seeded multi-entry history
const BLUEOAK = "org_07c662"; // trial crm subscription only

beforeEach(() => {
  resetPricingFixtures();
});

describe("pricingForOrg", () => {
  it("returns null for an unknown organization", () => {
    expect(pricingForOrg("org_does_not_exist")).toBeNull();
  });

  it("covers every catalog product for a known organization", () => {
    const pricing = pricingForOrg(BLUEOAK);
    expect(pricing).not.toBeNull();
    expect(pricing?.products.map((p) => p.product_key).sort()).toEqual(
      ["auctions", "crm", "hrms", "procure", "suite", "trading"].sort(),
    );
  });

  it("marks the subscribed trial product with its subscription, and everything else as none", () => {
    const pricing = pricingForOrg(BLUEOAK)!;
    const crm = pricing.products.find((p) => p.product_key === "crm")!;
    expect(crm.subscription?.state).toBe("trial");
    expect(crm.subscription?.trial_ends_at).toBe("2026-09-05T00:00:00Z");

    const hrms = pricing.products.find((p) => p.product_key === "hrms")!;
    expect(hrms.subscription).toBeNull();
    expect(hrms.current_price).toBeNull();
  });

  it("shows the bespoke subscription as custom, with a public list price for reference", () => {
    const pricing = pricingForOrg(NORTHWIND)!;
    const trading = pricing.products.find((p) => p.product_key === "trading")!;
    expect(trading.current_price?.is_bespoke).toBe(true);
    expect(trading.current_price?.amount_minor).toBe(42_000_000);
    expect(trading.public_price).not.toBeNull();
    expect(trading.public_price?.amount_minor).toBeGreaterThan(0);
  });

  it("gives a bespoke subscription's seeded history newest first available for sorting", () => {
    const pricing = pricingForOrg(NORTHWIND)!;
    const trading = pricing.products.find((p) => p.product_key === "trading")!;
    expect(trading.history.length).toBeGreaterThanOrEqual(2);
  });

  it("leaves an untouched list-priced subscription with empty history", () => {
    const pricing = pricingForOrg(BLUEOAK)!;
    const crm = pricing.products.find((p) => p.product_key === "crm")!;
    expect(crm.history).toEqual([]);
  });

  it("hands back a defensive copy — mutating the result never touches the store", () => {
    const first = pricingForOrg(BLUEOAK)!;
    first.products[0]!.history.push({
      plan_version_id: "tampered",
      amount_minor: 1,
      currency: "INR",
      billing_interval: "month",
      created_at: "2026-01-01T00:00:00Z",
      created_by_email_masked: "x***x@opsgrids.com",
      reason: "should not stick",
    });
    const second = pricingForOrg(BLUEOAK)!;
    expect(second.products[0]!.history).toEqual([]);
  });
});

describe("applyFixturePrice", () => {
  it("records a new history entry and marks the product bespoke", () => {
    const before = pricingForOrg(BLUEOAK)!.products.find((p) => p.product_key === "crm")!;
    expect(before.history).toEqual([]);

    const result = applyFixturePrice(BLUEOAK, {
      product_key: "crm",
      amount_minor: 39_900,
      currency: "INR",
      billing_interval: "month",
      reason: "Trial-to-paid conversion discount",
      apply_to_subscription: true,
    });

    expect(result.effective).toBe("now");
    expect(result.applied_to_subscription_id).not.toBeNull();

    const after = pricingForOrg(BLUEOAK)!.products.find((p) => p.product_key === "crm")!;
    expect(after.current_price?.is_bespoke).toBe(true);
    expect(after.current_price?.amount_minor).toBe(39_900);
    expect(after.history).toHaveLength(1);
    expect(after.history[0]!.reason).toBe("Trial-to-paid conversion discount");
    expect(after.subscription?.plan_version_id).toBe(result.plan_version_id);
  });

  it("does not touch the subscription when apply_to_subscription is false", () => {
    const result = applyFixturePrice(BLUEOAK, {
      product_key: "crm",
      amount_minor: 39_900,
      currency: "INR",
      billing_interval: "month",
      reason: "Quoted ahead of renewal",
      apply_to_subscription: false,
    });

    expect(result.effective).toBe("next_period");
    expect(result.applied_to_subscription_id).toBeNull();

    const after = pricingForOrg(BLUEOAK)!.products.find((p) => p.product_key === "crm")!;
    // The pinned plan version on the subscription is untouched, even though a new price exists.
    expect(after.subscription?.plan_version_id).toBe("pv_crm_growth_v2");
    expect(after.current_price?.plan_version_id).toBe(result.plan_version_id);
  });

  it("is not_subscribed for a product this org has never subscribed to", () => {
    const result = applyFixturePrice(BLUEOAK, {
      product_key: "hrms",
      amount_minor: 100_000,
      currency: "INR",
      billing_interval: "month",
      reason: "Quoted ahead of a possible HRMS add-on",
      apply_to_subscription: true,
    });

    expect(result.effective).toBe("not_subscribed");
    expect(result.applied_to_subscription_id).toBeNull();
  });

  it("prepends new history so the newest change reads first", () => {
    applyFixturePrice(BLUEOAK, {
      product_key: "crm",
      amount_minor: 39_900,
      currency: "INR",
      billing_interval: "month",
      reason: "First change",
      apply_to_subscription: true,
    });
    applyFixturePrice(BLUEOAK, {
      product_key: "crm",
      amount_minor: 44_900,
      currency: "INR",
      billing_interval: "month",
      reason: "Second change",
      apply_to_subscription: true,
    });

    const crm = pricingForOrg(BLUEOAK)!.products.find((p) => p.product_key === "crm")!;
    expect(crm.history).toHaveLength(2);
    expect(crm.history[0]!.reason).toBe("Second change");
    expect(crm.history[1]!.reason).toBe("First change");
  });
});
