import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { AuditOut, MetricsOverviewOut, MetricsSeriesOut, OrganizationsOut, OrgStateOut, SubscriptionsOut } from "@/api/types";
import { OverviewPage } from "./Overview";

/**
 * Rendered against the real `/admin/organizations`, `/admin/metrics/overview`,
 * `/admin/metrics/series`, `/admin/subscriptions` and `/admin/audit` shapes
 * (`services/console/app/schemas.py`), including the fields tonight's backend batch added
 * (`lifecycle`, `country`, `created_at`, `user_count`, `product_name`/`plan_name`) and the null
 * cases those contracts are explicit about (a `SeriesPointOut.value` before console's tracking
 * window began, a `user_count` account-service couldn't answer for).
 */

function mockAuth(roles: string[]): AuthContextValue {
  return {
    status: "authenticated",
    operator: { staff_id: "op_1", email: "op@opsgrids.example", display_name: "Op", operator_roles: roles, amr: ["pwd", "otp"] },
    login: vi.fn(),
    beginEnrolment: vi.fn(),
    confirmEnrolment: vi.fn(),
    finishEnrolment: vi.fn(),
    verifyMfa: vi.fn(),
    cancelSignIn: vi.fn(),
    logout: vi.fn(),
    hasRole: (role) => roles.includes(role),
  };
}

