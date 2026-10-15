import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import { HomePage } from "./Home";

vi.mock("@/api", () => ({
  getFinanceOverview: vi.fn(),
  listFinanceInvoices: vi.fn(),
  listFinanceCustomers: vi.fn(),
  countCrmCustomers: vi.fn(),
  listDealActivities: vi.fn(),
  listMyTasks: vi.fn(),
}));

import * as api from "@/api";

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "user-1",
    email: "sanyam@acme.example",
    full_name: "Sanyam Jain",
    organization_id: "org-1",
    membership_id: "member-1",
    roles: ["ORG_OWNER"],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [{ organization_id: "org-1", slug: "acme", name: "Acme Industries", roles: ["ORG_OWNER"] }],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const OWNER_GRANTS = [
  "finance.reports.summary.read:ORG",
  "finance.billing.invoice.read:ORG",
  "finance.billing.customer.read:ORG",
  "crm.book.company.read:ORG",
  "crm.pipeline.activity.read:ORG",
  "projects.board.task.read:ORG",
  "core.product.activation.write:ORG",
];
const ALL_PRODUCTS = ["finance", "crm", "projects"];

function page<T>(items: T[], total = items.length) {
  return { items, total, page: 1, size: Math.max(items.length, 1), pages: 1 };
}

function months() {
  const labels = ["Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026", "May 2026", "Jun 2026", "Jul 2026", "Aug 2026", "Sep 2026"];
  return labels.map((label, index) => ({
    month: `2026-${String(index + 1).padStart(2, "0")}`,
    label,
    revenue_minor: 90_000_00 + index * 5_000_00,
    received_minor: 80_000_00 + index * 4_000_00,
    outstanding_minor: 10_000_00,
  }));
}

const FINANCE_OVERVIEW = {
  period_label: "September 2026",
  revenue_history: [
    {
      currency: "INR",
      revenue_current_minor: 1_248_320_00,
      revenue_previous_minor: 1_114_500_00,
      revenue_delta_pct: 12.0,
      received_current_minor: 824_560_00,
      received_previous_minor: 800_000_00,
      received_delta_pct: 3.1,
      outstanding_current_minor: 248_320_00,
      outstanding_previous_minor: 200_000_00,
      outstanding_delta_pct: 24.1,
      months: months(),
    },
  ],
};

const RECENT_INVOICE = {
  id: "inv-1",
  number: "INV-8264",
  customer_id: "cust-1",
  issued_date: "2026-09-20",
  status: "paid",
  currency: "INR",
  total_minor: 12_000_00,
};

function arrangeInvoices(overrides: { recent?: unknown[]; counts?: Partial<Record<string, number>> } = {}) {
  const counts: Record<string, number> = { draft: 12, sent: 30, paid: 120, overdue: 6, void: 0, ...overrides.counts };
  vi.mocked(api.listFinanceInvoices).mockImplementation(async (params: { status?: string; size: number }) => {
    if (params.status) return page([], counts[params.status] ?? 0);
    return page((overrides.recent ?? [RECENT_INVOICE]) as never[]);
  });
}

const TASK = {
  id: "task-1",
  title: "Review design mockups",
  stage: "in_progress" as const,
  priority: "high" as const,
  due_date: "2026-09-26",
  project_code: "ERP",
};

function renderHome(options: { grants?: string[] | null; entitlements?: string[] | null } = {}) {
  const grants = options.grants === undefined ? OWNER_GRANTS : options.grants;
  const entitlements = options.entitlements === undefined ? ALL_PRODUCTS : options.entitlements;
  return render(
    <MemoryRouter>
      <AuthContext value={AUTH}>
        <PermissionProvider grants={grants} entitlements={entitlements}>
          <HomePage />
        </PermissionProvider>
      </AuthContext>
    </MemoryRouter>,
  );
}

describe("HomePage — populated", () => {
  it("shows the greeting and every widget's real-shaped figures", async () => {
    vi.mocked(api.getFinanceOverview).mockResolvedValue(FINANCE_OVERVIEW as never);
    arrangeInvoices();
    vi.mocked(api.listFinanceCustomers).mockResolvedValue(page([{ customer: { id: "cust-1", name: "Rohit Sharma" } }]) as never);
    vi.mocked(api.countCrmCustomers).mockResolvedValue(page([], 856) as never);
    vi.mocked(api.listMyTasks).mockResolvedValue(page([TASK]) as never);
    vi.mocked(api.listDealActivities).mockResolvedValue(
      page([{ id: "act-1", subject: "Called about renewal", occurred_at: "2026-09-24T10:00:00Z" }]) as never,
    );

    renderHome();

    expect(await screen.findByRole("heading", { name: /Sanyam/ })).toBeTruthy();
    expect(screen.getByText("Here's what's happening with your business today.")).toBeTruthy();

    // Total Revenue — real current-month figure, grouped like the phone hero (this card had only
    // a default-sized MetricCard's width, not the phone hero's full row), and a real delta because
    // it is non-null.
    expect(await screen.findByText("Total Revenue")).toBeTruthy();
    expect(screen.getByText("₹12,48,320")).toBeTruthy();
    expect(screen.getByText("vs last month")).toBeTruthy();

    // Total Invoices — summed from the five real status totals (12+30+120+6+0), never a scan.
    // The same total is also the donut's own centre figure, so it appears twice.
    await waitFor(() => expect(screen.getAllByText("168")).toHaveLength(2));

    // Total Customers and Pending Tasks.
    expect(await screen.findByText("856")).toBeTruthy();
    expect(await screen.findByText("Review design mockups")).toBeTruthy();

    // Recent Invoices resolves the customer id to the finance directory's name.
    expect(await screen.findByText("Rohit Sharma")).toBeTruthy();
    expect(screen.getByText("INV-8264")).toBeTruthy();

    // Recent Activity merges the finance and CRM sources.
    expect(await screen.findByText("Called about renewal")).toBeTruthy();
  });
});

