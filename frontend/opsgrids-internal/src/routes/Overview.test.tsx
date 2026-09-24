/**
 * Overview: the platform's revenue and lifecycle at a glance, and the accounts an operator should
 * look at first. Every state of both reads is pinned, because they fail independently — the
 * metrics can be refused to a role that may still read accounts, and an empty attention list is
 * good news rather than a blank.
 *
 * Figures come from the fixtures the console renders in development, so a number here is a number
 * a person sees.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { buildOverviewMetrics } from "@/api/fixtures";
import { formatPercent } from "@/lib/format";
import { findAlert, renderConsole } from "@/testing-render";

const metrics = buildOverviewMetrics();

/** The KPI card a label belongs to. */
function kpi(label: string): HTMLElement {
  const card = screen.getByText(label).closest(".rounded-card");
  if (!(card instanceof HTMLElement)) throw new Error(`No KPI card for ${label}`);
  return card;
}

describe("Overview — populated", () => {
  it("is the console's landing page, with one h1 and the environment marked", async () => {
    renderConsole("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeDefined();
    expect(screen.getByText("Cross-tenant · all organizations")).toBeDefined();
    expect(screen.getByText("Fixtures data")).toBeDefined();
    expect(await screen.findByText("August 2026")).toBeDefined();
  });

  it("shows the six headline figures, each from the projection", async () => {
    renderConsole("/");
    await screen.findByText("Active accounts");

    expect(within(kpi("Active accounts")).getByText(String(metrics.active_accounts))).toBeDefined();
    expect(within(kpi("Trials in progress")).getByText(String(metrics.trials_in_progress))).toBeDefined();
    expect(within(kpi("Trial → paid conversion")).getByText(formatPercent(metrics.trial_conversion_pct))).toBeDefined();
    expect(within(kpi("Logo churn")).getByText(formatPercent(metrics.churn_pct))).toBeDefined();
    expect(within(kpi("Locked accounts")).getByText(String(metrics.locked_accounts))).toBeDefined();
    expect(within(kpi("Locked accounts")).getByText(`${metrics.suspended_accounts} suspended`)).toBeDefined();
    expect(kpi("Monthly recurring revenue").textContent).toContain("vs last month");
  });

  it("shows a change and a trend where there is history, and nothing where there is not", async () => {
    renderConsole("/");
    await screen.findByText("Active accounts");

    // A count with no history behind it says nothing rather than an invented 0%.
    expect(kpi("Locked accounts").querySelector("svg[viewBox='0 0 84 32']")).toBeNull();
    expect(kpi("Locked accounts").textContent).not.toMatch(/%/);
    expect(kpi("Active accounts").querySelector("svg[viewBox='0 0 84 32']")).not.toBeNull();
    expect(kpi("Active accounts").textContent).toContain("2.2%");
  });

  it("draws the MRR trend with the same twelve figures in text, and reads the trial funnel and lifecycle as words", async () => {
    renderConsole("/");
    const trend = await screen.findByRole("img", { name: "Monthly recurring revenue, trailing twelve months" });
    expect(trend).toBeDefined();
    const figure = trend.closest("figure");
    expect(figure?.textContent).toContain("Sep ₹12,40,000");
    expect(figure?.textContent).toContain("Aug ₹17,60,000");

    const funnel = screen.getByRole("img", { name: /Converted to paid: 8 accounts/ });
    expect(funnel.getAttribute("aria-label")).toContain("Still in trial: 3 accounts");
    expect(funnel.getAttribute("aria-label")).toContain("Trial expired: 1 accounts");
    expect(screen.getByRole("img", { name: /Retained \(active\): \d+ accounts/ })).toBeDefined();
  });

  it("lists the accounts that need attention, worst first, each linking to its page", async () => {
    renderConsole("/");
    const list = (await screen.findByText("Vertex Mining")).closest("ul");
    if (list === null) throw new Error("no attention list");
    const rows = within(list).getAllByRole("link");

    // Suspensions, then dunning, then trials about to lapse — and not a trial that has weeks left.
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Vertex Mining"),
      expect.stringContaining("Sahara Scrap Co"),
      expect.stringContaining("Pioneer Alloys"),
      expect.stringContaining("BlueOak Retail"),
      expect.stringContaining("Corveto Logistics"),
    ]);
    expect(rows[0]?.textContent).toContain("Suspended — access locked");
    expect(rows[0]?.textContent).toContain("Locked");
    expect(rows[2]?.textContent).toContain("Payment past due — in dunning");
    expect(rows[3]?.textContent).toContain("Trial ends in 12 days");
    expect(rows[0]?.getAttribute("href")).toBe("/accounts/org_4b0aa6");
    expect(within(list).queryByText("Anta Foods")).toBeNull();
  });
});

describe("Overview — every state of the two reads", () => {
  it("holds the layout with placeholders while both load, and says the metrics are busy", async () => {
    vi.spyOn(client, "getOverview").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(client, "listOrganizations").mockReturnValue(new Promise(() => undefined));
    renderConsole("/");

    const section = await screen.findByRole("region", { name: "Key metrics" });
    expect(section.getAttribute("aria-busy")).toBe("true");
    expect(section.querySelectorAll(".animate-pulse").length).toBeGreaterThan(6);
    expect(screen.queryByText("Active accounts")).toBeNull();
    expect(screen.queryByText("Nothing needs attention — every account is active and paid.")).toBeNull();
  });

  it("says the metrics failed, offers the retry a transient failure deserves, and recovers", async () => {
    const user = userEvent.setup();
    const overview = vi
      .spyOn(client, "getOverview")
      .mockRejectedValueOnce(new ApiError(503, "console projection is unavailable", { status: 503, request_id: "ovr-1" }));
    renderConsole("/");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load the platform metrics");
    expect(alert.textContent).toContain("console projection is unavailable");
    expect(alert.textContent).toContain("Reference: ovr-1");
    // The accounts read is independent: it still renders.
    expect(await screen.findByText("Vertex Mining")).toBeDefined();

    overview.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Active accounts")).toBeDefined();
  });

  it("says a role that may not read the metrics is not permitted, in the server's words, with no retry", async () => {
    vi.spyOn(client, "getOverview").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: billing_ops, owner", {
        status: 403,
        detail: "This action needs one of these operator roles: billing_ops, owner",
      }),
    );
    renderConsole("/", ["support"]);

    const alert = await findAlert();
    expect(alert.textContent).toContain("You can't view this");
    expect(alert.textContent).toContain("This action needs one of these operator roles: billing_ops, owner");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
    // Support may still read accounts, so the part of the page that is theirs is intact.
    expect(await screen.findByText("Vertex Mining")).toBeDefined();
  });

  it("says the accounts read failed on its own, and retries it on its own", async () => {
    const user = userEvent.setup();
    const orgs = vi
      .spyOn(client, "listOrganizations")
      .mockRejectedValueOnce(new ApiError(500, "directory unavailable", { status: 500 }));
    renderConsole("/");

    expect(await screen.findByText("Couldn't load accounts")).toBeDefined();
    expect(screen.getByText("Active accounts")).toBeDefined();

    orgs.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Vertex Mining")).toBeDefined();
  });

  it("says nothing needs attention when nothing does — good news, not a blank", async () => {
    vi.spyOn(client, "listOrganizations").mockResolvedValue({ items: [], total: 0 });
    renderConsole("/");
    expect(await screen.findByText("Nothing needs attention — every account is active and paid.")).toBeDefined();
    expect(screen.queryAllByRole("alert").filter((el) => (el.textContent ?? "").trim() !== "")).toHaveLength(0);
  });
});
