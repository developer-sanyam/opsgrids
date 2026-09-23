import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_PAUSED, CAMPAIGN_SENDING, CAMPAIGN_SENT, CONTACT_LISTS } from "@/api/fixtures";
import type { CampaignOut, Page } from "@/api/types";
import { CampaignsPage } from "./Campaigns";

/**
 * The campaign list: every state, a status a person can read without colour, and the filter that
 * asks the *service* for one status rather than filtering the page it happened to receive.
 */

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 100, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage(
  grants = ["engage.campaign.campaign.read:ORG", "engage.campaign.campaign.write:ORG"],
  state?: unknown,
) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[{ pathname: "/campaigns", state }]}>
        <Routes>
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/campaigns/new" element={<p>New campaign page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockLists() {
  return vi.spyOn(client, "listLists").mockResolvedValue(page(CONTACT_LISTS));
}

describe("loading", () => {
  it("shows a skeleton while the reads are in flight", () => {
    vi.spyOn(client, "listCampaigns").mockReturnValue(new Promise(() => {}));
    mockLists();
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says what a campaign needs and offers to draft one", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    mockLists();
    renderPage();
    expect(await screen.findByText("No campaigns yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "New campaign" }).length).toBe(2);
  });

  it("names the filter, not the whole product, when a status has nothing", async () => {
    const list = vi.spyOn(client, "listCampaigns").mockResolvedValueOnce(page([CAMPAIGN_DRAFT]));
    list.mockResolvedValueOnce(page([]));
    mockLists();
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    await user.selectOptions(screen.getByLabelText("Filter by status"), "cancelled");

    expect(await screen.findByText("No campaigns with that status")).toBeTruthy();
    expect(screen.queryByText("No campaigns yet")).toBeNull();
    expect(list).toHaveBeenLastCalledWith({ status: "cancelled", page: 1, size: 25 });
  });
});

describe("error", () => {
  it("shows a real failure for a 500", async () => {
    vi.spyOn(client, "listCampaigns").mockRejectedValue(new ApiError(500, "Internal error", null));
    mockLists();
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your campaigns");
    expect(alert.textContent).toContain("Internal error");
  });

  it("shows Engage-not-active on a 402, with no alert", async () => {
    vi.spyOn(client, "listCampaigns").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    mockLists();
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a 403 a permission error carrying the service's sentence", async () => {
    vi.spyOn(client, "listCampaigns").mockRejectedValue(
      new ApiError(403, "You do not have engage.campaign.campaign.read", null),
    );
    mockLists();
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("You do not have engage.campaign.campaign.read");
    expect(screen.queryByText("Engage isn't active for this organization yet")).toBeNull();
  });
});

describe("populated", () => {
  const ALL: CampaignOut[] = [CAMPAIGN_DRAFT, CAMPAIGN_SENDING, CAMPAIGN_PAUSED, CAMPAIGN_SENT];

  it("shows every status in words, each campaign as a link to its own page", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page(ALL));
    mockLists();
    renderPage();

    const link = await screen.findByRole("link", { name: CAMPAIGN_SENDING.name });
    expect(link.getAttribute("href")).toBe(`/campaigns/${CAMPAIGN_SENDING.id}`);
    for (const label of ["Draft", "Sending", "Paused — needs attention", "Sent"]) {
      // The label is also a filter option and, for "Sent", a column heading: read table cells.
      const cells = screen.getAllByText(label).filter((node) => node.closest("td") !== null);
      expect(cells.length, label).toBe(1);
    }
  });

  it("names the audience: a list by name, everyone in plain words", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page(ALL));
    mockLists();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    expect(screen.getByText("Enterprise accounts")).toBeTruthy();
    expect(screen.getAllByText("Everyone you may mail").length).toBe(3);
  });

  it("shows sent-of-recipients and the failed count, with a dash where nothing failed", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_SENDING, CAMPAIGN_DRAFT]));
    mockLists();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_SENDING.name });
    expect(screen.getByText("55 / 240")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("says so when a campaign's list has since gone", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("A list that could not be found")).toBeTruthy();
  });
});

describe("filtering", () => {
  it("asks the service for the chosen status instead of filtering the page", async () => {
    const list = vi
      .spyOn(client, "listCampaigns")
      .mockResolvedValueOnce(page([CAMPAIGN_DRAFT, CAMPAIGN_PAUSED]))
      .mockResolvedValueOnce(page([CAMPAIGN_PAUSED]));
    mockLists();
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    await user.selectOptions(screen.getByLabelText("Filter by status"), "paused");

    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ status: "paused", page: 1, size: 25 }));
    await waitFor(() => expect(screen.queryByRole("link", { name: CAMPAIGN_DRAFT.name })).toBeNull());
    expect(screen.getByRole("link", { name: CAMPAIGN_PAUSED.name })).toBeTruthy();
  });
});

describe("paging", () => {
  it("reads the next page from the service and keeps the filter", async () => {
    const list = vi
      .spyOn(client, "listCampaigns")
      .mockResolvedValueOnce({ items: [CAMPAIGN_DRAFT], total: 2, page: 1, size: 1, pages: 2 })
      .mockResolvedValueOnce({ items: [CAMPAIGN_SENT], total: 2, page: 2, size: 1, pages: 2 });
    mockLists();
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    await user.click(screen.getByRole("button", { name: "Next page" }));

    expect(await screen.findByRole("link", { name: CAMPAIGN_SENT.name })).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith({ page: 2, size: 25 });
  });

  it("goes back to the first page when the filter changes", async () => {
    const list = vi
      .spyOn(client, "listCampaigns")
      .mockResolvedValueOnce({ items: [CAMPAIGN_DRAFT], total: 2, page: 1, size: 1, pages: 2 })
      .mockResolvedValueOnce({ items: [CAMPAIGN_SENT], total: 2, page: 2, size: 1, pages: 2 })
      .mockResolvedValue(page([CAMPAIGN_PAUSED]));
    mockLists();
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("link", { name: CAMPAIGN_SENT.name });
    await user.selectOptions(screen.getByLabelText("Filter by status"), "paused");

    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ status: "paused", page: 1, size: 25 }));
  });
});

describe("permission gating", () => {
  it("offers New campaign to a caller who can draft", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    mockLists();
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "New campaign" }));
    expect(await screen.findByText("New campaign page")).toBeTruthy();
  });

  it("hides New campaign, and only that, from a read-only caller", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    mockLists();
    renderPage(["engage.campaign.campaign.read:ORG"]);
    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    expect(screen.queryByRole("button", { name: "New campaign" })).toBeNull();
  });
});

describe("a notice handed over by the screen that navigated here", () => {
  it("is announced in this screen's live region", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    mockLists();
    renderPage(undefined, { notice: "October delivery network update deleted." });
    await screen.findByText("No campaigns yet");
    await waitFor(() => {
      const live = Array.from(document.querySelectorAll('[aria-live="polite"]')).map((n) => n.textContent);
      expect(live).toContain("October delivery network update deleted.");
    });
  });
});