describe("HomePage — empty", () => {
  it("shows every widget's empty state on real zeros, never a hardcoded figure", async () => {
    vi.mocked(api.getFinanceOverview).mockResolvedValue({ period_label: "September 2026", revenue_history: [] } as never);
    arrangeInvoices({ recent: [], counts: { draft: 0, sent: 0, paid: 0, overdue: 0, void: 0 } });
    vi.mocked(api.listFinanceCustomers).mockResolvedValue(page([]) as never);
    vi.mocked(api.countCrmCustomers).mockResolvedValue(page([], 0) as never);
    vi.mocked(api.listMyTasks).mockResolvedValue(page([]) as never);
    vi.mocked(api.listDealActivities).mockResolvedValue(page([]) as never);

    renderHome();

    // Every KPI is a real zero, never omitted and never a placeholder — one per card.
    await waitFor(() => expect(screen.getAllByText("0")).toHaveLength(4));
    expect(await screen.findByText("No revenue history yet")).toBeTruthy();
    expect(await screen.findByText("Invoices by status appears once you've issued an invoice.")).toBeTruthy();
    // "No invoices yet" is the honest headline for both the donut and the table below it.
    expect(await screen.findAllByText("No invoices yet")).toHaveLength(2);
    expect(await screen.findByText("Nothing pending")).toBeTruthy();
  });
});

describe("HomePage — plan-gated", () => {
  it("shows the not-active state and never calls the product's own API", async () => {
    renderHome({ entitlements: [] });

    const notices = await screen.findAllByText("Finance isn't active for your organisation.");
    expect(notices.length).toBeGreaterThan(0);
    expect(screen.getAllByText("CRM isn't active for your organisation.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Projects isn't active for your organisation.").length).toBeGreaterThan(0);
    // An owner (`core.product.activation.write:ORG`) gets the trial action.
    expect(screen.getAllByRole("button", { name: "Start 14-day trial" }).length).toBeGreaterThan(0);

    expect(api.getFinanceOverview).not.toHaveBeenCalled();
    expect(api.listFinanceInvoices).not.toHaveBeenCalled();
    expect(api.countCrmCustomers).not.toHaveBeenCalled();
    expect(api.listMyTasks).not.toHaveBeenCalled();
  });

  it("a non-owner sees the same sentence with no dead-end button", async () => {
    renderHome({ entitlements: [], grants: [] });

    await screen.findAllByText("Finance isn't active for your organisation.");
    expect(screen.queryByRole("button", { name: "Start 14-day trial" })).toBeNull();
  });
});

describe("HomePage — no access", () => {
  it("shows No access and never fetches what the caller cannot read", async () => {
    renderHome({ grants: [] });

    const notices = await screen.findAllByText("No access");
    expect(notices.length).toBeGreaterThan(0);

    expect(api.getFinanceOverview).not.toHaveBeenCalled();
    expect(api.listFinanceInvoices).not.toHaveBeenCalled();
    expect(api.listFinanceCustomers).not.toHaveBeenCalled();
    expect(api.countCrmCustomers).not.toHaveBeenCalled();
    expect(api.listDealActivities).not.toHaveBeenCalled();
    expect(api.listMyTasks).not.toHaveBeenCalled();
  });
});

describe("HomePage — the mobile modules launcher", () => {
  it("lists only the products this caller is both entitled to and granted in", async () => {
    vi.mocked(api.getFinanceOverview).mockResolvedValue(FINANCE_OVERVIEW as never);
    arrangeInvoices();
    vi.mocked(api.listFinanceCustomers).mockResolvedValue(page([]) as never);
    vi.mocked(api.countCrmCustomers).mockResolvedValue(page([], 856) as never);
    vi.mocked(api.listMyTasks).mockResolvedValue(page([TASK]) as never);
    vi.mocked(api.listDealActivities).mockResolvedValue(page([]) as never);

    renderHome();

    const modules = await screen.findByRole("region", { name: "Your modules" });
    expect(within(modules).getByRole("link", { name: /Finance/ })).toBeTruthy();
    expect(within(modules).getByRole("link", { name: /CRM/ })).toBeTruthy();
    expect(within(modules).getByRole("link", { name: /Projects/ })).toBeTruthy();
  });
});
