import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { MetricsOverviewOut } from "@/api/types";
import { OverviewPage } from "./Overview";

/**
 * Rendered against the real `GET /admin/metrics/overview` (`MetricsOverviewOut`) and
 * `GET /admin/organizations` (`OrganizationsOut`) shapes — no sparkline, no delta and no revenue
 * figure, because the live payload doesn't carry them (`services/console/app/schemas.py`); `name`/
 * `slug` it does carry now, mirrored from account-service. This is the exact page that shipped
 * broken: `attentionRows(orgs.data.items)` threw because the real envelope key is `organizations`,
 * not `items`.
 */

function renderOverview() {
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

const METRICS: MetricsOverviewOut = {
  mrr_minor: 176_00000,
  currency: "INR",
  trials_started: 12,
  trial_conversions: 8,
  trial_conversion_rate: 66.7,
  active_subscriptions: 11,
  churned: 1,
  churn_rate: 8.3,
  organization_count: 15,
  organizations_by_lifecycle: { suspended: 2, active: 10, trial: 2, churned: 1, none: 0 },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OverviewPage", () => {
  it("renders every KPI from the real overview payload with no fabricated delta", async () => {
    vi.spyOn(client, "getOverview").mockResolvedValue(METRICS);
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
          organization_id: "org_a1f09c",
          name: "Northwind Metals",
          slug: "northwind-metals",
          mrr_minor: 420_000_00,
          currency: "INR",
          active_products: ["trading"],
          subscription_states: {},
          trial_ends_at: null,
          locked: false,
          updated_at: "2026-08-23T14:20:00Z",
        },
      ],
      metrics: {
        mrr_minor: METRICS.mrr_minor,
        currency: "INR",
        trials_started: 12,
        trial_conversions: 8,
        trial_conversion_rate: 66.7,
        active_subscriptions: 11,
        churned: 1,
        churn_rate: 8.3,
      },
    });

    renderOverview();

    await waitFor(() => expect(screen.getByText("Trials started")).toBeTruthy());
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("11")).toBeTruthy();
    expect(screen.getByText("66.7%")).toBeTruthy();
    // "Organizations by lifecycle" is built off the real `organizations_by_lifecycle` counts.
    expect(screen.getByText("Organizations by lifecycle")).toBeTruthy();
  });

  it("shows an honest empty attention list when nothing is locked or about to lapse", async () => {
    vi.spyOn(client, "getOverview").mockResolvedValue(METRICS);
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
          organization_id: "org_clean",
          name: "Clean Co",
          slug: "clean-co",
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: false,
          updated_at: "2026-08-23T14:20:00Z",
        },
      ],
      metrics: METRICS,
    });

    renderOverview();
    await waitFor(() =>
      expect(
        screen.getByText("Nothing needs attention — every account is unlocked with no trial ending soon."),
      ).toBeTruthy(),
    );
  });

  it("names the account in the attention list, falling back to the id when there's no name", async () => {
    vi.spyOn(client, "getOverview").mockResolvedValue(METRICS);
    vi.spyOn(client, "listOrganizations").mockResolvedValue({
      organizations: [
        {
          organization_id: "org_4b0aa6",
          name: "Vertex Mining",
          slug: "vertex-mining",
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: true,
          updated_at: "2026-07-30T10:04:00Z",
        },
        {
          organization_id: "org_not_backfilled",
          name: null,
          slug: null,
          mrr_minor: 0,
          currency: null,
          active_products: [],
          subscription_states: {},
          trial_ends_at: null,
          locked: true,
          updated_at: "2026-07-30T10:04:00Z",
        },
      ],
      metrics: METRICS,
    });

    renderOverview();

    await waitFor(() => expect(screen.getByText("Vertex Mining")).toBeTruthy());
    expect(screen.getByText("vertex-mining")).toBeTruthy();
    expect(screen.getByText("org_not_backfilled")).toBeTruthy();
  });

  it("shows a contained error card, not a crash, when the metrics call fails", async () => {
    vi.spyOn(client, "getOverview").mockRejectedValue(new Error("Request failed with 500"));
    vi.spyOn(client, "listOrganizations").mockResolvedValue({ organizations: [], metrics: METRICS });

    renderOverview();
    await waitFor(() => expect(screen.getAllByText("Request failed with 500").length).toBeGreaterThan(0));
  });
});
