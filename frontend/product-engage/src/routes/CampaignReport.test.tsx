import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_PAUSED, CAMPAIGN_SCHEDULED, CAMPAIGN_SENDING, CAMPAIGN_SENT } from "@/api/fixtures";
import type {
  CampaignOut,
  CampaignRecipientOut,
  ContactListOut,
  DomainOut,
  Page,
  SenderOut,
} from "@/api/types";
import { CampaignPage } from "./CampaignEditor";

/**
 * A campaign that has left `draft`. The point of this screen is that it reports what the service
 * folds and nothing more: five counts, an outcome per recipient, and no delivered / bounced /
 * unsubscribed figure the service does not have — with the note that says so.
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

const LIST: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "",
  member_count: 2,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

const SENT: CampaignOut = { ...CAMPAIGN_SENT, sender_id: SENDER.id };
const SENDING: CampaignOut = { ...CAMPAIGN_SENDING, sender_id: SENDER.id };
const PAUSED: CampaignOut = { ...CAMPAIGN_PAUSED, sender_id: SENDER.id };
const SCHEDULED: CampaignOut = { ...CAMPAIGN_SCHEDULED, sender_id: SENDER.id };

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
  {
    id: "r2",
    contact_id: "c2",
    email: "d•••@vaayu-freight.example.com",
    status: "failed",
    provider_message_id: null,
    error: "535 5.7.8 Username and Password not accepted",
    sent_at: null,
  },
  {
    id: "r3",
    contact_id: "c3",
    email: "a•••@vaayu-freight.example.com",
    status: "queued",
    provider_message_id: null,
    error: "",
    sent_at: null,
  },
];

const READ_WRITE = ["engage.campaign.campaign.read:ORG", "engage.campaign.campaign.write:ORG"];
const ALL = [...READ_WRITE, "engage.campaign.campaign.send:ORG"];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function mockRefs() {
  vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
}

function renderReport(campaign: CampaignOut, grants: readonly string[] = ALL) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/campaigns/${campaign.id}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
          <Route path="/suppressions" element={<p>Suppressions page</p>} />
          <Route path="/connection" element={<p>Connections page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

/** The value under a count's label in the delivery card. */
function count(label: string): string | null {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling?.textContent ?? null;
}

describe("a cold email to CRM leads", () => {
  it("says so, and an ordinary campaign does not", async () => {
    mockRefs();
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    vi.spyOn(client, "getCampaign").mockResolvedValue({ ...SENT, cold_outreach: true });
    const { unmount } = renderReport(SENT);
    expect(await screen.findByText(/Cold email to CRM leads/)).toBeTruthy();
    unmount();

    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    renderReport(SENT);
    await screen.findByText("Addressed to");
    expect(screen.queryByText(/Cold email to CRM leads/)).toBeNull();
  });
});

