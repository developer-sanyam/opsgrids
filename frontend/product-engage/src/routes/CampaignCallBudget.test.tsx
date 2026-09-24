import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_SENT } from "@/api/fixtures";
import type { CampaignOut, ContactListOut, DomainOut, Page, SenderOut } from "@/api/types";
import { CampaignPage } from "./CampaignEditor";
import { CampaignsPage } from "./Campaigns";

/**
 * How many calls a screen spends on reference data. The gateway allows 300 requests a minute a
 * user; before these limits a campaigns page could spend about a hundred of them naming a handful
 * of lists, so what is pinned here is a *budget*: a typical editor view is four calls, a report
 * three, a campaigns page one per distinct list it shows, and a caller who may not read lists is
 * told so by one refusal rather than by every read already in flight.
 */

const DOMAIN: DomainOut = {
  id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
  domain: "vaayu-logistics.example.com",
  status: "verified",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: [],
  last_checked_at: null,
  created_at: "2026-08-28T11:04:09Z",
};

function sender(n: number): SenderOut {
  return {
    id: `sender-${String(n).padStart(4, "0")}`,
    domain_id: DOMAIN.id,
    name: `Sender ${n}`,
    address: `s${n}@vaayu-logistics.example.com`,
    reply_to: "",
    signature: "",
    department: "",
    usable: true,
    verified_at: "2026-08-29T09:22:31Z",
    created_at: "2026-08-29T09:18:02Z",
  };
}

function list(n: number): ContactListOut {
  return {
    id: `list-${String(n).padStart(4, "0")}`,
    name: `List ${n}`,
    description: "",
    member_count: n,
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-09-18T10:12:00Z",
  };
}

const SENDER = sender(1);
const LIST = list(1);

const DRAFT: CampaignOut = {
  ...CAMPAIGN_DRAFT,
  sender_id: SENDER.id,
  audience: "list",
  list_id: LIST.id,
};

const GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
];

function one<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

/** Every read a campaign screen can make, counted. */
function spies() {
  return {
    campaign: vi.spyOn(client, "getCampaign"),
    senders: vi.spyOn(client, "listSenders"),
    domains: vi.spyOn(client, "listDomains"),
    lists: vi.spyOn(client, "listLists"),
    byId: vi.spyOn(client, "getList"),
  };
}