function renderOverview(roles: string[] = ["owner"]) {
  vi.spyOn(authContext, "useAuth").mockReturnValue(mockAuth(roles));
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

function org(overrides: Partial<OrgStateOut> = {}): OrgStateOut {
  return {
    organization_id: "org_a1f09c",
    name: "Northwind Metals",
    slug: "northwind-metals",
    country: "IN",
    created_at: "2024-02-11T00:00:00Z",
    lifecycle: "active",
    mrr_minor: 420_000_00,
    currency: "INR",
    active_products: ["trading"],
    subscription_states: {},
    trial_ends_at: null,
    locked: false,
    updated_at: "2026-08-23T14:20:00Z",
    user_count: 45,
    ...overrides,
  };
}

const METRICS_OUT = {
  mrr_minor: 176_00000,
  currency: "INR",
  trials_started: 12,
  trial_conversions: 8,
  trial_conversion_rate: 0.6667,
  active_subscriptions: 11,
  churned: 1,
  churn_rate: 0.0833,
};

function organizationsOut(organizations: OrgStateOut[]): OrganizationsOut {
  return { organizations, metrics: METRICS_OUT };
}

function overviewOut(overrides: Partial<MetricsOverviewOut> = {}): MetricsOverviewOut {
  return {
    ...METRICS_OUT,
    organization_count: 1,
    organizations_by_lifecycle: { suspended: 0, active: 1, trial: 0, churned: 0, none: 0 },
    ...overrides,
  };
}

function seriesOut(overrides: Partial<MetricsSeriesOut> = {}): MetricsSeriesOut {
  const periods = ["2026-07", "2026-08", "2026-09"];
  const flat = (value: number) => periods.map((period) => ({ period, value }));
  return {
    currency: "INR",
    mrr_minor: flat(420_000_00),
    new_organizations: flat(1),
    trials_started: flat(0),
    trial_conversions: flat(0),
    churned: flat(0),
    ...overrides,
  };
}

function mockAllSuccess(options: { organizations?: OrgStateOut[]; subscriptions?: SubscriptionsOut["subscriptions"]; audit?: AuditOut[] } = {}) {
  const organizations = options.organizations ?? [org()];
  vi.spyOn(client, "listOrganizations").mockResolvedValue(organizationsOut(organizations));
  vi.spyOn(client, "getOverview").mockResolvedValue(overviewOut());
  vi.spyOn(client, "getMetricsSeries").mockResolvedValue(seriesOut());
  vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: options.subscriptions ?? [] });
  vi.spyOn(client, "listAudit").mockResolvedValue({ audit: options.audit ?? [] });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OverviewPage — populated", () => {
  it("renders the header copy approved for this dashboard", async () => {
    mockAllSuccess();
    renderOverview();
    // The full title and the description are the `sm:`-and-up copy — the phone header (mock-1)
    // shortens the title to "Super Admin" and drops the description, both by CSS class rather
    // than a second render, so both texts are present in the tree at every width.
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /Super Admin/ })).toBeTruthy());
    expect(screen.getByText("Manage all accounts, monitor usage, and oversee the platform.")).toBeTruthy();
  });

  it("renders every KPI from real payloads, with a real delta only where the series has one", async () => {
    mockAllSuccess({
      organizations: [org(), org({ organization_id: "org_2", name: "Second Co", lifecycle: "trial", user_count: 5 })],
    });
    renderOverview();

    const metrics = await screen.findByRole("region", { name: "Key metrics" });
    await waitFor(() => expect(within(metrics).getByText("Total Accounts")).toBeTruthy());
    expect(within(metrics).getAllByText("2").length).toBeGreaterThan(0); // Total Accounts
    expect(within(metrics).getByText("Active Accounts")).toBeTruthy();
    expect(within(metrics).getByText("Trial Accounts")).toBeTruthy();
    expect(within(metrics).getByText("Suspended Accounts")).toBeTruthy();
    expect(within(metrics).getByText("Total Users")).toBeTruthy();
    expect(within(metrics).getByText("Monthly Revenue")).toBeTruthy();
    // vs-last-month delta comes only from the series — never a fabricated "+0%".
    expect(within(metrics).getAllByText("vs last month").length).toBeGreaterThan(0);
  });

  it("renders the All Accounts table with the real columns", async () => {
    mockAllSuccess({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          name: "Northwind Metals",
          slug: "northwind-metals",
          product_key: "trading",
          product_name: "OpsGrids Trading Desk",
          state: "active",
          plan_version_id: "pv_1",
          plan_name: "Enterprise",
          amount_minor: 420_000_00,
          currency: "INR",
          trial_ends_at: null,
        },
      ],
    });
    renderOverview();

    await waitFor(() => expect(screen.getByText("Northwind Metals")).toBeTruthy());
    // The slug shows both under the company name and in its own Domain column — by design.
    expect(screen.getAllByText("northwind-metals").length).toBeGreaterThan(0);
    expect(screen.getByText("OpsGrids Trading Desk · Enterprise")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Every account on the platform" });
    expect(within(table).getByText("Active")).toBeTruthy();
  });

  it("shows the Revenue by Plan chart populated from paying subscriptions", async () => {
    mockAllSuccess({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          name: "Northwind Metals",
          slug: "northwind-metals",
          product_key: "trading",
          product_name: "OpsGrids Trading Desk",
          state: "active",
          plan_version_id: "pv_1",
          plan_name: "Enterprise",
          amount_minor: 420_000_00,
          currency: "INR",
          trial_ends_at: null,
        },
      ],
    });
    renderOverview();
    await waitFor(() => expect(screen.getByText("Revenue by Plan")).toBeTruthy());
    expect(screen.getAllByText("OpsGrids Trading Desk").length).toBeGreaterThan(0);
  });
});

