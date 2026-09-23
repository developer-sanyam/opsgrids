import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import { SubscriptionsPage } from "./Subscriptions";

vi.mock("@/api", () => ({
  listSubscriptions: vi.fn(),
  listProducts: vi.fn(),
  listPlans: vi.fn(),
  cancelSubscription: vi.fn(),
  changeSubscriptionPlan: vi.fn(),
}));

import * as api from "@/api";

const base = {
  plan_version_id: "v",
  state: "active",
  trial_ends_at: null,
  current_period_start: null,
  current_period_end: null,
  cancel_at: null,
  currency: "INR",
  items: [],
  dunning: null,
};

const products = [
  { key: "suite", name: "OpsGrids Suite", is_bundle: true },
  { key: "hrms", name: "OpsGrids People", is_bundle: false },
  { key: "crm", name: "OpsGrids Revenue", is_bundle: false },
  { key: "finance", name: "OpsGrids Finance", is_bundle: false },
];

describe("SubscriptionsPage — bundles", () => {
  it("a bundle lists what it includes, and a standalone product says the bundle also covers it", async () => {
    vi.mocked(api.listProducts).mockResolvedValue(products as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      { ...base, id: "s1", product_key: "hrms", entitled_products: ["hrms"] },
      { ...base, id: "s2", product_key: "suite", entitled_products: ["hrms", "crm", "finance"] },
    ] as never);

    render(
      <PermissionProvider grants={["core.product.activation.read:ORG"]}>
        <SubscriptionsPage />
      </PermissionProvider>,
    );

    expect(await screen.findByText("Includes People, Revenue and Finance")).toBeTruthy();
    expect(screen.getByText("Also included in Suite")).toBeTruthy();
  });
});
