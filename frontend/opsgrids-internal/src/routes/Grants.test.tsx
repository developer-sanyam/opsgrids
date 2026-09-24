import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { GrantsPage } from "./Grants";

function renderGrants() {
  return render(
    <MemoryRouter>
      <GrantsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GrantsPage", () => {
  it("renders ledger rows from billing's real Page<CreditLedgerEntryAdminOut> envelope", async () => {
    vi.spyOn(client, "listLedger").mockResolvedValue({
      items: [
        {
          id: "le_1",
          organization_id: "org_7e3dd9",
          entry_type: "grant",
          amount_minor: 360_000_00,
          currency: "INR",
          reason: "Migration goodwill",
          valid_from: "2026-08-01T00:00:00Z",
          valid_until: "2026-10-31T23:59:59Z",
          reversal_of: null,
          drawn_from: null,
          created_at: "2026-07-29T11:12:00Z",
        },
        {
          id: "le_2",
          organization_id: "org_3af995",
          entry_type: "reversal",
          amount_minor: -20_000_00,
          currency: "INR",
          reason: "Reversed — payment recovered",
          valid_from: "2026-08-19T00:00:00Z",
          valid_until: null,
          reversal_of: "le_0",
          drawn_from: null,
          created_at: "2026-08-19T09:22:00Z",
        },
      ],
      total: 2,
      page: 1,
      size: 200,
      pages: 1,
    });
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

    renderGrants();
    await waitFor(() => expect(screen.getByText("org_7e3dd9")).toBeTruthy());
    expect(screen.getByText("org_3af995")).toBeTruthy();
  });

  it("shows the empty state for an empty ledger page", async () => {
    vi.spyOn(client, "listLedger").mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
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

    renderGrants();
    await waitFor(() => expect(screen.getByText("No ledger entries")).toBeTruthy());
  });
});