describe("OverviewPage — empty", () => {
  it("shows zeroed KPIs and the table's empty state for an org list with no rows", async () => {
    mockAllSuccess({ organizations: [] });
    renderOverview();

    const metrics = await screen.findByRole("region", { name: "Key metrics" });
    await waitFor(() => expect(within(metrics).getAllByText("0").length).toBeGreaterThan(0));
    expect(await screen.findByText("No matching accounts")).toBeTruthy();
  });

  it("renders the exact approved empty state when every subscription is still a trial", async () => {
    mockAllSuccess({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          name: "Northwind Metals",
          slug: "northwind-metals",
          product_key: "trading",
          product_name: "OpsGrids Trading Desk",
          state: "trial",
          plan_version_id: "pv_1",
          plan_name: "Enterprise",
          amount_minor: 0,
          currency: "INR",
          trial_ends_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    renderOverview();
    await waitFor(() => expect(screen.getByText("No paying subscriptions yet")).toBeTruthy());
  });

  it("shows the empty state for no recent activity and no trials ending soon", async () => {
    mockAllSuccess({ organizations: [org({ trial_ends_at: null })], audit: [] });
    renderOverview();
    await waitFor(() => expect(screen.getByText("No activity yet")).toBeTruthy());
    expect(screen.getByText("No trials ending soon")).toBeTruthy();
  });
});

describe("OverviewPage — error", () => {
  it("shows a contained error, not a crash, when the accounts list fails", async () => {
    vi.spyOn(client, "listOrganizations").mockRejectedValue(new Error("Request failed with 503"));
    vi.spyOn(client, "getOverview").mockResolvedValue(overviewOut());
    vi.spyOn(client, "getMetricsSeries").mockResolvedValue(seriesOut());
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: [] });
    vi.spyOn(client, "listAudit").mockResolvedValue({ audit: [] });
    renderOverview();
    await waitFor(() => expect(screen.getAllByText(/something went wrong on our side/i).length).toBeGreaterThan(0));
  });

  it("shows a contained error on the revenue tile when the billing-gated call fails", async () => {
    mockAllSuccess();
    vi.spyOn(client, "getOverview").mockRejectedValue(new Error("Request failed with 500"));
    renderOverview();
    await waitFor(() => expect(screen.getByText(/something went wrong on our side/i)).toBeTruthy());
  });
});

describe("OverviewPage — loading", () => {
  it("renders skeletons with no crash while every call is in flight", () => {
    vi.spyOn(client, "listOrganizations").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "getOverview").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "getMetricsSeries").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listSubscriptions").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listAudit").mockReturnValue(new Promise(() => {}));
    renderOverview();
    expect(screen.getByRole("heading", { level: 1, name: /Super Admin/ })).toBeTruthy();
  });
});

describe("OverviewPage — permission gates", () => {
  it("never calls the billing-gated routes for a support operator, and shows Restricted", async () => {
    const overviewSpy = vi.spyOn(client, "getOverview");
    const seriesSpy = vi.spyOn(client, "getMetricsSeries");
    const subsSpy = vi.spyOn(client, "listSubscriptions");
    vi.spyOn(client, "listOrganizations").mockResolvedValue(organizationsOut([org()]));
    vi.spyOn(client, "listAudit").mockResolvedValue({ audit: [] });

    renderOverview(["support"]);

    // Restricted on the Monthly Revenue tile, the Account Growth chart and the Revenue by Plan
    // chart — every surface behind `owner`/`billing_ops`.
    await waitFor(() => expect(screen.getAllByText("Restricted").length).toBeGreaterThanOrEqual(3));
    expect(overviewSpy).not.toHaveBeenCalled();
    expect(seriesSpy).not.toHaveBeenCalled();
    expect(subsSpy).not.toHaveBeenCalled();
    // Support does hold the audit role — Recent Account Activity still renders.
    expect(screen.getByText("No activity yet")).toBeTruthy();
  });

  it("never calls the audit-gated route for a billing_ops operator, and shows Restricted there instead", async () => {
    const auditSpy = vi.spyOn(client, "listAudit");
    vi.spyOn(client, "listOrganizations").mockResolvedValue(organizationsOut([org()]));
    vi.spyOn(client, "getOverview").mockResolvedValue(overviewOut());
    vi.spyOn(client, "getMetricsSeries").mockResolvedValue(seriesOut());
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: [] });

    renderOverview(["billing_ops"]);

    await waitFor(() => expect(screen.getByText("Monthly Revenue")).toBeTruthy());
    expect(auditSpy).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByText("Recent activity needs the owner or support role.")).toBeTruthy(),
    );
  });
});
