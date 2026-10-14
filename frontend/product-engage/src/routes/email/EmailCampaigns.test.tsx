import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import * as email from "@/api/email";
import type { CampaignCounts } from "@/api/email";
import { CAMPAIGN_DRAFT, CAMPAIGN_PAUSED, CAMPAIGN_SCHEDULED, CAMPAIGN_SENDING, CAMPAIGN_SENT } from "@/api/fixtures";
import type { CampaignOut, CampaignStatus, Page } from "@/api/types";
import { EmailCampaignsPage } from "./EmailCampaigns";

/**
 * The campaign list: status tabs with the service's own counts, the tab asking the service for that
 * status, the scheduled state, the recipients column, and every state around them. There are no
 * Opened or Clicked columns to test because the campaign payload carries neither.
 */

const COUNTS: CampaignCounts = {
  all: 5,
  draft: 1,
  scheduled: 1,
  sending: 1,
  paused: 1,
  sent: 1,
  cancelled: 0,
};

function page(items: CampaignOut[], total = items.length): Page<CampaignOut> {
  return { items, total, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

const ALL = [CAMPAIGN_SENT, CAMPAIGN_SCHEDULED, CAMPAIGN_SENDING, CAMPAIGN_PAUSED, CAMPAIGN_DRAFT];

/** A service double: answers each status filter with the campaigns in that status. */
function serve(campaigns: CampaignOut[] = ALL) {
  return vi.spyOn(client, "listCampaigns").mockImplementation(async (options = {}) => {
    const rows = options.status === undefined ? campaigns : campaigns.filter((c) => c.status === options.status);
    return page(rows);
  });
}

function renderPage(grants = ["engage.campaign.campaign.read:ORG", "engage.campaign.campaign.write:ORG"]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/email/campaigns"]}>
        <Routes>
          <Route path="/email/campaigns" element={<EmailCampaignsPage />} />
          <Route path="/campaigns/new" element={<p>New campaign page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading", () => {
  it("says it is loading and shows placeholders", () => {
    vi.spyOn(client, "listCampaigns").mockReturnValue(new Promise(() => {}));
    vi.spyOn(email, "countCampaignsByStatus").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(screen.getByText("Loading campaigns")).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("populated", () => {
  it("shows a row per campaign with its status, recipients and delivery counts", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();

    const link = await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    expect(link.getAttribute("href")).toBe(`/campaigns/${CAMPAIGN_SENT.id}`);
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Sent")).toBeTruthy();
    expect(within(row).getByText(new Intl.NumberFormat().format(CAMPAIGN_SENT.stats.recipients))).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Recipients" })).toBeTruthy();
  });

  it("encodes the campaign id in the link to it", async () => {
    serve([{ ...CAMPAIGN_SENT, id: "camp/1?x#y" }]);
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();

    const link = await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    expect(link.getAttribute("href")).toBe("/campaigns/camp%2F1%3Fx%23y");
  });

  it("does not draw Opened or Clicked columns the campaign payload has no data for", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();
    await screen.findByRole("table");
    expect(screen.queryByRole("columnheader", { name: "Opened" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Clicked" })).toBeNull();
  });

  it("puts each status's count on its tab", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();

    await screen.findByRole("table");
    const all = await screen.findByRole("tab", { name: /^All/ });
    expect(all.textContent).toContain("5");
    expect(screen.getByRole("tab", { name: /^Cancelled/ }).textContent).toContain("0");
    expect(screen.getByRole("tab", { name: /^Scheduled/ }).textContent).toContain("1");
  });

  it("still lists campaigns, without counts, when the counts cannot be read", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockRejectedValue(new ApiError(503, "Counts down", null));
    renderPage();

    expect(await screen.findByRole("link", { name: CAMPAIGN_SENT.name })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Scheduled" })).toBeTruthy();
  });

  it("says a scheduled campaign is scheduled, and for when", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();

    const row = (await screen.findByRole("link", { name: CAMPAIGN_SCHEDULED.name })).closest("tr") as HTMLElement;
    expect(within(row).getByText(/^Scheduled for /)).toBeTruthy();
    expect(within(row).getAllByText("Scheduled").length).toBeGreaterThan(0);
  });
});

describe("tab filtering", () => {
  it("asks the service for the chosen status rather than filtering the page it has", async () => {
    const list = serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    await user.click(screen.getByRole("tab", { name: /^Scheduled/ }));

    expect(await screen.findByRole("link", { name: CAMPAIGN_SCHEDULED.name })).toBeTruthy();
    expect(screen.queryByRole("link", { name: CAMPAIGN_SENT.name })).toBeNull();
    expect(list).toHaveBeenLastCalledWith({ status: "scheduled", page: 1, size: client.FULL_PAGE });
    expect(screen.getByRole("tab", { name: /^Scheduled/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("names the tab, not the whole product, when a status has nothing", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    await user.click(screen.getByRole("tab", { name: /^Cancelled/ }));

    expect(await screen.findByText("No cancelled campaigns")).toBeTruthy();
    expect(screen.queryByText("No campaigns yet")).toBeNull();
  });

  it("searches within the tab by name", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    await user.type(screen.getByRole("searchbox", { name: "Search campaigns" }), CAMPAIGN_DRAFT.name);

    expect(screen.getByRole("link", { name: CAMPAIGN_DRAFT.name })).toBeTruthy();
    expect(screen.queryByRole("link", { name: CAMPAIGN_SENT.name })).toBeNull();

    await user.clear(screen.getByRole("searchbox", { name: "Search campaigns" }));
    await user.type(screen.getByRole("searchbox", { name: "Search campaigns" }), "no such campaign");
    expect(screen.getByText("No campaigns match that search")).toBeTruthy();
  });

  it("says when the list is only the newest part of a status", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_SENT], 350));
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage();
    expect(await screen.findByText(/This is the newest 1; pick a status to narrow the rest/)).toBeTruthy();
  });
});

describe("empty", () => {
  it("says what a campaign needs and offers to create one", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue({ ...COUNTS, all: 0 });
    renderPage();
    expect(await screen.findByText("No campaigns yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Create campaign" }).length).toBe(2);
  });

  it("hides Create campaign from someone who may not write campaigns", async () => {
    serve();
    vi.spyOn(email, "countCampaignsByStatus").mockResolvedValue(COUNTS);
    renderPage(["engage.campaign.campaign.read:ORG"]);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Create campaign" })).toBeNull();
  });
});

describe("error", () => {
  it("names the failure", async () => {
    vi.spyOn(client, "listCampaigns").mockRejectedValue(new ApiError(500, "Campaigns are down", null));
    vi.spyOn(email, "countCampaignsByStatus").mockRejectedValue(new ApiError(500, "Campaigns are down", null));
    renderPage();
    expect(await screen.findByText("Couldn't load your campaigns")).toBeTruthy();
    expect(screen.getByText("Campaigns are down")).toBeTruthy();
  });

  it("shows the not-active state for a 402", async () => {
    vi.spyOn(client, "listCampaigns").mockRejectedValue(new ApiError(402, "Not entitled", null));
    vi.spyOn(email, "countCampaignsByStatus").mockRejectedValue(new ApiError(402, "Not entitled", null));
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("the counts read", () => {
  it("counts each status from the service's own total and sums them for All", async () => {
    const totals: Record<CampaignStatus, number> = {
      draft: 2,
      scheduled: 3,
      sending: 0,
      paused: 1,
      sent: 10,
      cancelled: 4,
    };
    const list = vi
      .spyOn(client, "listCampaigns")
      .mockImplementation(async (options = {}) =>
        page([], options.status === undefined ? 0 : totals[options.status]),
      );

    const counts = await email.countCampaignsByStatus();

    expect(counts).toEqual({ ...totals, all: 20 });
    expect(list).toHaveBeenCalledWith({ status: "scheduled", page: 1, size: 1 });
  });
});