describe("a sent campaign", () => {
  function mockSent() {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    return vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
  }

  it("says which version of an A/B test won, and shows version B beside A", async () => {
    mockRefs();
    const ab: CampaignOut = {
      ...SENT,
      ab_test: true,
      subject_b: "Version B subject line",
      html_body_b: "",
      text_body_b: "Version B text",
      ab_split_percent: 10,
      ab_metric: "click",
      ab_wait_minutes: 240,
      ab_winner: "b",
      ab_decided_at: "2026-10-05T14:00:00Z",
    };
    vi.spyOn(client, "getCampaign").mockResolvedValue(ab);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(ab);

    expect(await screen.findByText(/A\/B test: version B won on click rate/)).toBeTruthy();
    expect(screen.getByText("Version B subject line")).toBeTruthy();
  });

  it("reports the five counts the service folds, and how far along the audience is", async () => {
    mockSent();
    renderReport(SENT);

    expect(await screen.findByRole("heading", { level: 2, name: "Delivery" })).toBeTruthy();
    expect(count("Recipients")).toBe("512");
    expect(count("Queued")).toBe("0");
    expect(count("Sent")).toBe("498");
    expect(count("Failed")).toBe("9");
    expect(count("Suppressed")).toBe("5");
    expect(screen.getByText("512 of 512 recipients dealt with.")).toBeTruthy();
  });

  it("shows the status in words and the lifecycle's own timestamps", async () => {
    mockSent();
    renderReport(SENT);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    const card = screen.getByRole("heading", { level: 2, name: "Delivery" }).closest("div") as HTMLElement;
    expect(within(card).getByText("Sent")).toBeTruthy();
    expect(screen.getByText("Drafted")).toBeTruthy();
    expect(screen.getByText("Started sending")).toBeTruthy();
    expect(screen.getByText("Finished")).toBeTruthy();
    expect(screen.queryByText("Cancelled", { selector: "dt" })).toBeNull();
  });

  it("does not invent delivered, bounced or unsubscribed counts, and says where those go", async () => {
    mockSent();
    renderReport(SENT);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });

    for (const label of ["Delivered", "Bounced", "Unsubscribed", "Opened", "Clicked"]) {
      expect(screen.queryByText(label, { selector: "dt" }), label).toBeNull();
    }
    expect(screen.getByText(/not that it reached an inbox/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Suppressions" }).getAttribute("href")).toBe("/suppressions");
  });

  it("offers no form to edit, no send, and no cancel: what went out is a record", async () => {
    mockSent();
    renderReport(SENT);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send campaign" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel campaign" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete draft" })).toBeNull();
  });

  it("shows what was sent, as whom and to whom, without running any of it", async () => {
    const listed: CampaignOut = { ...SENT, audience: "list", list_id: LIST.id };
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(listed);
    vi.spyOn(client, "getList").mockResolvedValue(LIST);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(listed);
    await screen.findByRole("heading", { level: 2, name: "What was sent" });
    await screen.findByText("Enterprise accounts");
    expect(screen.getByText("Vaayu Logistics Support <support@vaayu-logistics.example.com>")).toBeTruthy();
    expect(screen.getByText("Enterprise accounts")).toBeTruthy();
    const frame = screen.getByTitle("HTML preview of the campaign body");
    expect(frame.getAttribute("sandbox")).toBe("");
    // The outline nests: the preview is a subsection of "What was sent", not a second h2.
    expect(screen.getByRole("heading", { level: 3, name: "Preview" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Preview" })).toBeNull();
  });

  it("says plainly when a sent campaign reached nobody", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue({
      ...SENT,
      stats: { recipients: 0, queued: 0, sent: 0, failed: 0, suppressed: 0 },
    });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(SENT);

    expect(
      await screen.findByText("Sent to 0 recipients — nobody on the audience could be emailed"),
    ).toBeTruthy();
  });

  it("says nothing extra for a sent campaign that actually reached its audience", async () => {
    mockSent();
    renderReport(SENT);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    expect(
      screen.queryByText("Sent to 0 recipients — nobody on the audience could be emailed"),
    ).toBeNull();
  });

  it("says so when the sender or the list has since gone", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    const orphan = { ...SENT, audience: "list" as const, list_id: LIST.id };
    vi.spyOn(client, "getCampaign").mockResolvedValue(orphan);
    vi.spyOn(client, "getList").mockRejectedValue(new ApiError(404, "No such list", null));
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(orphan);
    // Every sender was read and this one is not among them; the service said 404 for the list.
    expect(await screen.findByText("a sender that no longer exists")).toBeTruthy();
    expect(screen.getByText("A list that no longer exists")).toBeTruthy();
  });
});

describe("its recipients", () => {
  it("shows masked addresses, an outcome in words, and the provider's own reason for a failure", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(SENT);

    expect(await screen.findByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
    expect(screen.getByText("d•••@vaayu-freight.example.com")).toBeTruthy();
    expect(screen.getByText("535 5.7.8 Username and Password not accepted")).toBeTruthy();
    const table = screen.getByRole("table", { name: /Recipients of this campaign/ });
    for (const label of ["Sent", "Failed", "Queued"]) {
      expect(within(table).getAllByText(label).length, label).toBeGreaterThan(0);
    }
  });

  it("asks the service for one outcome at a time", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    const list = vi
      .spyOn(client, "listCampaignRecipients")
      .mockResolvedValueOnce(page(ROWS))
      .mockResolvedValueOnce(page([ROWS[1] as CampaignRecipientOut]));
    const user = userEvent.setup();
    renderReport(SENT);

    await screen.findByText("p•••@vaayu-logistics.example.com");
    await user.selectOptions(screen.getByLabelText("Filter by outcome"), "failed");

    await waitFor(() => expect(list).toHaveBeenLastCalledWith(SENT.id, { status: "failed", page: 1, size: 25 }));
    await waitFor(() => expect(screen.queryByText("p•••@vaayu-logistics.example.com")).toBeNull());
    expect(screen.getByText("d•••@vaayu-freight.example.com")).toBeTruthy();
  });

  it("says nobody was addressed for a settled campaign with no recipients", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue({ ...SENT, stats: { recipients: 0, queued: 0, sent: 0, failed: 0, suppressed: 0 } });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(SENT);
    expect(await screen.findByText("No recipients yet")).toBeTruthy();
    expect(screen.getByText(/Nobody was addressed/)).toBeTruthy();
  });

  it("says recipients appear as the send works for a live campaign with none yet", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENDING);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(SENDING);
    expect(await screen.findByText(/Recipients appear here as the send works/)).toBeTruthy();
  });

  it("names the filter, not the campaign, when an outcome has no rows", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValueOnce(page(ROWS)).mockResolvedValueOnce(page([]));
    const user = userEvent.setup();
    renderReport(SENT);

    await screen.findByText("p•••@vaayu-logistics.example.com");
    await user.selectOptions(screen.getByLabelText("Filter by outcome"), "suppressed");
    expect(await screen.findByText("No recipients with that outcome")).toBeTruthy();
  });

  it("shows a failure to read them without touching the counts above", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    vi.spyOn(client, "listCampaignRecipients").mockRejectedValue(new ApiError(500, "Internal error", null));
    renderReport(SENT);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load this campaign's recipients");
    expect(count("Sent")).toBe("498");
  });

  it("shows Engage-not-active for the recipients on a 402", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    vi.spyOn(client, "listCampaignRecipients").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderReport(SENT);
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });

  it("keeps a 403 on the recipients an error carrying the service's sentence", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENT);
    vi.spyOn(client, "listCampaignRecipients").mockRejectedValue(
      new ApiError(403, "You do not have engage.campaign.campaign.read", null),
    );
    renderReport(SENT);
    expect((await screen.findByRole("alert")).textContent).toContain("You do not have engage.campaign.campaign.read");
  });
});

