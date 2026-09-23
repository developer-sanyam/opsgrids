import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_SENDING } from "@/api/fixtures";
import type { CampaignOut, ContactListOut, DomainOut, Page, SenderOut } from "@/api/types";
import { framePolicy } from "@/lib/previewPolicy";
import { CampaignPage } from "./CampaignEditor";

/**
 * What the draft screen does for someone who may not change it, how its headings run, where focus
 * goes when the control that was just used is replaced, and what its preview will and will not
 * load. Each of these was a way for the screen to be quietly wrong for someone.
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

const LIST: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "",
  member_count: 12,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

const DRAFT: CampaignOut = { ...CAMPAIGN_DRAFT, sender_id: SENDER.id, audience: "list", list_id: LIST.id };

const READ = "engage.campaign.campaign.read:ORG";
const WRITE = "engage.campaign.campaign.write:ORG";
const SEND = "engage.campaign.campaign.send:ORG";

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function mockReads(campaign: CampaignOut = DRAFT) {
  vi.spyOn(client, "getCampaign").mockResolvedValue(campaign);
  vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
  vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
}

function renderDraft(grants: readonly string[]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/campaigns/${DRAFT.id}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function focusIsInTheHeading(): boolean {
  const heading = screen.getByRole("heading", { level: 1 });
  return heading.contains(document.activeElement);
}

describe("a caller who may read a draft and not change it", () => {
  it("sees the fields read-only or disabled, says why, and is offered no Save", async () => {
    mockReads();
    renderDraft([READ]);

    expect(await screen.findByText("You can read this draft, not change it")).toBeTruthy();
    for (const label of ["Campaign name", "Subject"]) {
      expect((screen.getByLabelText(label) as HTMLInputElement).readOnly, label).toBe(true);
    }
    expect((screen.getByLabelText(/HTML body/) as HTMLTextAreaElement).readOnly).toBe(true);
    expect((screen.getByLabelText(/Plain-text body/) as HTMLTextAreaElement).readOnly).toBe(true);
    expect((screen.getByLabelText("Send as") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("List") as HTMLSelectElement).disabled).toBe(true);
    for (const radio of screen.getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(true);
    }
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
    // Read-only text is still readable, focusable and selectable — not a greyed-out box.
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe(DRAFT.subject);
  });

  it("does not let typing change anything, so nothing is ever unsaved", async () => {
    mockReads();
    const user = userEvent.setup();
    renderDraft([READ, SEND]);

    const subject = (await screen.findByLabelText("Subject")) as HTMLInputElement;
    await user.type(subject, "!!!");
    expect(subject.value).toBe(DRAFT.subject);
    expect(screen.queryByText("You have unsaved changes.")).toBeNull();
  });

  it("who can send is never blocked from sending by an unsaved state they cannot create", async () => {
    mockReads();
    const user = userEvent.setup();
    renderDraft([READ, SEND]);

    const send = (await screen.findByRole("button", { name: "Send campaign" })) as HTMLButtonElement;
    await user.type(screen.getByLabelText("Subject"), "!");
    expect(send.disabled).toBe(false);
    expect(screen.queryByText(/Save your changes first/)).toBeNull();
  });

  it("still blocks Send on unsaved changes for someone who can both write and send", async () => {
    mockReads();
    const user = userEvent.setup();
    renderDraft([READ, WRITE, SEND]);

    const send = (await screen.findByRole("button", { name: "Send campaign" })) as HTMLButtonElement;
    await user.type(screen.getByLabelText("Subject"), "!");
    expect(send.disabled).toBe(true);
    expect(screen.getByText(/Save your changes first/)).toBeTruthy();
  });

  it("leaves the fields live for someone who may write", async () => {
    mockReads();
    renderDraft([READ, WRITE]);

    expect((await screen.findByLabelText("Subject") as HTMLInputElement).readOnly).toBe(false);
    expect((screen.getByLabelText("Send as") as HTMLSelectElement).disabled).toBe(false);
    for (const radio of screen.getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(false);
    }
  });

  it("disables the audience choice, like every other field, while a save is in flight", async () => {
    mockReads();
    let release: (value: CampaignOut) => void = () => {};
    vi.spyOn(client, "editCampaign").mockReturnValue(
      new Promise<CampaignOut>((resolve) => {
        release = resolve;
      }),
    );
    const user = userEvent.setup();
    renderDraft([READ, WRITE]);

    await user.type(await screen.findByLabelText("Subject"), "!");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    for (const radio of screen.getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(true);
    }
    expect((screen.getByLabelText("Send as") as HTMLSelectElement).disabled).toBe(true);
    release({ ...DRAFT, subject: `${DRAFT.subject}!` });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saving…" })).toBeNull());
  });
});

describe("the outline of the draft screen", () => {
  it("runs Send, then Write the campaign, then Preview — no unheaded block between them", async () => {
    mockReads();
    renderDraft([READ, WRITE, SEND]);

    await screen.findByLabelText("Subject");
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Send", "Write the campaign", "Preview"]);
    expect(screen.getAllByRole("heading", { level: 1 }).length).toBe(1);
  });
});

describe("where focus goes when the control just used is replaced", () => {
  it("moves to the page heading after Send turns the draft into a report", async () => {
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(DRAFT)
      .mockResolvedValue({ ...CAMPAIGN_SENDING, id: DRAFT.id, name: DRAFT.name, sender_id: SENDER.id });
    vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    vi.spyOn(client, "sendCampaign").mockResolvedValue({ ...CAMPAIGN_SENDING, id: DRAFT.id });
    const user = userEvent.setup();
    renderDraft([READ, WRITE, SEND]);

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    await screen.findByRole("heading", { level: 2, name: "Delivery" });
    await waitFor(() => expect(focusIsInTheHeading()).toBe(true));
  });

  it("moves to the page heading after Cancel, once the page has been re-read", async () => {
    const running: CampaignOut = { ...CAMPAIGN_SENDING, id: DRAFT.id, sender_id: SENDER.id };
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(running)
      .mockResolvedValue({ ...running, status: "cancelled", cancelled_at: "2026-09-23T06:30:00Z" });
    vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    vi.spyOn(client, "cancelCampaign").mockResolvedValue({ ...running, status: "cancelled" });
    const user = userEvent.setup();
    renderDraft([READ, WRITE, SEND]);

    await user.click(await screen.findByRole("button", { name: "Cancel campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel campaign" }));

    await screen.findByText("Cancelled", { selector: "dt" });
    await waitFor(() => expect(focusIsInTheHeading()).toBe(true));
  });

  it("moves to the page heading after Resume", async () => {
    const paused: CampaignOut = {
      ...DRAFT,
      status: "paused",
      pause_reason: "No connected email provider was found.",
      started_at: "2026-09-19T05:00:00Z",
    };
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(paused)
      .mockResolvedValueOnce(paused)
      .mockResolvedValue({ ...paused, status: "sending", pause_reason: "" });
    vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    vi.spyOn(client, "resumeCampaign").mockResolvedValue({ ...paused, status: "sending" });
    const user = userEvent.setup();
    renderDraft([READ, WRITE, SEND]);

    await user.click(await screen.findByRole("button", { name: "Resume now" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Resume now" })).toBeNull());
    await waitFor(() => expect(focusIsInTheHeading()).toBe(true));
  });
});

describe("the preview's images", () => {
  const HOSTILE = '<p>Hi</p><img src="https://track.example.com/pixel.gif">';

  it("blocks every image by default, with the exact policy pinned", async () => {
    mockReads({ ...DRAFT, html_body: HOSTILE });
    renderDraft([READ, WRITE]);

    const frame = (await screen.findByTitle("HTML preview of the campaign body")) as HTMLIFrameElement;
    const policy =
      "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'none'; style-src 'unsafe-inline'\">";
    expect(frame.getAttribute("srcdoc")).toBe(`${policy}${HOSTILE}`);
    expect(frame.getAttribute("sandbox")).toBe("");
    const toggle = screen.getByRole("switch", { name: "Load remote images" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    // Says what turning it on costs, before it is turned on.
    expect(screen.getByText(/tells that host your IP address and browser/)).toBeTruthy();
    expect(screen.getByText(/Remote images are\s+blocked here unless you turn them on above/)).toBeTruthy();
  });

  it("allows https images only once the switch is on, and blocks them again when it is off", async () => {
    mockReads({ ...DRAFT, html_body: HOSTILE });
    const user = userEvent.setup();
    renderDraft([READ, WRITE]);

    const frame = (await screen.findByTitle("HTML preview of the campaign body")) as HTMLIFrameElement;
    const toggle = screen.getByRole("switch", { name: "Load remote images" });

    await user.click(toggle);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(frame.getAttribute("srcdoc")).toContain("img-src https:;");
    expect(frame.getAttribute("srcdoc")).not.toContain("img-src 'none'");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.getByText(/Each host can see your IP address and browser/)).toBeTruthy();

    await user.click(toggle);
    expect(frame.getAttribute("srcdoc")).toContain("img-src 'none'");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("offers the switch only where there is HTML to preview", async () => {
    mockReads({ ...DRAFT, html_body: "", text_body: "Plain only" });
    renderDraft([READ, WRITE]);

    await screen.findByText("Plain only", { selector: "pre" });
    expect(screen.queryByRole("switch", { name: "Load remote images" })).toBeNull();
  });
});

describe("framePolicy", () => {
  it("is a fixed string for each position of the switch, and never allows anything else", () => {
    expect(framePolicy(false)).toBe(
      "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'none'; style-src 'unsafe-inline'\">",
    );
    expect(framePolicy(true)).toBe(
      "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src https:; style-src 'unsafe-inline'\">",
    );
    for (const on of [false, true]) {
      expect(framePolicy(on)).not.toMatch(/script|data:|http:|\*|unsafe-eval/);
    }
  });
});
