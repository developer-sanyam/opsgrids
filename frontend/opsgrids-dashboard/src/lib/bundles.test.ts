import { describe, expect, it } from "vitest";

import type { PlanVersion, Product, Subscription } from "@/api/types";
import { bundleMembers, bundlesIncluding, joinNames, productNames } from "./bundles";

const version = (features: PlanVersion["features"]): PlanVersion => ({
  id: "v",
  version_no: 1,
  state: "published",
  billing_interval: "month",
  restricted_to_org_id: null,
  published_at: null,
  prices: [],
  features,
});

const subscription = (over: Partial<Subscription>): Subscription => ({
  id: "s",
  product_key: "suite",
  plan_version_id: "v",
  state: "active",
  trial_ends_at: null,
  current_period_start: null,
  current_period_end: null,
  cancel_at: null,
  currency: "INR",
  entitled_products: ["hrms", "crm", "finance"],
  items: [],
  dunning: null,
  ...over,
});

describe("bundleMembers", () => {
  it("reads the members a bundle version declares", () => {
    expect(bundleMembers(version([{ feature_key: "bundled_products", value: ["hrms", "crm"] }]))).toEqual([
      "hrms",
      "crm",
    ]);
  });

  it("is empty for a version that is not a bundle, or a malformed declaration", () => {
    expect(bundleMembers(version([{ feature_key: "seats", value: 10 }]))).toEqual([]);
    expect(bundleMembers(version([{ feature_key: "bundled_products", value: "hrms,crm" }]))).toEqual([]);
  });
});

describe("bundlesIncluding", () => {
  it("finds the live bundles that include a product, never the product's own subscription", () => {
    const suite = subscription({});
    const own = subscription({ id: "own", product_key: "hrms", entitled_products: ["hrms"] });
    expect(bundlesIncluding("hrms", [suite, own])).toEqual([suite]);
    expect(bundlesIncluding("projects", [suite, own])).toEqual([]);
  });

  it("ignores a cancelled bundle", () => {
    expect(bundlesIncluding("hrms", [subscription({ state: "cancelled" })])).toEqual([]);
  });

  it("only an active or trialling bundle marks its members included", () => {
    for (const state of ["suspended", "past_due"]) {
      expect(bundlesIncluding("hrms", [subscription({ state })])).toEqual([]);
    }
    expect(bundlesIncluding("hrms", [subscription({ state: "trial" })])).toHaveLength(1);
  });
});

describe("names", () => {
  const products = [{ key: "hrms", name: "OpsGrids People" }, { key: "crm", name: "OpsGrids Revenue" }] as Product[];

  it("names keys the way the dashboard does, and leaves unknown keys as they are", () => {
    expect(productNames(["hrms", "crm", "mystery"], products)).toEqual(["People", "Revenue", "mystery"]);
  });

  it("joins a list the way a sentence does", () => {
    expect(joinNames([])).toBe("");
    expect(joinNames(["People"])).toBe("People");
    expect(joinNames(["People", "Revenue", "Finance"])).toBe("People, Revenue and Finance");
  });
});