describe("a campaign that is sending", () => {
  it("says it is live and refreshes the counts from the service", async () => {
    mockRefs();
    const get = vi
      .spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SENDING)
      .mockResolvedValue({ ...SENDING, stats: { ...SENDING.stats, queued: 170, sent: 65 } });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(SENDING);

    // The page's own read, then the poll's first read.
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(count("Sent")).toBe("65"));
    expect(screen.getByText(/Counts refresh every few seconds/)).toBeTruthy();
    // Stamped by an effect once the poll's answer is on screen, so it arrives a render later.
    expect(await screen.findByText(/Last updated/)).toBeTruthy();
  });

  it("reloads into the settled screen when the status moves on under it", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SENDING)
      .mockResolvedValueOnce({ ...SENDING, status: "sent", completed_at: "2026-09-23T07:00:00Z" })
      .mockResolvedValue({ ...SENDING, status: "sent", completed_at: "2026-09-23T07:00:00Z" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    renderReport(SENDING);

    // Both at once: "Sent" alone is true for the instant the live screen shows the polled status
    // before the reload lands, and "no live note" alone is true while the page is loading.
    await waitFor(() => {
      const delivery = screen.getByRole("heading", { level: 2, name: "Delivery" });
      expect(within(delivery.closest("div") as HTMLElement).getByText("Sent")).toBeTruthy();
      expect(screen.queryByText(/Counts refresh every few seconds/)).toBeNull();
    });
    expect(screen.queryByRole("button", { name: "Cancel campaign" })).toBeNull();
  });
});

