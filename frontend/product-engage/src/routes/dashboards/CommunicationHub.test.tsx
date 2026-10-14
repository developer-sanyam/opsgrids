import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as analytics from "@/api/analytics";
import type { HubOverview } from "@/api/analytics";
import { HUB, WHATSAPP_EMPTY } from "./testData";
import { CommunicationHubPage } from "./CommunicationHub";

const NOTHING_CONNECTED: HubOverview = {
  channels: [
    { key: "whatsapp", connected: false, detail: null },
    { key: "email", connected: false, detail: null },
    { key: "sms", connected: false, detail: null },
    { key: "push", connected: false, detail: null },
  ],
  whatsapp: null,
  email: null,
  costs: null,
};

function mount() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommunicationHubPage />
    </MemoryRouter>,
  );
}

function card(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { level: 2, name });
  const found = heading.closest("div.rounded-card");
  if (!(found instanceof HTMLElement)) throw new Error(`No card around ${name}`);
  return found;
}

describe("the communication hub", () => {
  it("shows placeholders and says it is loading", () => {
    vi.spyOn(analytics, "getHubOverview").mockReturnValue(new Promise(() => undefined));
    mount();

    expect(screen.getByText("Loading communication overview")).toBeTruthy();
    expect(screen.queryByText("Connected")).toBeNull();
  });

  it("says which channels are connected and which are not, in words", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();

    await screen.findByText("+91 ••••• 4321 · Quality green");
    expect(within(card("WhatsApp")).getByText("Connected")).toBeTruthy();
    expect(within(card("Email")).getByText("Not connected")).toBeTruthy();
  });

  it("offers Connect on a channel that is not connected, and Open on one that is", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();
    await screen.findByText("+91 ••••• 4321 · Quality green");

    expect(within(card("WhatsApp")).getByRole("link", { name: "Open WhatsApp" }).getAttribute("href")).toBe(
      "/whatsapp",
    );
    expect(within(card("Email")).getByRole("link", { name: "Connect Email" }).getAttribute("href")).toBe(
      "/connection",
    );
  });

  it("does not offer a Connect action for a channel Engage cannot connect", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();
    await screen.findByText("+91 ••••• 4321 · Quality green");

    expect(screen.queryByRole("link", { name: "Connect SMS" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Connect Push" })).toBeNull();
    expect(within(card("SMS")).getByText(/isn.t available in Engage yet/)).toBeTruthy();
    // Not both an invitation to connect and a statement that connecting is impossible.
    expect(within(card("SMS")).queryByText(/to start messaging customers/)).toBeNull();
  });

  it("links the WhatsApp connect action to the connection wizard", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(NOTHING_CONNECTED);
    mount();

    const link = await screen.findByRole("link", { name: "Connect WhatsApp" });
    expect(link.getAttribute("href")).toBe("/whatsapp/connect");
  });

  it("summarises the connected channel's numbers and labels the cost Estimated", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();

    const summary = await screen.findByRole("heading", { level: 2, name: "WhatsApp in this period" });
    expect(summary).toBeTruthy();
    expect(screen.getByText("12,842")).toBeTruthy();
    expect(screen.getByText("Estimated messaging cost")).toBeTruthy();
    expect(screen.getByText("Estimated")).toBeTruthy();
    expect(screen.getByText("₹3,842.50")).toBeTruthy();
  });

  it("has an empty state when no channel is connected yet", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(NOTHING_CONNECTED);
    mount();

    expect(await screen.findByText("Nothing to report yet")).toBeTruthy();
    expect(screen.queryByText("Estimated messaging cost")).toBeNull();
  });

  it("says so when a connected channel sent nothing in the range", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue({
      ...HUB,
      whatsapp: WHATSAPP_EMPTY,
    });
    mount();

    expect(await screen.findByText("No WhatsApp messages in this period")).toBeTruthy();
  });

  it("shows an error with a retry", async () => {
    const read = vi
      .spyOn(analytics, "getHubOverview")
      .mockRejectedValueOnce(new ApiError(500, "Hub is down", null))
      .mockResolvedValue(HUB);
    mount();

    expect(await screen.findByText("Couldn't load the communication overview")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("+91 ••••• 4321 · Quality green")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("shows the plan-gated and permission-denied states", async () => {
    vi.spyOn(analytics, "getHubOverview").mockRejectedValueOnce(new ApiError(402, "x", null));
    const gated = mount();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    gated.unmount();

    vi.spyOn(analytics, "getHubOverview").mockRejectedValueOnce(new ApiError(403, "x", null));
    mount();
    expect(await screen.findByText("You can't open the communication overview")).toBeTruthy();
  });

  it("refetches when the date range changes", async () => {
    const read = vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();
    await screen.findByText("+91 ••••• 4321 · Quality green");

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Date range" }), "7d");

    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(read.mock.calls[1]?.[0].from).not.toBe(read.mock.calls[0]?.[0].from);
  });

  it("links the tabs to the WhatsApp and Email overviews", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    mount();
    await screen.findByText("+91 ••••• 4321 · Quality green");

    const tabs = screen.getByRole("navigation", { name: "Communication sections" });
    expect(within(tabs).getByRole("link", { name: "WhatsApp" }).getAttribute("href")).toBe("/whatsapp");
    expect(within(tabs).getByRole("link", { name: "Email" }).getAttribute("href")).toBe("/email");
    expect(within(tabs).getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
  });
});
