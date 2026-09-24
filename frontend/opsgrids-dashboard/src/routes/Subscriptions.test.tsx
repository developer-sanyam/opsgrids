import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("SubscriptionsPage — change plan", () => {
  it("offers a plan to change to by name only, with no catalogue price shown anywhere", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listProducts).mockResolvedValue([
      { key: "operations", name: "OpsGrids Inventory", is_bundle: false },
    ] as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      { ...base, id: "s1", product_key: "operations", plan_version_id: "ver-1", entitled_products: ["operations"] },
    ] as never);
    vi.mocked(api.listPlans).mockResolvedValue([
      {
        id: "plan-1",
        product_key: "operations",
        code: "pro",
        name: "Inventory Pro",
        state: "active",
        sort_order: 10,
        versions: [
          {
            id: "ver-2",
            version_no: 2,
            state: "published",
            billing_interval: "month",
            currency: "INR",
            restricted_to_org_id: null,
            published_at: "2026-09-20T00:00:00Z",
            prices: [
              { id: "p1", kind: "flat", amount_minor: 499900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
            ],
            features: [],
          },
        ],
      },
    ] as never);

    render(
      <PermissionProvider grants={["core.product.activation.write:ORG"]}>
        <SubscriptionsPage />
      </PermissionProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Change plan Inventory" }));
    expect(await screen.findByRole("option", { name: "Inventory Pro" })).toBeTruthy();
    expect(screen.queryByText(/₹4,999\.00/)).toBeNull();
    expect(screen.queryByText(/per month/)).toBeNull();
    expect(screen.queryByText(/flat/)).toBeNull();
  });
});
