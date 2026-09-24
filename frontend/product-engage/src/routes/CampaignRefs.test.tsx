import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_SENT } from "@/api/fixtures";
import type { CampaignOut, ContactListOut, DomainOut, Page, SenderOut } from "@/api/types";
import { CampaignPage, NewCampaignPage } from "./CampaignEditor";

/**
 * The reference data behind a campaign — its list, its sender, and the pickers for both — when the
 * organization is bigger than one page, or the caller may not read all of it. The failure these pin
 * is the quiet one: a controlled `<select>` whose value is not among its options shows some *other*
 * option, so a draft addressed to list #201 would open on list #1 and send to the wrong people.
 */

const DOMAIN: DomainOut = {
  id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
  domain: "vaayu-logistics.example.com",
  status: "verified",
  records: [],
  last_checked_at: null,
  created_at: "2026-08-28T11:04:09Z",
};

const SENDER: SenderOut = {
  id: "5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744",
  domain_id: DOMAIN.id,
  name: "Vaayu Logistics Support",
  address: "support@vaayu-logistics.example.com",
  reply_to: "",
  signature: "",
  department: "",
  usable: true,
  verified_at: "2026-08-29T09:22:31Z",
  created_at: "2026-08-29T09:18:02Z",
};

const GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
];

function listNamed(n: number): ContactListOut {
  const id = `list-${String(n).padStart(4, "0")}`;
  return {
    id,
    name: `List ${n}`,
    description: "",
    member_count: n,
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-09-18T10:12:00Z",
  };
}

/** A paged service holding `total` lists, honouring `page` and `size` as the real one does. */
function serveLists(total: number) {
  const all = Array.from({ length: total }, (_, index) => listNamed(index + 1));
  return vi.spyOn(client, "listLists").mockImplementation(({ page = 1, size = 25 } = {}) => {
    const items = all.slice((page - 1) * size, page * size);
    return Promise.resolve<Page<ContactListOut>>({
      items,
      total,
      page,
      size,
      pages: Math.ceil(total / size),
    });
  });
}

function senderNamed(n: number): SenderOut {
  return {
    ...SENDER,
    id: `sender-${String(n).padStart(4, "0")}`,
    name: `Sender ${n}`,
    address: `s${n}@vaayu-logistics.example.com`,
  };
}

function one<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

const DRAFT: CampaignOut = {
  ...CAMPAIGN_DRAFT,
  sender_id: SENDER.id,
  audience: "list",
  list_id: "list-0201",
};

afterEach(() => {
  vi.restoreAllMocks();
});

function mockCampaign(campaign: CampaignOut) {
  vi.spyOn(client, "getCampaign").mockResolvedValue(campaign);
  vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(one([]));
}

function mockSenders() {
  vi.spyOn(client, "listSenders").mockResolvedValue(one([SENDER]));
  vi.spyOn(client, "listDomains").mockResolvedValue(one([DOMAIN]));
}

