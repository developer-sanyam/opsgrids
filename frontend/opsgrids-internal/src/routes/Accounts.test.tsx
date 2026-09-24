import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { AccountsPage } from "./Accounts";

/** Rendered against the real `OrganizationsOut` envelope — `{organizations, metrics}`, not the
 *  `{items, total}` `Page<T>` the client used to assume. */
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
  it("renders rows from the real organizations envelope, with no fabricated name", async () => {
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
          organization_id: "org_a1f09c",
          mrr_minor: 420_000_00,
          currency: "INR",
          active_products: ["trading"],
          subscription_states: {},
          trial_ends_at: null,
          locked: false,
          updated_at: "2026-08-23T14:20:00Z",
        },
        {
          organization_id: "org_4b0aa6",
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: true,
          updated_at: "2026-07-30T10:04:00Z",
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

    await waitFor(() => expect(screen.getByText("org_a1f09c")).toBeTruthy());
    expect(screen.getByText("org_4b0aa6")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
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
