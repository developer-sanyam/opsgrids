import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as analytics from "@/api/analytics";
import * as client from "@/api/client";
import { CAMPAIGN_SENT } from "@/api/fixtures";
import { CAMPAIGN } from "./testData";
import { CampaignAnalyticsPage } from "./CampaignAnalytics";
import { formatDay } from "./dates";

/**
 * One campaign's analytics. The point of most of this file is that a metric that was never tracked
 * is "Tracking is off for this campaign" — never a zero — and that a zero which really was
 * measured says something different.
 */

function mount() {
  vi.spyOn(client, "getCampaign").mockResolvedValue(CAMPAIGN_SENT);
  return render(
    <MemoryRouter initialEntries={["/email/campaigns/c-1/analytics"]}>
      <Routes>
        <Route path="/email/campaigns/:id/analytics" element={<CampaignAnalyticsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("campaign analytics", () => {
  it("shows placeholders and says it is loading", () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockReturnValue(new Promise(() => undefined));
    mount();

    expect(screen.getByText("Loading campaign analytics")).toBeTruthy();
    expect(screen.queryByText("12,842")).toBeNull();
  });

  it("asks for the campaign named in the URL", async () => {
    const read = vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();
    await screen.findByText("12,842");

    expect(read).toHaveBeenCalledWith("c-1");
  });

  it("shows the KPIs as shares of recipients", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();

    expect(await screen.findByText("12,842")).toBeTruthy();
    expect(screen.getAllByText("98% of recipients").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("42.1% of recipients")).toBeTruthy();
    expect(screen.getByText("8.6% of recipients")).toBeTruthy();
    expect(screen.getAllByText("0.7% of recipients").length).toBeGreaterThanOrEqual(1);
  });

  it("draws opens by device, clicks by link and engagement over time, each with a table alternative", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();
    await screen.findByText("12,842");

    const device = screen.getByRole("table", { name: /Opens by device/ });
    expect(device.textContent).toContain("Desktop");
    expect(device.textContent).toContain("2,825");

    const links = screen.getByRole("table", { name: "Clicks by link" });
    expect(links.textContent).toContain("423");

    const engagement = screen.getByRole("table", { name: "Engagement per day: opens and clicks" });
    expect(engagement.textContent).toContain(formatDay("2026-09-20"));
  });

  it("names a link by host and path only, never by its query string", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();
    await screen.findByText("12,842");

    const links = screen.getByRole("table", { name: "Clicks by link" });
    expect(links.textContent).toContain("shop.example.com/sale");
    expect(links.textContent).not.toContain("token");
    expect(links.textContent).not.toContain("utm_source");
  });

  it("shows deliverability with what delivered means, bounces, complaints and unsubscribes", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();
    await screen.findByText("12,842");

    expect(screen.getByText("Accepted by your email provider")).toBeTruthy();
    expect(screen.getByText("261")).toBeTruthy();
    expect(screen.getByText("2% of recipients")).toBeTruthy();
    expect(screen.getByText("Spam complaints")).toBeTruthy();
  });

  it("shows the header from the campaign itself", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    mount();

    expect(await screen.findByRole("heading", { level: 1, name: CAMPAIGN_SENT.name })).toBeTruthy();
    expect(screen.getByRole("link", { name: "View campaign" }).getAttribute("href")).toBe("/campaigns/c-1");
  });

  it("says 'Tracking is off for this campaign' instead of showing zeros when both flags are off", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue({
      ...CAMPAIGN,
      opened: 0,
      clicked: 0,
      by_device: [],
      by_link: [],
      series: [],
      tracking: { opens: false, clicks: false },
    });
    mount();
    await screen.findByText("12,842");

    // Opened and Clicked KPIs, plus the three cards.
    expect(screen.getAllByText("Tracking is off for this campaign").length).toBeGreaterThanOrEqual(5);
    expect(screen.getAllByText("Off")).toHaveLength(2);
    expect(screen.queryByText("No opens recorded yet")).toBeNull();
    expect(screen.queryByText("No clicks recorded yet")).toBeNull();
    expect(screen.queryByRole("table", { name: /Opens by device/ })).toBeNull();
    expect(screen.queryByRole("table", { name: "Clicks by link" })).toBeNull();
    // Delivery is still reported.
    expect(screen.getAllByText("98% of recipients").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Spam complaints")).toBeTruthy();
  });

  it("switches off only the metric whose flag is off, and charts the other alone", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue({
      ...CAMPAIGN,
      opened: 0,
      by_device: [],
      tracking: { opens: false, clicks: true },
    });
    mount();
    await screen.findByText("12,842");

    expect(screen.getAllByText("Off")).toHaveLength(1);
    expect(screen.getByText("8.6% of recipients")).toBeTruthy();
    expect(screen.getByRole("table", { name: "Clicks by link" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Engagement per day: clicks" })).toBeTruthy();
  });

  it("tells a measured zero apart from an unmeasured one", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue({
      ...CAMPAIGN,
      opened: 0,
      clicked: 0,
      by_device: [],
      by_link: [],
      series: [],
      tracking: { opens: true, clicks: true },
    });
    mount();

    expect(await screen.findByText("No opens recorded yet")).toBeTruthy();
    expect(screen.getByText("No clicks recorded yet")).toBeTruthy();
    expect(screen.getByText("No engagement recorded yet")).toBeTruthy();
    expect(screen.queryByText("Tracking is off for this campaign")).toBeNull();
  });

  it("says a campaign that reached nobody has nothing to report", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue({ ...CAMPAIGN, recipients: 0, delivered: 0 });
    mount();

    expect(await screen.findByText("Nothing to report yet")).toBeTruthy();
  });

  it("shows an error with a retry", async () => {
    const read = vi
      .spyOn(analytics, "getCampaignAnalytics")
      .mockRejectedValueOnce(new ApiError(500, "Analytics failed", null))
      .mockResolvedValue(CAMPAIGN);
    mount();

    expect(await screen.findByText("Couldn't load campaign analytics")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("12,842")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("shows the plan-gated, permission-denied and not-found states", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockRejectedValueOnce(new ApiError(402, "x", null));
    const gated = mount();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    gated.unmount();

    vi.spyOn(analytics, "getCampaignAnalytics").mockRejectedValueOnce(new ApiError(403, "x", null));
    const denied = mount();
    expect(await screen.findByText("You can't open this campaign's analytics")).toBeTruthy();
    denied.unmount();

    vi.spyOn(analytics, "getCampaignAnalytics").mockRejectedValueOnce(new ApiError(404, "Campaign not found", null));
    mount();
    expect(await screen.findByText("Campaign not found")).toBeTruthy();
  });

  it("still shows the analytics when the campaign itself cannot be read", async () => {
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, "getCampaign").mockRejectedValue(new Error("nope"));
    render(
      <MemoryRouter initialEntries={["/email/campaigns/c-1/analytics"]}>
        <Routes>
          <Route path="/email/campaigns/:id/analytics" element={<CampaignAnalyticsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("12,842")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Campaign analytics" })).toBeTruthy();
  });
});
