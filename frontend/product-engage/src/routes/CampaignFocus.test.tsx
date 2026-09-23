import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_SENDING } from "@/api/fixtures";
import type { CampaignOut, Page } from "@/api/types";
import { CampaignPage } from "./CampaignEditor";

/**
 * A status change the *service* makes — a poll that finds a sending campaign sent — must not move
 * the focus of someone who did nothing and may be anywhere on the page. It moves it only where it
 * would be lost anyway: nothing focused, or focus on something the re-read is about to replace. And
 * that is decided when the change is requested, not later.
 */

const SENDING: CampaignOut = { ...CAMPAIGN_SENDING };
const SENT: CampaignOut = {
  ...CAMPAIGN_SENDING,
  status: "sent",
  completed_at: "2026-09-23T07:00:00Z",
};

const GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

/** The page loads as `sending`; the poll's answer is held until the test releases it as `sent`. */
function holdThePoll() {
  let release: (campaign: CampaignOut) => void = () => {};
  vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([]));
  vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(
    page([
      {
        id: "r1",
        contact_id: "c1",
        email: "p•••@vaayu-logistics.example.com",
        status: "sent",
        provider_message_id: "smtp-1",
        error: "",
        sent_at: "2026-09-23T06:05:00Z",
      },
    ]),
  );
  vi.spyOn(client, "getCampaign")
    .mockResolvedValueOnce(SENDING) // the page's own read
    .mockReturnValueOnce(
      new Promise<CampaignOut>((resolve) => {
        release = resolve;
      }),
    ) // the poll's first read, held
    .mockResolvedValue(SENT); // the reload after the change
  return (campaign: CampaignOut) => release(campaign);
}

function renderReport() {
  return render(
    <PermissionProvider grants={GRANTS}>
      <MemoryRouter initialEntries={[`/campaigns/${SENDING.id}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
          <Route path="/campaigns" element={<p>All campaigns page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

async function settledAsSent() {
  await waitFor(() => expect(screen.queryByText(/Counts refresh every few seconds/)).toBeNull());
  await screen.findByRole("heading", { level: 2, name: "Delivery" });
  await waitFor(() => expect(screen.getByText("Finished")).toBeTruthy());
}

describe("a change the service made while the person was elsewhere", () => {
  it("leaves focus on the link they were on", async () => {
    const release = holdThePoll();
    renderReport();
    const back = await screen.findByRole("link", { name: "All campaigns" });
    await screen.findByRole("heading", { level: 2, name: "Delivery" });

    back.focus();
    expect(document.activeElement).toBe(back);
    release(SENT);
    await settledAsSent();

    expect(document.activeElement).toBe(back);
  });

  it("leaves focus in the sidebar or anywhere else outside the campaign's own content", async () => {
    const release = holdThePoll();
    renderReport();
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    // Something outside the page's content, as the app's navigation rail is.
    const outside = document.createElement("button");
    outside.textContent = "Elsewhere";
    document.body.appendChild(outside);
    outside.focus();

    release(SENT);
    await settledAsSent();

    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("does take focus when nothing was focused, because there is nothing to lose", async () => {
    const release = holdThePoll();
    renderReport();
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    release(SENT);
    await settledAsSent();

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 })));
  });

  it("does take focus when it was on something the re-read is about to replace", async () => {
    const release = holdThePoll();
    renderReport();
    const region = await screen.findByRole("region", { name: "Recipients of this campaign" });
    region.focus();
    expect(document.activeElement).toBe(region);

    release(SENT);
    await settledAsSent();

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 })));
  });

  it("does take focus when it was inside a dialog that the re-read replaces", async () => {
    const release = holdThePoll();
    renderReport();
    const cancel = await screen.findByRole("button", { name: "Cancel campaign" });
    cancel.click();
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    release(SENT);
    await settledAsSent();

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 })));
  });
});
