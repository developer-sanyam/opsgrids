import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_PAUSED, CAMPAIGN_SENDING, CAMPAIGN_SENT, CONTACT_LISTS } from "@/api/fixtures";
import type { CampaignOut, ContactListOut, Page } from "@/api/types";
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
          <Route
            path="/campaigns"
            element={
              <>
                <CampaignsPage />
                <StateProbe />
              </>
            }
          />
          <Route path="/campaigns/new" element={<p>New campaign page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

/** Shows the router state of the current history entry, so a test can see it being cleared. */
function StateProbe() {
  const location = useLocation();
  return <output data-testid="router-state">{JSON.stringify(location.state)}</output>;
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
    // The page reads the lists it shows by id, not the organization's whole set.
    const byId = vi.spyOn(client, "getList").mockResolvedValue(CONTACT_LISTS[0] as ContactListOut);
    const paged = mockLists();
    renderPage();

    await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name });
    expect(await screen.findByText("Enterprise accounts")).toBeTruthy();
    expect(byId).toHaveBeenCalledTimes(1);
    expect(paged).not.toHaveBeenCalled();
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

  it("says a list no longer exists only when the service answers 404 for it", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    vi.spyOn(client, "getList").mockRejectedValue(new ApiError(404, "No such list", null));
    renderPage();
    expect(await screen.findByText("A list that no longer exists")).toBeTruthy();
  });

  it("does not claim a list is gone when it simply could not be read", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    vi.spyOn(client, "getList").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.read", null),
    );
    renderPage();
    expect(await screen.findByText("A list")).toBeTruthy();
    expect(screen.queryByText(/no longer exists|could not be found/)).toBeNull();
  });
});

describe("when the lists cannot be read", () => {
  it("still shows every campaign, with 'A list' where a list's name would be", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT, CAMPAIGN_SENT]));
    const refused = new ApiError(403, "You do not have engage.audience.list.read", null);
    vi.spyOn(client, "listLists").mockRejectedValue(refused);
    vi.spyOn(client, "getList").mockRejectedValue(refused);
    renderPage(["engage.campaign.campaign.read:ORG"]);

    expect(await screen.findByRole("link", { name: CAMPAIGN_DRAFT.name })).toBeTruthy();
    expect(screen.getByRole("link", { name: CAMPAIGN_SENT.name })).toBeTruthy();
    expect(screen.getByText("A list")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reads by id the list a campaign is addressed to when the paged read did not reach it", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([CAMPAIGN_DRAFT]));
    // Not the campaign's list — the paged read reached other lists only.
    vi.spyOn(client, "listLists").mockResolvedValue({ ...page([CONTACT_LISTS[1] as ContactListOut]), total: 5_000, pages: 25 });
    const byId = vi.spyOn(client, "getList").mockResolvedValue({
      ...(CONTACT_LISTS[0] as ContactListOut),
      id: CAMPAIGN_DRAFT.list_id ?? "",
      name: "List 4,201",
    });
    renderPage();

    expect(await screen.findByText("List 4,201")).toBeTruthy();
    expect(byId).toHaveBeenCalledWith(CAMPAIGN_DRAFT.list_id);
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

  it("moves focus to the page heading, because the control that caused it is gone", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    mockLists();
    renderPage(undefined, { notice: "October delivery network update deleted." });
    await screen.findByText("No campaigns yet");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).contains(document.activeElement)).toBe(true),
    );
  });

  it("is cleared from the history entry once announced, so a reload does not say it again", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    mockLists();
    renderPage(undefined, { notice: "October delivery network update deleted." });
    await screen.findByText("No campaigns yet");

    await waitFor(() => expect(screen.getByTestId("router-state").textContent).toBe("null"));
    // Announced once, and still in the live region afterwards.
    const live = Array.from(document.querySelectorAll('[aria-live="polite"]')).map((n) => n.textContent);
    expect(live).toContain("October delivery network update deleted.");
  });

  it("announces nothing for router state that is not a notice", async () => {
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([]));
    mockLists();
    renderPage(undefined, { notice: 42 });
    await screen.findByText("No campaigns yet");
    const live = Array.from(document.querySelectorAll('[aria-live="polite"]')).map((n) => n.textContent);
    expect(live.filter((text) => text !== "")).toEqual([]);
  });
});
