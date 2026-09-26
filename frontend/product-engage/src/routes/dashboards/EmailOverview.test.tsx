import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as analytics from "@/api/analytics";
import { EMAIL, EMAIL_EMPTY } from "./testData";
import { EmailOverviewPage } from "./EmailOverview";

function mount(url = "/email") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <EmailOverviewPage />
    </MemoryRouter>,
  );
}

describe("the email overview", () => {
  it("shows placeholders and says it is loading while the figures load", () => {
    vi.spyOn(analytics, "getEmailOverview").mockReturnValue(new Promise(() => undefined));
    mount();

    expect(screen.getByText("Loading email overview")).toBeTruthy();
    expect(screen.queryByText("48,392")).toBeNull();
  });

  it("shows the counts with each rate over the right denominator", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    mount();

    expect(await screen.findByText("48,392")).toBeTruthy();
    expect(screen.getByText("96.8% of sent")).toBeTruthy();
    expect(screen.getByText("39.8% of delivered")).toBeTruthy();
    expect(screen.getByText("12.5% of delivered")).toBeTruthy();
    expect(screen.getByText("0.9% of sent")).toBeTruthy();
    expect(screen.getByText("12,842 subscribed")).toBeTruthy();
    expect(screen.getByText("Campaign sent")).toBeTruthy();
  });

  it("says what delivered means and that opens need tracking on", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    mount();

    expect(await screen.findByText(/your provider accepted the message/)).toBeTruthy();
    expect(screen.getByText(/counted only for campaigns that have tracking turned on/)).toBeTruthy();
  });

  it("gives the performance chart a table alternative", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    mount();

    const table = await screen.findByRole("table", {
      name: "Emails per day: sent, delivered, opened, clicked and failed",
    });
    expect(table.textContent).toContain("1,300");
  });

  it("offers Create campaign as a link to the campaign editor", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    mount();

    const link = await screen.findByRole("link", { name: "Create campaign" });
    expect(link.getAttribute("href")).toBe("/campaigns/new");
  });

  it("shows empty states when nothing was sent", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL_EMPTY);
    mount();

    expect(await screen.findByText("No emails in this period")).toBeTruthy();
    expect(screen.getByText("No recent activity yet")).toBeTruthy();
    expect(screen.queryByText(/of delivered/)).toBeNull();
  });

  it("shows an error with a retry that reads again", async () => {
    const read = vi
      .spyOn(analytics, "getEmailOverview")
      .mockRejectedValueOnce(new ApiError(503, "Analytics is warming up", null))
      .mockResolvedValue(EMAIL);
    mount();

    expect(await screen.findByText("Couldn't load the email overview")).toBeTruthy();
    expect(screen.getByText("Analytics is warming up")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("48,392")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("shows the plan-gated and permission-denied states", async () => {
    vi.spyOn(analytics, "getEmailOverview").mockRejectedValueOnce(new ApiError(402, "x", null));
    const first = mount();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    first.unmount();

    vi.spyOn(analytics, "getEmailOverview").mockRejectedValueOnce(new ApiError(403, "x", null));
    mount();
    expect(await screen.findByText("You can't open the email overview")).toBeTruthy();
  });

  it("refetches with the new dates when the range changes", async () => {
    const read = vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    mount("/email?range=custom&from=2026-09-01&to=2026-09-30");
    await screen.findByText("48,392");
    expect(read).toHaveBeenLastCalledWith({ from: "2026-09-01", to: "2026-09-30" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Date range" }), "90d");

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    const second = read.mock.calls[1]?.[0];
    const days = (Date.parse(`${second?.to}T00:00:00Z`) - Date.parse(`${second?.from}T00:00:00Z`)) / 86_400_000;
    expect(days).toBe(89);
  });
});