function renderEditor(campaign: CampaignOut) {
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

describe("the editor", () => {
  it("costs four calls for a typical draft, and none of them by id", async () => {
    const reads = spies();
    reads.campaign.mockResolvedValue(DRAFT);
    reads.senders.mockResolvedValue(one([SENDER]));
    reads.domains.mockResolvedValue(one([DOMAIN]));
    reads.lists.mockResolvedValue(one([LIST]));
    renderEditor(DRAFT);

    await screen.findByLabelText("Subject");
    expect(reads.campaign).toHaveBeenCalledTimes(1);
    expect(reads.senders).toHaveBeenCalledTimes(1);
    expect(reads.domains).toHaveBeenCalledTimes(1);
    expect(reads.lists).toHaveBeenCalledTimes(1);
    expect(reads.byId).not.toHaveBeenCalled();
    const total = Object.values(reads).reduce((sum, spy) => sum + spy.mock.calls.length, 0);
    expect(total).toBeLessThanOrEqual(4);
  });

  it("costs three calls for a sent campaign's report, and never reads the domains or the paged lists", async () => {
    const sent: CampaignOut = { ...CAMPAIGN_SENT, sender_id: SENDER.id, audience: "list", list_id: LIST.id };
    const reads = spies();
    reads.campaign.mockResolvedValue(sent);
    reads.senders.mockResolvedValue(one([SENDER]));
    reads.byId.mockResolvedValue(LIST);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(one([]));
    renderEditor(sent);

    await screen.findByText("List 1");
    // The campaign, its sender (to name who it went as), and its own list by id: three.
    expect(reads.campaign).toHaveBeenCalledTimes(1);
    expect(reads.senders).toHaveBeenCalledTimes(1);
    expect(reads.byId).toHaveBeenCalledTimes(1);
    // A report is not a form: nothing to pick from, so nothing to read to fill a picker.
    expect(reads.domains).not.toHaveBeenCalled();
    expect(reads.lists).not.toHaveBeenCalled();
  });

  it.each([401, 402, 403])(
    "makes no by-id read of a campaign's list when the paged read was refused with a %i",
    async (status) => {
      const reads = spies();
      reads.campaign.mockResolvedValue(DRAFT);
      reads.senders.mockResolvedValue(one([SENDER]));
      reads.domains.mockResolvedValue(one([DOMAIN]));
      reads.lists.mockRejectedValue(new ApiError(status, "refused", null));
      reads.byId.mockRejectedValue(new ApiError(status, "refused", null));
      renderEditor(DRAFT);

      expect(await screen.findByText("Couldn't load your lists")).toBeTruthy();
      // The same permission would be refused the same way: nothing to learn by asking again.
      expect(reads.byId).not.toHaveBeenCalled();
    },
  );

  it("does still read the list by id when the paged read failed for some other reason", async () => {
    const reads = spies();
    reads.campaign.mockResolvedValue(DRAFT);
    reads.senders.mockResolvedValue(one([SENDER]));
    reads.domains.mockResolvedValue(one([DOMAIN]));
    reads.lists.mockRejectedValue(new ApiError(500, "Internal error", null));
    reads.byId.mockResolvedValue(LIST);
    renderEditor(DRAFT);

    await waitFor(() => expect(reads.byId).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Couldn't load your lists")).toBeTruthy();
  });
});

describe("the campaigns page", () => {
  function fifty(distinctLists: number): CampaignOut[] {
    return Array.from({ length: 50 }, (_, index) => ({
      ...CAMPAIGN_SENT,
      id: `campaign-${index}`,
      name: `Campaign ${index}`,
      audience: "list" as const,
      list_id: list((index % distinctLists) + 1).id,
    }));
  }

  function renderPage() {
    return render(
      <PermissionProvider grants={GRANTS}>
        <MemoryRouter initialEntries={["/campaigns"]}>
          <Routes>
            <Route path="/campaigns" element={<CampaignsPage />} />
          </Routes>
        </MemoryRouter>
      </PermissionProvider>,
    );
  }

  it("reads one list per distinct list on the page — three for fifty rows over three lists — and pages through none", async () => {
    const reads = spies();
    reads.campaign.mockResolvedValue(CAMPAIGN_SENT);
    const rows = fifty(3);
    vi.spyOn(client, "listCampaigns").mockResolvedValue({
      items: rows,
      total: 50,
      page: 1,
      size: 50,
      pages: 1,
    });
    reads.byId.mockImplementation((id: string) => Promise.resolve(list(Number(id.slice(-4)))));
    renderPage();

    expect(await screen.findAllByText("List 1")).toHaveLength(17);
    expect(reads.byId).toHaveBeenCalledTimes(3);
    expect(reads.lists).not.toHaveBeenCalled();
  });

  it("has at most four by-id reads in flight at once, and reads the first alone", async () => {
    const reads = spies();
    vi.spyOn(client, "listCampaigns").mockResolvedValue({
      items: fifty(12),
      total: 50,
      page: 1,
      size: 50,
      pages: 1,
    });
    let inFlight = 0;
    let peak = 0;
    const starts: number[] = [];
    reads.byId.mockImplementation(async (id: string) => {
      starts.push(inFlight);
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return list(Number(id.slice(-4)));
    });
    renderPage();

    await waitFor(() => expect(reads.byId).toHaveBeenCalledTimes(12));
    await screen.findAllByText(/^List \d+$/);
    expect(peak).toBeLessThanOrEqual(4);
    // The first read went out with nothing else in flight: it is the probe.
    expect(starts[0]).toBe(0);
    expect(starts[1]).toBe(0);
  });

  it("is refused once, not once per list, when the caller may not read lists", async () => {
    const reads = spies();
    vi.spyOn(client, "listCampaigns").mockResolvedValue({
      items: fifty(12),
      total: 50,
      page: 1,
      size: 50,
      pages: 1,
    });
    reads.byId.mockRejectedValue(new ApiError(403, "You do not have engage.audience.list.read", null));
    renderPage();

    expect(await screen.findByRole("link", { name: "Campaign 0" })).toBeTruthy();
    // Twelve distinct lists, and one refusal was enough to know.
    expect(reads.byId).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText("A list").length).toBe(50);
  });
});

describe("reading every page of a reference list", () => {
  it("has at most two pages in flight at once, however many there are", async () => {
    const reads = spies();
    reads.campaign.mockResolvedValue({ ...DRAFT, audience: "all_sendable", list_id: null });
    reads.senders.mockResolvedValue(one([SENDER]));
    reads.domains.mockResolvedValue(one([DOMAIN]));
    let inFlight = 0;
    let peak = 0;
    reads.lists.mockImplementation(async ({ page = 1 } = {}) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return {
        items: Array.from({ length: 200 }, (_, index) => list((page - 1) * 200 + index + 1)),
        total: 1_000,
        page,
        size: 200,
        pages: 5,
      };
    });
    renderEditor({ ...DRAFT, audience: "all_sendable", list_id: null });

    await screen.findByLabelText("Subject");
    expect(reads.lists).toHaveBeenCalledTimes(5);
    expect(peak).toBeLessThanOrEqual(2);
  });

  it("offers a sender once when it turns up on two pages, because an offset page can repeat a row", async () => {
    const reads = spies();
    reads.campaign.mockResolvedValue({ ...DRAFT, audience: "all_sendable", list_id: null });
    reads.domains.mockResolvedValue(one([DOMAIN]));
    reads.lists.mockResolvedValue(one([]));
    // 300 exist. The second page repeats the last row of the first — a row written between the two
    // requests shifted it across the boundary.
    reads.senders.mockImplementation(({ page = 1 } = {}) =>
      Promise.resolve({
        items:
          page === 1
            ? Array.from({ length: 200 }, (_, index) => sender(index + 1))
            : Array.from({ length: 100 }, (_, index) => sender(index + 200)),
        total: 300,
        page,
        size: 200,
        pages: 2,
      }),
    );
    renderEditor({ ...DRAFT, audience: "all_sendable", list_id: null });

    const select = (await screen.findByLabelText("Send as")) as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values.filter((v) => v === "sender-0200")).toHaveLength(1);
    expect(new Set(values).size).toBe(values.length);
    // 299 distinct rows were read of 300: the picker says it is not the whole set.
    expect(screen.getByText(/Showing 299 of 300 senders\./)).toBeTruthy();
  });
});
