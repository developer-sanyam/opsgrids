import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as analytics from "@/api/analytics";
import { WHATSAPP, WHATSAPP_EMPTY } from "./testData";
import { WhatsAppAnalyticsPage } from "./WhatsAppAnalytics";

function mount() {
  return render(
    <MemoryRouter initialEntries={["/whatsapp/analytics"]}>
      <WhatsAppAnalyticsPage />
    </MemoryRouter>,
  );
}

describe("WhatsApp analytics", () => {
  it("shows placeholders and says it is loading", () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockReturnValue(new Promise(() => undefined));
    mount();

    expect(screen.getByText("Loading WhatsApp analytics")).toBeTruthy();
    expect(screen.queryByText("Recipient not on WhatsApp")).toBeNull();
  });

  it("breaks the period down by status, category and failure reason, each as a table for a screen reader", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();

    const status = await screen.findByRole("table", { name: /WhatsApp messages by status/ });
    expect(status.textContent).toContain("Read");
    expect(status.textContent).toContain("8,643");

    const category = screen.getByRole("table", { name: "WhatsApp messages by category" });
    expect(category.textContent).toContain("Marketing");
    expect(category.textContent).toContain("9,000");

    const failures = screen.getByRole("table", { name: "WhatsApp failures by reason" });
    expect(failures.textContent).toContain("Recipient not on WhatsApp");
    expect(failures.textContent).toContain("90");

    expect(screen.getByRole("table", { name: /WhatsApp messages per day/ })).toBeTruthy();
  });

  it("labels the cost as Estimated in its heading, its tag, its total and its note", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();

    expect(await screen.findByRole("heading", { level: 2, name: "Estimated cost by category" })).toBeTruthy();
    expect(screen.getByText("Estimated")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Estimated WhatsApp cost by category" });
    expect(table.textContent).toContain("₹7,061.40");
    expect(table.textContent).toContain("₹3,842.50");
    expect(screen.getByText(/not from Meta.s invoice/)).toBeTruthy();
  });

  it("has an empty state for every card when there was nothing to count", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP_EMPTY);
    mount();

    expect(await screen.findByText("No messages in this period")).toBeTruthy();
    expect(screen.getByText("No messages to break down")).toBeTruthy();
    expect(screen.getByText("No categorised messages")).toBeTruthy();
    expect(screen.getByText("No failed messages")).toBeTruthy();
    expect(screen.getByText("No estimated cost for this period")).toBeTruthy();
  });

  it("shows an error with a retry, plus the plan-gated and permission-denied states", async () => {
    const read = vi
      .spyOn(analytics, "getWhatsAppOverview")
      .mockRejectedValueOnce(new ApiError(500, "Analytics failed", null))
      .mockResolvedValue(WHATSAPP);
    const first = mount();
    expect(await screen.findByText("Couldn't load WhatsApp analytics")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByText("Recipient not on WhatsApp")).length).toBeGreaterThan(0);
    expect(read).toHaveBeenCalledTimes(2);
    first.unmount();

    vi.spyOn(analytics, "getWhatsAppOverview").mockRejectedValueOnce(new ApiError(402, "x", null));
    const gated = mount();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    gated.unmount();

    vi.spyOn(analytics, "getWhatsAppOverview").mockRejectedValueOnce(new ApiError(403, "x", null));
    mount();
    expect(await screen.findByText("You can't open WhatsApp analytics")).toBeTruthy();
  });

  it("refetches when the date range changes", async () => {
    const read = vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    mount();
    await screen.findAllByText("Recipient not on WhatsApp");

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Date range" }), "90d");

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  });
});
