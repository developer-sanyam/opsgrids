import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { AccountsPage } from "./Accounts";

/** Rendered against the real `OrganizationsOut` envelope — `{organizations, metrics}`, not the
 *  `{items, total}` `Page<T>` the client used to assume — and the account-service `name`/`slug`
 *  mirror now folded onto each row. */
function renderAccounts() {
  return render(
    <MemoryRouter>
      <AccountsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AccountsPage", () => {
  it("renders a name and slug when the projection carries one, and the raw id when it doesn't", async () => {
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
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
        },
        {
          organization_id: "org_4b0aa6",
          name: null,
          slug: null,
          country: null,
          created_at: null,
          lifecycle: "suspended",
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: true,
          updated_at: "2026-07-30T10:04:00Z",
          user_count: null,
        },
      ],
      metrics: {
        mrr_minor: 420_000_00,
        currency: "INR",
        trials_started: 0,
        trial_conversions: 0,
        trial_conversion_rate: 0,
        active_subscriptions: 1,
        churned: 0,
        churn_rate: 0,
      },
    });

    renderAccounts();

    await waitFor(() => expect(screen.getByText("Northwind Metals")).toBeTruthy());
    expect(screen.getByText("northwind-metals")).toBeTruthy();
    // No name yet (predates the identity fold) — the row falls back to the raw id, never a guess.
    expect(screen.getByText("org_4b0aa6")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("is searchable by name and by slug, client-side", async () => {
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
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
        },
        {
          organization_id: "org_4b0aa6",
          name: "Vertex Mining",
          slug: "vertex-mining",
          country: "IN",
          created_at: "2024-12-01T00:00:00Z",
          lifecycle: "suspended",
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: true,
          updated_at: "2026-07-30T10:04:00Z",
          user_count: 28,
        },
      ],
      metrics: {
        mrr_minor: 420_000_00,
        currency: "INR",
        trials_started: 0,
        trial_conversions: 0,
        trial_conversion_rate: 0,
        active_subscriptions: 1,
        churned: 0,
        churn_rate: 0,
      },
    });

    renderAccounts();
    await waitFor(() => expect(screen.getByText("Northwind Metals")).toBeTruthy());

    await userEvent.type(screen.getByLabelText("Search accounts"), "vertex");
    await waitFor(() => expect(screen.queryByText("Northwind Metals")).toBeNull());
    expect(screen.getByText("Vertex Mining")).toBeTruthy();

    await userEvent.clear(screen.getByLabelText("Search accounts"));
    await userEvent.type(screen.getByLabelText("Search accounts"), "northwind-metals");
    await waitFor(() => expect(screen.queryByText("Vertex Mining")).toBeNull());
    expect(screen.getByText("Northwind Metals")).toBeTruthy();
  });

  it("shows the empty state for an org list with no rows", async () => {
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [],
      metrics: {
        mrr_minor: 0,
        currency: null,
        trials_started: 0,
        trial_conversions: 0,
        trial_conversion_rate: 0,
        active_subscriptions: 0,
        churned: 0,
        churn_rate: 0,
      },
    });

    renderAccounts();
    await waitFor(() => expect(screen.getByText("No matching accounts")).toBeTruthy());
  });

  it("shows a contained error state rather than throwing when the list call fails", async () => {
    vi.spyOn(client, "listOrganizations").mockRejectedValue(new Error("Request failed with 503"));
    renderAccounts();
    await waitFor(() => expect(screen.getByText("Request failed with 503")).toBeTruthy());
  });
});
