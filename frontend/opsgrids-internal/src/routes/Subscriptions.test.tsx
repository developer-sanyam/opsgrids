import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { SubscriptionsPage } from "./Subscriptions";

function renderSubscriptions() {
  return render(
    <MemoryRouter>
      <SubscriptionsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SubscriptionsPage", () => {
  it("renders rows from the real, flattened SubscriptionOut shape", async () => {
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          product_key: "trading",
          state: "active",
          plan_version_id: "pv_trading_bespoke_northwind_v1",
          amount_minor: 420_000_00,
          currency: "INR",
          trial_ends_at: null,
        },
        {
          organization_id: "org_07c662",
          product_key: "crm",
          state: "trial",
          plan_version_id: null,
          amount_minor: 0,
          currency: null,
          trial_ends_at: "2026-09-05T00:00:00Z",
        },
      ],
    });

    renderSubscriptions();
    await waitFor(() => expect(screen.getByText("trading")).toBeTruthy());
    expect(screen.getByText("crm")).toBeTruthy();
  });

  it("shows the empty state for an empty subscription list", async () => {
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: [] });
    renderSubscriptions();
    await waitFor(() => expect(screen.getByText("No matching subscriptions")).toBeTruthy());
  });
});
