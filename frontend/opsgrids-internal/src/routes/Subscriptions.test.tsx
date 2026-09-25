import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  it("renders rows from the real, flattened SubscriptionOut shape, with the org's name", async () => {
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({
      subscriptions: [
        {
          organization_id: "org_a1f09c",
          name: "Northwind Metals",
          slug: "northwind-metals",
          product_key: "trading",
          product_name: "OpsGrids Trading Desk",
          state: "active",
          plan_version_id: "pv_trading_bespoke_northwind_v1",
          plan_name: "Enterprise · Northwind",
          amount_minor: 420_000_00,
          currency: "INR",
          trial_ends_at: null,
        },
        {
          organization_id: "org_07c662",
          name: null,
          slug: null,
          product_key: "crm",
          product_name: "OpsGrids CRM",
          state: "trial",
          plan_version_id: null,
          plan_name: null,
          amount_minor: 0,
          currency: null,
          trial_ends_at: "2026-09-05T00:00:00Z",
        },
      ],
    });

    renderSubscriptions();
    await waitFor(() => expect(screen.getByText("trading")).toBeTruthy());
    expect(screen.getByText("crm")).toBeTruthy();
    expect(screen.getByText("Northwind Metals")).toBeTruthy();
    expect(screen.getByText("northwind-metals")).toBeTruthy();
    // No name for the second row (predates the identity fold) — falls back to the raw id.
    expect(screen.getByText("org_07c662")).toBeTruthy();
  });

  it("is searchable by the organization's name", async () => {
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({
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
        {
          organization_id: "org_07c662",
          name: "BlueOak Retail",
          slug: "blueoak-retail",
          product_key: "crm",
          product_name: "OpsGrids CRM",
          state: "trial",
          plan_version_id: null,
          plan_name: null,
          amount_minor: 0,
          currency: null,
          trial_ends_at: "2026-09-05T00:00:00Z",
        },
      ],
    });

    renderSubscriptions();
    await waitFor(() => expect(screen.getByText("Northwind Metals")).toBeTruthy());

    await userEvent.type(screen.getByLabelText("Search subscriptions"), "blueoak");
    await waitFor(() => expect(screen.queryByText("Northwind Metals")).toBeNull());
    expect(screen.getByText("BlueOak Retail")).toBeTruthy();
  });

  it("shows the empty state for an empty subscription list", async () => {
    vi.spyOn(client, "listSubscriptions").mockResolvedValue({ subscriptions: [] });
    renderSubscriptions();
    await waitFor(() => expect(screen.getByText("No matching subscriptions")).toBeTruthy());
  });
});
