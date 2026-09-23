import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { MarketplacePage } from "./Marketplace";

vi.mock("@/api", () => ({
  listProducts: vi.fn(),
  listActivations: vi.fn(),
  listSubscriptions: vi.fn(),
  listPlans: vi.fn(),
  startTrial: vi.fn(),
}));

import * as api from "@/api";

const inventory = {
  key: "operations",
  name: "OpsGrids Inventory",
  tagline: "Suppliers, purchase orders and stock on hand",
  description: null,
  category: "Operations",
  status: "available",
  connects_to: [],
  features: [],
  is_platform: false,
};

const soon = { ...inventory, key: "hrms", name: "OpsGrids People", status: "soon", category: "People" };
const platform = { ...inventory, key: "notification", name: "Notifications", is_platform: true };

const plans = [
  {
    id: "plan-1",
    product_key: "operations",
    code: "standard",
    name: "Inventory",
    state: "active",
    sort_order: 10,
    versions: [
      {
        id: "ver-1",
        version_no: 1,
        state: "published",
        billing_interval: "month",
        currency: "INR",
        restricted_to_org_id: null,
        published_at: "2026-09-20T00:00:00Z",
        prices: [{ id: "p1", kind: "flat", amount_minor: 249900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }],
      },
    ],
  },
];

function arrange(overrides: Partial<{ subscriptions: unknown[]; activations: unknown[] }> = {}) {
  vi.mocked(api.listProducts).mockResolvedValue([inventory, soon, platform] as never);
  vi.mocked(api.listActivations).mockResolvedValue((overrides.activations ?? []) as never);
  vi.mocked(api.listSubscriptions).mockResolvedValue((overrides.subscriptions ?? []) as never);
  vi.mocked(api.listPlans).mockResolvedValue(plans as never);
  vi.mocked(api.startTrial).mockResolvedValue({
    id: "sub-1",
    product_key: "operations",
    plan_version_id: "ver-1",
    state: "trial",
    trial_ends_at: "2026-10-04T00:00:00Z",
    current_period_start: null,
    current_period_end: null,
    cancel_at: null,
    currency: "INR",
  } as never);
}

describe("MarketplacePage", () => {
  it("lists sellable products with the flat monthly price, hides platform ones, marks soon ones", async () => {
    arrange();
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Inventory" })).toBeTruthy());
    expect(screen.getByText(/₹2,499\.00/)).toBeTruthy();
    expect(screen.getByText(/per month, flat/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "People" })).toBeTruthy();
    expect(screen.getByText("Coming soon")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Notifications" })).toBeNull();
    // Only the available product with a price can be started.
    expect(screen.getAllByRole("button", { name: "Start 14-day trial" })).toHaveLength(1);
  });

  it("starting a trial subscribes to the published version — the subscription is the purchase", async () => {
    const user = userEvent.setup();
    arrange();
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await waitFor(() => screen.getByRole("button", { name: "Start 14-day trial" }));
    await user.click(screen.getByRole("button", { name: "Start 14-day trial" }));

    await waitFor(() => expect(api.startTrial).toHaveBeenCalledWith("operations", "ver-1"));
    expect((await screen.findByRole("status")).textContent).toContain("14-day trial begins now");
  });

  it("an entitled product on trial gets an Open link to its own host", async () => {
    arrange({
      subscriptions: [
        {
          id: "sub-1",
          product_key: "operations",
          plan_version_id: "ver-1",
          state: "trial",
          trial_ends_at: "2099-01-01T00:00:00Z",
          current_period_start: null,
          current_period_end: null,
          cancel_at: null,
          currency: "INR",
        },
      ],
      activations: [
        {
          product_key: "operations",
          status: "trial",
          entitles: true,
          activated_at: "2026-09-20T00:00:00Z",
          trial_ends_at: "2099-01-01T00:00:00Z",
          deactivated_at: null,
        },
      ],
    });
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    const link = await screen.findByRole("link", { name: "Open Inventory" });
    expect(link.getAttribute("href")).toBe("https://ops.opsgrids.com/");
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
    expect(screen.getByText(/On trial/).textContent).toContain("days left");
  });
});

describe("MarketplacePage — bundles", () => {
  const crm = { ...inventory, key: "crm", name: "OpsGrids Revenue", category: "Revenue" };
  const suite = { ...inventory, key: "suite", name: "OpsGrids Suite", category: "Bundles", is_bundle: true };
  const trioVersion = {
    id: "trio-v1",
    version_no: 1,
    state: "published",
    billing_interval: "month",
    currency: "INR",
    restricted_to_org_id: null,
    published_at: "2026-09-20T00:00:00Z",
    prices: [{ id: "p2", kind: "per_seat", amount_minor: 80700, currency: "INR", unit: "seat", unit_price_micros: null, meter_key: null }],
    features: [
      { feature_key: "bundled_products", value: ["operations", "crm"] },
      { feature_key: "bundle_tier", value: "trio" },
    ],
  };
  const suitePlans = [{ id: "plan-s", product_key: "suite", code: "trio", name: "Suite Trio", state: "active", sort_order: 31, versions: [trioVersion] }];
  const bundleSubscription = {
    id: "sub-s",
    product_key: "suite",
    plan_version_id: "trio-v1",
    state: "active",
    trial_ends_at: null,
    current_period_start: null,
    current_period_end: null,
    cancel_at: null,
    currency: "INR",
    entitled_products: ["operations", "crm"],
  };

  function arrangeBundle(subscriptions: unknown[], activations: unknown[]) {
    vi.mocked(api.listProducts).mockResolvedValue([inventory, crm, suite] as never);
    vi.mocked(api.listActivations).mockResolvedValue(activations as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue(subscriptions as never);
    vi.mocked(api.listPlans).mockImplementation(((key: string) =>
      Promise.resolve(key === "suite" ? suitePlans : plans)) as never);
  }

  it("the bundle card names each rung and its members", async () => {
    arrangeBundle([], []);
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Suite" })).toBeTruthy());
    expect(screen.getByText("Suite Trio")).toBeTruthy();
    expect(screen.getByText(/Inventory and Revenue/)).toBeTruthy();
  });

  it("a member the suite entitles says so and offers Open, not another trial", async () => {
    arrangeBundle(
      [bundleSubscription],
      [
        {
          product_key: "operations",
          status: "active",
          entitles: true,
          activated_at: "2026-09-20T00:00:00Z",
          trial_ends_at: null,
          deactivated_at: null,
          entitled_by: ["suite"],
        },
      ],
    );
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    expect((await screen.findAllByText("Included in Suite Trio")).length).toBe(2);
    expect(screen.getByRole("link", { name: "Open Inventory" }).getAttribute("href")).toBe("https://ops.opsgrids.com/");
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
    expect(screen.getByText("Each product it includes opens from its own card.")).toBeTruthy();
  });
});