describe("cancelling", () => {
  it("asks on the page, says what cannot be recalled, then cancels once", async () => {
    mockRefs();
    const confirm = vi.spyOn(window, "confirm");
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SENDING)
      .mockResolvedValueOnce(SENDING)
      .mockResolvedValue({ ...SENDING, status: "cancelled", cancelled_at: "2026-09-23T06:30:00Z" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page(ROWS));
    const cancel = vi.spyOn(client, "cancelCampaign").mockResolvedValue({ ...SENDING, status: "cancelled" });
    const user = userEvent.setup();
    renderReport(SENDING);

    await user.click(await screen.findByRole("button", { name: "Cancel campaign" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Recipients that have not been sent yet will never be sent");
    expect(dialog.textContent).toContain("55 so far");
    expect(cancel).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    // The safe answer has words that say what it does, and holds focus.
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Keep sending" }));

    await user.click(within(dialog).getByRole("button", { name: "Cancel campaign" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
    expect(cancel).toHaveBeenCalledWith(SENDING.id);
    expect(await screen.findByText("Cancelled", { selector: "dt" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel campaign" })).toBeNull();
  });

  it("shows the service's sentence and keeps the campaign when the cancel is refused", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENDING);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    vi.spyOn(client, "cancelCampaign").mockRejectedValue(new ApiError(409, "A sent campaign cannot be cancelled", null));
    const user = userEvent.setup();
    renderReport(SENDING);

    await user.click(await screen.findByRole("button", { name: "Cancel campaign" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel campaign" }));
    expect(await within(dialog).findByText("A sent campaign cannot be cancelled")).toBeTruthy();
  });

  it("is offered to someone who can write, and to nobody who can only read", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(SENDING);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(SENDING, ["engage.campaign.campaign.read:ORG"]);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    expect(screen.queryByRole("button", { name: "Cancel campaign" })).toBeNull();
  });
});

describe("a paused campaign", () => {
  function mockPaused() {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(PAUSED);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
  }

  it("says it is not sending, gives the driver's reason verbatim and where to fix it", async () => {
    mockPaused();
    renderReport(PAUSED);

    expect(await screen.findByText("This campaign is paused and is not sending")).toBeTruthy();
    expect(screen.getByText(PAUSED.pause_reason)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Connections" }).getAttribute("href")).toBe("/connection");
    expect(screen.getByRole("link", { name: "Senders" }).getAttribute("href")).toBe("/senders");
    expect(screen.getByText("Paused — needs attention", { selector: "span" })).toBeTruthy();
  });

  it("resumes on request and announces it", async () => {
    mockPaused();
    const resume = vi.spyOn(client, "resumeCampaign").mockResolvedValue({ ...PAUSED, status: "sending" });
    const user = userEvent.setup();
    renderReport(PAUSED);

    await user.click(await screen.findByRole("button", { name: "Resume now" }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith(PAUSED.id));
  });

  it("shows the service's refusal when there is still nothing to resume on", async () => {
    mockPaused();
    vi.spyOn(client, "resumeCampaign").mockRejectedValue(
      new ApiError(409, "Only a paused campaign can be resumed; this one is sending", null),
    );
    const user = userEvent.setup();
    renderReport(PAUSED);

    await user.click(await screen.findByRole("button", { name: "Resume now" }));
    const alert = await screen.findByText("Only a paused campaign can be resumed; this one is sending");
    expect(alert.getAttribute("role")).toBe("alert");
  });

  it("offers Resume only to someone who may send", async () => {
    mockPaused();
    renderReport(PAUSED, READ_WRITE);
    await screen.findByText("This campaign is paused and is not sending");
    expect(screen.queryByRole("button", { name: "Resume now" })).toBeNull();
    // The reason is still there: hiding a control never hides the explanation.
    expect(screen.getByText(PAUSED.pause_reason)).toBeTruthy();
  });

  it("falls back to a plain sentence when the driver recorded no reason", async () => {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue({ ...PAUSED, pause_reason: "" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    renderReport(PAUSED);
    expect(await screen.findByText("The send driver could not find a route to send on.")).toBeTruthy();
  });
});

describe("a scheduled campaign", () => {
  function mockScheduled(campaign: CampaignOut = SCHEDULED) {
    mockRefs();
    vi.spyOn(client, "getCampaign").mockResolvedValue(campaign);
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
  }

  it("says when it goes out and offers to change it, beside Cancel", async () => {
    mockScheduled();
    renderReport(SCHEDULED);

    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    // Scoped to the `<p>`: the timeline below also has a "Scheduled for" label, on a `<dt>`.
    expect(screen.getByText(/Scheduled for/, { selector: "p" })).toBeTruthy();
    // 2026-10-15T12:30:00Z is 15 Oct, 6:00 pm IST.
    expect(screen.getByText("15 Oct, 6:00 pm IST", { selector: "span" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reschedule" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel campaign" })).toBeTruthy();
  });

  it("hides Reschedule from a caller who can write but not send, without hiding Cancel", async () => {
    mockScheduled();
    renderReport(SCHEDULED, READ_WRITE);
    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    expect(screen.queryByRole("button", { name: "Reschedule" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel campaign" })).toBeTruthy();
  });

  it("reschedules through the same dialog and reloads the page on success", async () => {
    mockScheduled();
    const rescheduled: CampaignOut = { ...SCHEDULED, scheduled_at: "2026-11-01T09:00:00Z" };
    const schedule = vi.spyOn(client, "scheduleCampaign").mockResolvedValue(rescheduled);
    vi.spyOn(client, "getCampaign").mockResolvedValueOnce(SCHEDULED).mockResolvedValue(rescheduled);
    const user = userEvent.setup();
    renderReport(SCHEDULED);

    await user.click(await screen.findByRole("button", { name: "Reschedule" }));
    expect(screen.getByRole("heading", { name: "Reschedule this campaign" })).toBeTruthy();
    const field = screen.getByLabelText("Send at") as HTMLInputElement;
    // The dialog opens on the campaign's current schedule, not blank.
    expect(field.value).toBe("2026-10-15T18:00");

    fireEvent.change(field, { target: { value: "2099-11-01T14:30" } });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Reschedule campaign" }));

    await waitFor(() => expect(schedule).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancels a scheduled campaign through the existing cancel dialog", async () => {
    mockScheduled();
    const cancel = vi
      .spyOn(client, "cancelCampaign")
      .mockResolvedValue({ ...SCHEDULED, status: "cancelled", cancelled_at: "2026-09-25T09:00:00Z" });
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(SCHEDULED)
      .mockResolvedValue({ ...SCHEDULED, status: "cancelled", cancelled_at: "2026-09-25T09:00:00Z" });
    const user = userEvent.setup();
    renderReport(SCHEDULED);

    await user.click(await screen.findByRole("button", { name: "Cancel campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel campaign" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(SCHEDULED.id));
    expect(await screen.findByText("Cancelled", { selector: "dt" })).toBeTruthy();
  });
});