function renderEditor(campaignId: string, grants: readonly string[] = GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/campaigns/${campaignId}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
          <Route path="/campaigns/new" element={<NewCampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

async function selected(label: string): Promise<string> {
  const select = (await screen.findByLabelText(label)) as HTMLSelectElement;
  return select.selectedOptions[0]?.textContent ?? "";
}

describe("an organization with more lists than one page", () => {
  it("reads every page, so the 201st list is a real option and the draft opens on it", async () => {
    mockCampaign(DRAFT);
    mockSenders();
    const lists = serveLists(250);
    const getList = vi.spyOn(client, "getList");
    renderEditor(DRAFT.id);

    expect(await selected("List")).toBe("List 201 (201)");
    expect((screen.getByLabelText("List") as HTMLSelectElement).value).toBe("list-0201");
    // Two pages of 200, and nothing read by id: the paged read reached it.
    expect(lists).toHaveBeenCalledTimes(2);
    expect(lists).toHaveBeenCalledWith({ page: 1, size: 200 });
    expect(lists).toHaveBeenCalledWith({ page: 2, size: 200 });
    expect(getList).not.toHaveBeenCalled();
    // Nothing is missing, so nothing is called partial.
    expect(screen.queryByText(/Showing .* of .* lists/)).toBeNull();

    // A list from the second page can be chosen, not just displayed.
    const options = Array.from((screen.getByLabelText("List") as HTMLSelectElement).options);
    expect(options.some((o) => o.value === "list-0250")).toBe(true);
  });

  it("names the 201st list in the send confirmation, with its size, not 'could not be found'", async () => {
    mockCampaign(DRAFT);
    mockSenders();
    serveLists(250);
    const user = userEvent.setup();
    renderEditor(DRAFT.id);

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("List 201, 201 contacts today");
    expect(dialog.textContent).not.toMatch(/could not be found|no longer exists/);
  });

  it("says a picker is a partial view past the page cap, and still opens on the campaign's own list by id", async () => {
    const target = "list-2301";
    mockCampaign({ ...DRAFT, list_id: target });
    mockSenders();
    const lists = serveLists(2_500);
    const getList = vi.spyOn(client, "getList").mockResolvedValue(listNamed(2_301));
    renderEditor(DRAFT.id);

    expect(await selected("List")).toBe("List 2301 (2301)");
    expect(lists).toHaveBeenCalledTimes(5);
    expect(getList).toHaveBeenCalledWith(target);
    expect(screen.getByText(/Showing 1,000 of 2,500 lists\./)).toBeTruthy();
    expect(screen.getByText(/The list this campaign already uses is always included/)).toBeTruthy();
    expect((screen.getByLabelText("List") as HTMLSelectElement).value).toBe(target);
  });

  it("says a list no longer exists only on a real 404 for it", async () => {
    mockCampaign({ ...DRAFT, list_id: "list-9999" });
    mockSenders();
    serveLists(3);
    vi.spyOn(client, "getList").mockRejectedValue(new ApiError(404, "No such list", null));
    const user = userEvent.setup();
    renderEditor(DRAFT.id);

    expect(await selected("List")).toBe("A list that no longer exists");
    await user.click(screen.getByRole("button", { name: "Send campaign" }));
    expect(screen.getByRole("dialog").textContent).toContain("a list that no longer exists");
  });

  it("does not claim a list is gone when the caller may not read lists, and keeps it selected", async () => {
    mockCampaign(DRAFT);
    mockSenders();
    vi.spyOn(client, "listLists").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.read", null),
    );
    vi.spyOn(client, "getList").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.read", null),
    );
    const user = userEvent.setup();
    renderEditor(DRAFT.id);

    // The select is not blank and not some other list: it keeps the campaign's own choice.
    expect(await selected("List")).toBe("The list chosen for this campaign");
    expect((screen.getByLabelText("List") as HTMLSelectElement).value).toBe("list-0201");
    const warning = screen.getByText("Couldn't load your lists").closest("div") as HTMLElement;
    expect(warning.textContent).toContain("You do not have engage.audience.list.read");
    await user.click(screen.getByRole("button", { name: "Send campaign" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("the list chosen for this campaign");
    expect(dialog.textContent).not.toMatch(/could not be found|no longer exists/);
  });
});

describe("senders", () => {
  it("keeps the campaign's sender selected and names it neutrally when senders cannot be read", async () => {
    mockCampaign({ ...DRAFT, audience: "all_sendable", list_id: null });
    vi.spyOn(client, "listSenders").mockRejectedValue(
      new ApiError(403, "You do not have engage.setup.sender.read", null),
    );
    vi.spyOn(client, "listDomains").mockResolvedValue(one([DOMAIN]));
    vi.spyOn(client, "listLists").mockResolvedValue(one([]));
    const user = userEvent.setup();
    renderEditor(DRAFT.id);

    expect(await selected("Send as")).toBe("The sender chosen for this campaign");
    expect(screen.getByText("Couldn't load your senders")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Send campaign" }));
    expect(screen.getByRole("dialog").textContent).toContain("goes out as the sender chosen for this campaign");
  });

  it("says so when there are more senders than were read", async () => {
    mockCampaign({ ...DRAFT, audience: "all_sendable", list_id: null });
    const all = Array.from({ length: 2_800 }, (_, index) => senderNamed(index + 1));
    vi.spyOn(client, "listSenders").mockImplementation(({ page = 1 } = {}) =>
      Promise.resolve({
        items: all.slice((page - 1) * 200, page * 200),
        total: 2_800,
        page,
        size: 200,
        pages: 14,
      }),
    );
    vi.spyOn(client, "listDomains").mockResolvedValue(one([DOMAIN]));
    vi.spyOn(client, "listLists").mockResolvedValue(one([]));
    renderEditor(DRAFT.id);

    await screen.findByLabelText("Send as");
    expect(screen.getByText(/Showing 1,000 of 2,800 senders\./)).toBeTruthy();
    // The campaign's own sender is on none of those pages: still selected, still named neutrally.
    expect(await selected("Send as")).toBe("The sender chosen for this campaign");
  });

  it("offers every sender, unfiltered, when the domains cannot be read", async () => {
    mockCampaign({ ...DRAFT, audience: "all_sendable", list_id: null });
    const other: SenderOut = { ...senderNamed(2), domain_id: "some-domain-we-cannot-read" };
    vi.spyOn(client, "listSenders").mockResolvedValue(one([SENDER, other]));
    vi.spyOn(client, "listDomains").mockRejectedValue(
      new ApiError(403, "You do not have engage.setup.domain.read", null),
    );
    vi.spyOn(client, "listLists").mockResolvedValue(one([]));
    renderEditor(DRAFT.id);

    const select = (await screen.findByLabelText("Send as")) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toContain(
      "Sender 2 <s2@vaayu-logistics.example.com>",
    );
  });
});

describe("a role that may read campaigns and nothing else", () => {
  it("still shows a sent campaign's report, with neutral labels where a name would be", async () => {
    mockCampaign({ ...CAMPAIGN_SENT, sender_id: SENDER.id, audience: "list", list_id: "list-0007" });
    const refused = new ApiError(403, "You do not have engage.setup.sender.read", null);
    vi.spyOn(client, "listSenders").mockRejectedValue(refused);
    vi.spyOn(client, "listDomains").mockRejectedValue(refused);
    vi.spyOn(client, "listLists").mockRejectedValue(refused);
    vi.spyOn(client, "getList").mockRejectedValue(refused);
    renderEditor(CAMPAIGN_SENT.id, ["engage.campaign.campaign.read:ORG"]);

    expect(await screen.findByRole("heading", { level: 2, name: "Delivery" })).toBeTruthy();
    expect(screen.getByText("the sender chosen for this campaign")).toBeTruthy();
    expect(screen.getByText("A list")).toBeTruthy();
    expect(screen.queryByText(/could not be found|no longer exists/)).toBeNull();
    // The rows and counts are all there.
    expect(screen.getByText("498", { selector: "dd" })).toBeTruthy();
  });
});

describe("a new campaign when only some reads succeed", () => {
  it("shows the form with a warning when lists cannot be read, and still lets you address everyone", async () => {
    mockSenders();
    vi.spyOn(client, "listLists").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.read", null),
    );
    const create = vi.spyOn(client, "createCampaign").mockResolvedValue({
      ...DRAFT,
      id: "new-id",
      audience: "all_sendable",
      list_id: null,
    });
    vi.spyOn(client, "getCampaign").mockResolvedValue({
      ...DRAFT,
      id: "new-id",
      audience: "all_sendable",
      list_id: null,
    });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(one([]));
    const user = userEvent.setup();
    render(
      <PermissionProvider grants={GRANTS}>
        <MemoryRouter initialEntries={["/campaigns/new"]}>
          <Routes>
            <Route path="/campaigns/new" element={<NewCampaignPage />} />
            <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
          </Routes>
        </MemoryRouter>
      </PermissionProvider>,
    );

    await user.type(await screen.findByLabelText("Campaign name"), "October");
    await user.selectOptions(screen.getByLabelText("Send as"), SENDER.id);
    await user.click(screen.getByRole("radio", { name: /One list/ }));
    const warning = screen.getByText("Couldn't load your lists").closest("div") as HTMLElement;
    expect(within(warning).getByText(/You do not have engage.audience.list.read/)).toBeTruthy();
    expect((screen.getByLabelText("List") as HTMLSelectElement).disabled).toBe(true);
    // The failed read is not mistaken for "no lists exist".
    expect(screen.queryByText(/Create a list under Audience/)).toBeNull();

    await user.click(screen.getByRole("radio", { name: /Everyone you may mail/ }));
    await user.click(screen.getByRole("button", { name: "Create draft" }));
    await waitFor(() => expect(create).toHaveBeenCalled());
  });
});
