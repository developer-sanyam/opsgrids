import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import { AccountDetailPage } from "./AccountDetail";

/**
 * Rendered against the real `OrgDetailOut` composite — a thin directory identity plus an
 * `OrgStateOut | null` commercial half — and the separately-fetched, globally-listed
 * subscriptions/usage/ledger the live API actually offers, each filtered client-side to this one
 * organization exactly the way `routes/AccountDetail.tsx` does it.
 */

function mockAuth() {
  const value: AuthContextValue = {
    status: "authenticated",
    operator: null,
    login: vi.fn(),
    beginEnrolment: vi.fn(),
    confirmEnrolment: vi.fn(),
    finishEnrolment: vi.fn(),
    verifyMfa: vi.fn(),
    cancelSignIn: vi.fn(),
    logout: vi.fn(),
    hasRole: () => true,
  };
  vi.spyOn(authContext, "useAuth").mockReturnValue(value);
}

function renderDetail(orgId = "org_a1f09c") {
  return render(
    <MemoryRouter initialEntries={[`/accounts/${orgId}`]}>
      <Routes>
        <Route path="/accounts/:orgId" element={<AccountDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AccountDetailPage", () => {
  it("renders the real OrgDetailOut composite, with commercial data present", async () => {
    mockAuth();
    vi.spyOn(client, "getOrganization").mockResolvedValue({
      organization_id: "org_a1f09c",
      slug: "northwind-metals",
      name: "Northwind Metals",
      status: "active",
      country: "IN",
      created_at: "2024-02-11T00:00:00Z",
      commercial: {
        organization_id: "org_a1f09c",
        mrr_minor: 420_000_00,
        currency: "INR",
        active_products: ["trading"],
        subscription_states: {
          trading: { state: "active", plan_version_id: "pv_1", amount_minor: 420_000_00, currency: "INR", trial_ends_at: null },
        },
        trial_ends_at: null,
        locked: false,
        updated_at: "2026-08-23T14:20:00Z",
      },
    });
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          product_key: "trading",
          state: "active",
          plan_version_id: "pv_1",
          amount_minor: 420_000_00,
          currency: "INR",
          trial_ends_at: null,
        },
      ],
    });
    vi.spyOn(client, "listUsage").mockResolvedValue({ usage: [] });
    vi.spyOn(client, "listLedger").mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue({ organization_id: "org_a1f09c", products: [] });

    renderDetail();

    await waitFor(() => expect(screen.getByRole("heading", { name: "Northwind Metals" })).toBeTruthy());
    expect(screen.getByText("northwind-metals")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText("trading").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByText("No credit or comp grants on this account.")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("No metered usage recorded for this account.")).toBeTruthy());
  });

  it("renders an org with no commercial projection yet as an honest gap, not a crash", async () => {
    mockAuth();
    vi.spyOn(client, "getOrganization").mockResolvedValue({
      organization_id: "org_new",
      slug: "brand-new-co",
      name: "Brand New Co",
      status: "trial",
      country: null,
      created_at: "2026-08-25T00:00:00Z",
      commercial: null,
    });
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: [] });
    vi.spyOn(client, "listUsage").mockResolvedValue({ usage: [] });
    vi.spyOn(client, "listLedger").mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue({ organization_id: "org_new", products: [] });

    renderDetail("org_new");

    await waitFor(() => expect(screen.getByRole("heading", { name: "Brand New Co" })).toBeTruthy());
    expect(screen.getByText("not yet projected")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByText("No subscription recorded for this organization.")).toBeTruthy(),
    );
  });

  it("shows a contained error card instead of crashing when the org detail call fails", async () => {
    mockAuth();
    vi.spyOn(client, "getOrganization").mockRejectedValue(new Error("No such organization"));
    renderDetail("org_missing");
    await waitFor(() => expect(screen.getByText("No such organization")).toBeTruthy());
  });
});
