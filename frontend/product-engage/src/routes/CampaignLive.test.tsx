import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_PAUSED, CAMPAIGN_SENDING, CAMPAIGN_SENT } from "@/api/fixtures";
import type { CampaignOut, CampaignRecipientOut, Page } from "@/api/types";
import { CampaignPage } from "./CampaignEditor";

/**
 * A campaign that is being worked on while someone watches: what the screen does when the service
 * stops answering a poll, and when it is the driver rather than a person that moves the status.
 * The failure pinned first is a false one — figures reverting to their first values, or to nothing,
 * under a line that still says they are refreshing.
 */

const SENDING: CampaignOut = { ...CAMPAIGN_SENDING };
const PAUSED: CampaignOut = { ...CAMPAIGN_PAUSED };
const REFUSED = new ApiError(403, "You do not have engage.campaign.campaign.read", null);

const ROWS: CampaignRecipientOut[] = [
  {
    id: "r1",
    contact_id: "c1",
    email: "p•••@vaayu-logistics.example.com",
    status: "sent",
    provider_message_id: "smtp-1",
    error: "",
    sent_at: "2026-09-23T06:05:00Z",
  },
];

const GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function mockRefs() {
  vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([]));
}

function renderReport(campaign: CampaignOut) {
  return render(
    <PermissionProvider grants={GRANTS}>
      <MemoryRouter initialEntries={[`/campaigns/${campaign.id}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function count(label: string): string | null {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent ?? null;
}

const STOPPED = "Live updates stopped — reload to refresh.";

describe("when live updates are refused", () => {
  it("keeps the last counts and says updates stopped, instead of reverting to the page's first figures", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockRefs();
    const fresher: CampaignOut = { ...SENDING, stats: { ...SENDING.stats, queued: 170, sent: 65 } };
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SENDING) // the page's own read
      .mockResolvedValueOnce(fresher) // the poll's first read
      .mockRejectedValue(REFUSED); // and then the service says no
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(SENDING);

    await waitFor(() => expect(count("Sent")).toBe("65"));
    expect(screen.getByText(/Counts refresh every few seconds/)).toBeTruthy();

    await vi.advanceTimersByTimeAsync(6_000);

    await waitFor(() => expect(screen.getAllByText(new RegExp(STOPPED)).length).toBeGreaterThan(0));
    // Not 55 (the page's first read) and not 0: the last good figures.
    expect(count("Sent")).toBe("65");
    expect(count("Queued")).toBe("170");
    // The line that claimed they were still refreshing is gone.
    expect(screen.queryByText(/Counts refresh every few seconds/)).toBeNull();
    expect(screen.getByText(/These figures are as of/)).toBeTruthy();
  });

  it("keeps the figures the page opened with when the very first poll is refused", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValueOnce(SENDING).mockRejectedValue(REFUSED);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(SENDING);

    await waitFor(() => expect(screen.getAllByText(new RegExp(STOPPED)).length).toBeGreaterThan(0));
    expect(count("Sent")).toBe("55");
    expect(count("Recipients")).toBe("240");
    expect(screen.queryByText(/Counts refresh every few seconds/)).toBeNull();
  });

  it("keeps the recipients that were on screen and says the table stopped too", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENDING);
    vi.spyOn(client, "listCampaignRecipients")
      .mockResolvedValueOnce(page(ROWS))
      .mockRejectedValue(REFUSED);
    renderReport(SENDING);

    expect(await screen.findByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(6_000);

    const region = screen.getByRole("region", { name: "Recipients of this campaign" });
    expect(within(region).getByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
    await waitFor(() => {
      const card = screen.getByRole("heading", { level: 2, name: "Recipients" }).closest("div")
        ?.parentElement as HTMLElement;
      expect(within(card).getByText(STOPPED)).toBeTruthy();
    });
  });
});

describe("a paused campaign is polled too", () => {
  it("polls, because the driver retries a paused campaign on its own", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockRefs();
    const get = vi.spyOn(client, "getCampaign").mockResolvedValue(PAUSED);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(PAUSED);

    await screen.findByText("This campaign is paused and is not sending");
    const before = get.mock.calls.length;
    await vi.advanceTimersByTimeAsync(11_000);
    expect(get.mock.calls.length).toBeGreaterThan(before);
    expect(
      screen.getByText(/Counts refresh every few seconds while this campaign is sending or paused/),
    ).toBeTruthy();
  });

  it("reloads into the running screen when the driver resumes it on its own", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(PAUSED)
      .mockResolvedValue({ ...PAUSED, status: "sending", pause_reason: "" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(PAUSED);

    await waitFor(() => expect(screen.queryByText("This campaign is paused and is not sending")).toBeNull());
    expect(await screen.findByText("Sending", { selector: "span" })).toBeTruthy();
  });

  it("announces a status change in words, not as the wire token", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SENDING)
      .mockResolvedValue({ ...SENDING, status: "paused", pause_reason: "No provider" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(SENDING);

    await waitFor(() => {
      const live = Array.from(document.querySelectorAll('[aria-live="polite"]')).map((n) => n.textContent);
      expect(live).toContain(`${SENDING.name} is now paused.`);
    });
  });
});

describe("its recipients table", () => {
  it("can be reached and scrolled from the keyboard when it is wider than the screen", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(CAMPAIGN_SENT);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(CAMPAIGN_SENT);

    const region = await screen.findByRole("region", { name: "Recipients of this campaign" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(within(region).getByRole("table")).toBeTruthy();
  });
});
