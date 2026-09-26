import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as analytics from "@/api/analytics";
import { WHATSAPP, WHATSAPP_EMPTY } from "./testData";
import { formatDay } from "./dates";
import { WhatsAppOverviewPage } from "./WhatsAppOverview";

/**
 * The WhatsApp overview in every state it has: loading, populated, empty, failed (three ways), and
 * a range change that asks the service again. The cost must say "Estimated" wherever it appears.
 */

function UrlProbe() {
  const location = useLocation();
  return <p data-testid="url">{location.search}</p>;
}

function mount(url = "/whatsapp") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <WhatsAppOverviewPage />
      <UrlProbe />
    </MemoryRouter>,
  );
}

describe("the WhatsApp overview", () => {
  it("holds its layout with placeholders while the figures load, and says it is loading", () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockReturnValue(new Promise(() => undefined));
    mount();

    expect(screen.getByText("Loading WhatsApp overview")).toBeTruthy();
    expect(screen.queryByText("12,842")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "WhatsApp" })).toBeTruthy();
  });

  it("shows the counts, shares of sent, the volume chart, the feed and the totals", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();

    expect(await screen.findByText("12,842")).toBeTruthy();
    expect(screen.getByText("11,921")).toBeTruthy();
    expect(screen.getByText("92.8% of sent")).toBeTruthy();
    expect(screen.getByText("67.3% of sent")).toBeTruthy();
    expect(screen.getByText("2,184")).toBeTruthy();

    expect(screen.getByText("Message delivered")).toBeTruthy();
    expect(screen.getByText("Order shipped - #ORD-2938")).toBeTruthy();
    expect(screen.getByText("8 pending")).toBeTruthy();
    expect(screen.getByText("28 unassigned")).toBeTruthy();
  });

  it("gives the volume chart a text alternative: a table of every day and series", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();

    const table = await screen.findByRole("table", {
      name: "WhatsApp messages per day: sent, delivered, read and failed",
    });
    expect(table.textContent).toContain(formatDay("2026-09-01"));
    expect(table.textContent).toContain("620");
    expect(table.textContent).toContain("Delivered");
  });

  it("still draws a one-day range, with its table, rather than a line through a single point", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue({
      ...WHATSAPP,
      series: WHATSAPP.series.slice(0, 1),
    });
    mount("/whatsapp?range=custom&from=2026-09-01&to=2026-09-01");

    const table = await screen.findByRole("table", { name: /WhatsApp messages per day/ });
    expect(table.textContent).toContain("620");
  });

  it("labels the cost as an estimate, in the card's own name, with the money in rupees", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();

    expect(await screen.findByText("Estimated WhatsApp cost")).toBeTruthy();
    expect(screen.getByText("₹3,842.50")).toBeTruthy();
    expect(screen.getByText("Estimated from the rate card, not a bill")).toBeTruthy();
  });

  it("says so when nothing was sent, rather than drawing a flat line or a rate over zero", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP_EMPTY);
    mount();

    expect(await screen.findByText("No messages in this period")).toBeTruthy();
    expect(screen.getByText("No recent activity yet")).toBeTruthy();
    expect(screen.queryByText(/of sent/)).toBeNull();
    expect(screen.queryByRole("table", { name: /WhatsApp messages per day/ })).toBeNull();
  });

  it("reports a failed read with its own words and a way to try again", async () => {
    const read = vi
      .spyOn(analytics, "getWhatsAppOverview")
      .mockRejectedValueOnce(new ApiError(500, "The analytics store is unavailable", null))
      .mockResolvedValue(WHATSAPP);
    mount();

    expect(await screen.findByText("Couldn't load the WhatsApp overview")).toBeTruthy();
    expect(screen.getByText("The analytics store is unavailable")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("12,842")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("says a network failure is a network failure", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockRejectedValue(new TypeError("Failed to fetch"));
    mount();

    expect(await screen.findByText(/Couldn't reach the server/)).toBeTruthy();
  });

  it("shows the plan-gated state, not an error, when Engage is not active", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockRejectedValue(new ApiError(402, "Payment required", null));
    mount();

    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByText("Couldn't load the WhatsApp overview")).toBeNull();
  });

  it("shows the permission-denied state for a 403", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockRejectedValue(new ApiError(403, "Forbidden", null));
    mount();

    expect(await screen.findByText("You can't open the WhatsApp overview")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("asks for the last 30 days by default, ending today", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();
    await screen.findByText("12,842");

    const [range] = read.mock.calls[0] ?? [];
    expect(range).toBeDefined();
    const days = (Date.parse(`${range?.to}T00:00:00Z`) - Date.parse(`${range?.from}T00:00:00Z`)) / 86_400_000;
    expect(days).toBe(29);
  });

  it("asks again, for the new range, and puts the range in the URL when the preset changes", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();
    await screen.findByText("12,842");

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Date range" }), "7d");

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    const first = read.mock.calls[0]?.[0];
    const second = read.mock.calls[1]?.[0];
    expect(second?.from).not.toBe(first?.from);
    expect(second?.to).toBe(first?.to);
    const days = (Date.parse(`${second?.to}T00:00:00Z`) - Date.parse(`${second?.from}T00:00:00Z`)) / 86_400_000;
    expect(days).toBe(6);
    expect(screen.getByTestId("url").textContent).toBe("?range=7d");
    expect(await screen.findByText("12,842")).toBeTruthy();
  });

  it("reads a custom range from the URL and sends exactly those dates", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount("/whatsapp?range=custom&from=2026-09-01&to=2026-09-30");
    await screen.findByText("12,842");

    expect(read).toHaveBeenCalledWith({ from: "2026-09-01", to: "2026-09-30" });
    expect(screen.getByRole("combobox", { name: "Date range" })).toHaveProperty("value", "custom");
  });

  it("refuses a custom range whose end is before its start, and does not ask the service", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount("/whatsapp?range=custom&from=2026-09-01&to=2026-09-30");
    await screen.findByText("12,842");

    const to = screen.getByLabelText("To");
    await userEvent.clear(to);
    await userEvent.type(to, "2026-08-15");

    expect(await screen.findByText("The end date must be on or after the start date.")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("falls back to the default range for a malformed custom range in the URL", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount("/whatsapp?range=custom&from=nope&to=2026-09-30");
    await screen.findByText("12,842");

    expect(screen.getByRole("combobox", { name: "Date range" })).toHaveProperty("value", "30d");
    expect(read).toHaveBeenCalledTimes(1);
  });
});
