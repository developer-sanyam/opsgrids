import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Toaster } from "@opsgrids/ui";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { formatDate } from "@/lib/money";

import { MarketplacePage } from "./Marketplace";

vi.mock("@/api", () => ({
  listProducts: vi.fn(),
  listActivations: vi.fn(),
  listSubscriptions: vi.fn(),
  listSubscriptionPlanSummaries: vi.fn(),
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

function arrange(overrides: Partial<{ subscriptions: unknown[]; activations: unknown[]; summaries: unknown[] }> = {}) {
  vi.mocked(api.listProducts).mockResolvedValue([inventory, soon, platform] as never);
  vi.mocked(api.listActivations).mockResolvedValue((overrides.activations ?? []) as never);
  vi.mocked(api.listSubscriptions).mockResolvedValue((overrides.subscriptions ?? []) as never);
  vi.mocked(api.listSubscriptionPlanSummaries).mockResolvedValue((overrides.summaries ?? []) as never);
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
  it("lists sellable products, hides platform ones, marks soon ones, and never shows a catalogue price", async () => {
    arrange();
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Inventory" })).toBeTruthy());
    expect(screen.getByText("Free 14-day trial")).toBeTruthy();
    // The trial converts by itself, so the card says so before anyone starts one.
    expect(
      screen.getByText(
        "When a trial ends, it continues as a paid subscription at the price agreed for your organisation, plus GST, unless you cancel it before then on the Subscriptions page.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/Pricing is agreed with OpsGrids for your organisation — contact info@opsgrids\.com\./),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Email us" }).getAttribute("href")).toBe("mailto:info@opsgrids.com");
    expect(screen.queryByText(/₹2,499\.00/)).toBeNull();
    expect(screen.queryByText(/per month/)).toBeNull();
    expect(screen.queryByText(/flat/)).toBeNull();
    expect(screen.getByRole("heading", { name: "People" })).toBeTruthy();
    expect(screen.getByText("Coming soon")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Notifications" })).toBeNull();
    // Only the available product with a published plan version can be started.
    expect(screen.getAllByRole("button", { name: "Start 14-day trial" })).toHaveLength(1);
  });

  it("a product with no published plan version shows a disabled state instead of a price", async () => {
    vi.mocked(api.listProducts).mockResolvedValue([inventory] as never);
    vi.mocked(api.listActivations).mockResolvedValue([] as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([] as never);
    vi.mocked(api.listSubscriptionPlanSummaries).mockResolvedValue([] as never);
    vi.mocked(api.listPlans).mockResolvedValue([] as never);

    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Inventory" })).toBeTruthy());
    expect(screen.getByText("Not available to start yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
  });

  it("starting a trial subscribes to the published version — the subscription is the purchase", async () => {
    const user = userEvent.setup();
    arrange();
    render(
      <MemoryRouter>
        <Toaster>
          <MarketplacePage />
        </Toaster>
      </MemoryRouter>,
    );
    await waitFor(() => screen.getByRole("button", { name: "Start 14-day trial" }));
    await user.click(screen.getByRole("button", { name: "Start 14-day trial" }));

    await waitFor(() => expect(api.startTrial).toHaveBeenCalledWith("operations", "ver-1"));
    // The result is a toast (announced), not a banner at the top of a page the card may be far below.
    // The Toaster renders the visible toast and a polite live region carrying the same sentence.
    const status = (await screen.findAllByText(/14-day trial begins now/))[0]?.textContent ?? "";
    expect(status).toContain("14-day trial begins now");
    expect(status).toContain(`When the trial ends on ${formatDate("2026-10-04T00:00:00Z")}, it continues as a paid subscription`);
  });

  /** A published, flat-priced version as the customer plan read returns it (no amount). */
  const version = (id: string, version_no: number, billing_interval: string, restricted_to_org_id: string | null = null) => ({
    id,
    version_no,
    state: "published",
    billing_interval,
    currency: "INR",
    restricted_to_org_id,
    published_at: "2026-10-06T00:00:00Z",
    prices: [{ id: `${id}-p`, kind: "flat", amount_minor: null, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }],
    features: [],
  });

  it("a re-priced product starts its trial on the newest public monthly version, never the old or the yearly one", async () => {
    // The catalogue after the 2026-10-06 list prices: v1 (the old monthly price) stays published
    // for everyone already pinned to it, v2 is the new yearly price, v3 the new monthly one — and
    // a negotiated v4 for this organisation is not a list price.
    const repriced = [
      {
        ...plans[0],
        versions: [version("ver-1", 1, "month"), version("ver-2", 2, "year"), version("ver-3", 3, "month"), version("ver-4", 4, "month", "org-1")],
      },
    ];
    const user = userEvent.setup();
    arrange();
    vi.mocked(api.listPlans).mockResolvedValue(repriced as never);
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: "Start 14-day trial" }));

    await waitFor(() => expect(api.startTrial).toHaveBeenCalledWith("operations", "ver-3"));
  });

  it("a plan with no public monthly version starts on its newest flat version, which the server still offers", async () => {
    // The subscribe write refuses a superseded version (`not_offerable`), so the oldest of two
    // yearly versions would only fail to start.
    const yearlyOnly = [{ ...plans[0], versions: [version("ver-1", 1, "year"), version("ver-2", 2, "year")] }];
    const user = userEvent.setup();
    arrange();
    vi.mocked(api.listPlans).mockResolvedValue(yearlyOnly as never);
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: "Start 14-day trial" }));

    await waitFor(() => expect(api.startTrial).toHaveBeenCalledWith("operations", "ver-2"));
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
    expect(link.getAttribute("href")).toBe("https://ops.opsgrids.com");
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
    expect(screen.getByText(/On trial/).textContent).toContain("days left");
  });

  it("an active subscription shows active plan name and renewal info without trial terms", async () => {
    arrange({
      subscriptions: [
        {
          id: "sub-1",
          product_key: "operations",
          plan_version_id: "ver-1",
          state: "active",
          trial_ends_at: null,
          current_period_start: "2026-10-01T00:00:00Z",
          current_period_end: "2026-11-01T00:00:00Z",
          cancel_at: null,
          currency: "INR",
        },
      ],
      activations: [
        {
          product_key: "operations",
          status: "active",
          entitles: true,
          activated_at: "2026-09-20T00:00:00Z",
          trial_ends_at: null,
          deactivated_at: null,
        },
      ],
      summaries: [
        {
          subscription_id: "sub-1",
          current: {
            plan_version_id: "ver-1",
            plan_id: "plan-1",
            plan_name: "Operations Growth Plan",
            version_no: 1,
            billing_interval: "month",
            negotiated: false,
            amount_visible: false,
            currency: "INR",
            recurring_minor: null,
            prices: null,
          },
          pending: null,
          pending_effective_at: null,
          pending_scheduled_by: null,
        },
      ],
    });
    render(
      <MemoryRouter>
        <MarketplacePage />
      </MemoryRouter>,
    );
    const link = await screen.findByRole("link", { name: "Open Inventory" });
    expect(link.getAttribute("href")).toBe("https://ops.opsgrids.com");
    expect(screen.getByText("Operations Growth Plan")).toBeTruthy();
    expect(screen.getByText(/Active Plan/)).toBeTruthy();
    expect(screen.getByText(/Renews 1 Nov 2026/)).toBeTruthy();
    // Must NOT show trial boilerplate
    expect(screen.queryByText("Free 14-day trial")).toBeNull();
    expect(screen.queryByText(/When a trial ends/)).toBeNull();
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
    vi.mocked(api.listSubscriptionPlanSummaries).mockResolvedValue([] as never);
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
    expect(screen.getByRole("link", { name: "Open Inventory" }).getAttribute("href")).toBe("https://ops.opsgrids.com");
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
    expect(screen.getByText("Each product it includes opens from its own card.")).toBeTruthy();
  });
});
