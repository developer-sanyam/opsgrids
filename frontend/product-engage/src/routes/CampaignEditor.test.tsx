import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_SENDING } from "@/api/fixtures";
import type {
  CampaignOut,
  ContactListOut,
  DomainOut,
  Page,
  SenderOut,
} from "@/api/types";
import { CampaignPage, NewCampaignPage } from "./CampaignEditor";

/**
 * The draft screen and the send gate. What is pinned here is the gate's honesty: Send acts on the
 * saved draft and says so, a refusal shows the service's own sentence with the step that fixes it,
 * and nothing on this screen ever claims a message went out that the service did not accept.
 */

const VERIFIED: DomainOut = {
  id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
  domain: "vaayu-logistics.example.com",
  status: "verified",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: [],
  last_checked_at: "2026-09-19T06:12:44Z",
  created_at: "2026-08-28T11:04:09Z",
};

const PENDING: DomainOut = {
  id: "b90d5e47-2c31-4a88-8f70-61d4c9a5e223",
  domain: "vaayu-freight.example.com",
  status: "pending",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: [],
  last_checked_at: null,
  created_at: "2026-09-20T15:38:57Z",
};

const TESTED: SenderOut = {
  id: "5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744",
  domain_id: VERIFIED.id,
  name: "Vaayu Logistics Support",
  address: "support@vaayu-logistics.example.com",
  reply_to: "",
  signature: "",
  department: "Support",
  usable: true,
  verified_at: "2026-08-29T09:22:31Z",
  created_at: "2026-08-29T09:18:02Z",
};

const UNTESTED: SenderOut = {
  id: "1a47f0d6-88b3-4c95-b214-6e7d3f92ac58",
  domain_id: VERIFIED.id,
  name: "Priya Raghavan",
  address: "priya@vaayu-logistics.example.com",
  reply_to: "",
  signature: "",
  department: "",
  usable: false,
  verified_at: null,
  created_at: "2026-09-18T13:47:20Z",
};

const ON_PENDING_DOMAIN: SenderOut = {
  ...UNTESTED,
  id: "2b58a1e7-99c4-4da6-8325-7f8e4a03bd69",
  domain_id: PENDING.id,
  name: "Freight desk",
  address: "desk@vaayu-freight.example.com",
};

const LIST: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "",
  member_count: 1_240,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

const DRAFT: CampaignOut = {
  ...CAMPAIGN_DRAFT,
  sender_id: TESTED.id,
  audience: "list",
  list_id: LIST.id,
};

const ALL_GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
  "engage.setup.sender.write:ORG",
];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

/** The four reads the editor makes, answered. */
function mockReads(
  campaign: CampaignOut | Promise<CampaignOut> = DRAFT,
  extras: { senders?: SenderOut[]; domains?: DomainOut[]; lists?: ContactListOut[] } = {},
) {
  vi.spyOn(client, "getCampaign").mockImplementation(() => Promise.resolve(campaign));
  vi.spyOn(client, "listSenders").mockResolvedValue(
    page(extras.senders ?? [TESTED, UNTESTED, ON_PENDING_DOMAIN]),
  );
  vi.spyOn(client, "listDomains").mockResolvedValue(page(extras.domains ?? [VERIFIED, PENDING]));
  vi.spyOn(client, "listLists").mockResolvedValue(page(extras.lists ?? [LIST]));
  vi.spyOn(client, "listCampaignRecipients").mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    size: 25,
    pages: 0,
  });
}

function renderCampaign(campaignId = DRAFT.id, grants: readonly string[] = ALL_GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/campaigns/${campaignId}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
          <Route path="/campaigns/new" element={<NewCampaignPage />} />
          <Route path="/campaigns" element={<p>All campaigns page</p>} />
          <Route path="/senders" element={<p>Senders page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function renderNew(grants: readonly string[] = ALL_GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/campaigns/new"]}>
        <Routes>
          <Route path="/campaigns/new" element={<NewCampaignPage />} />
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function refused(message: string, status = 422) {
  return new ApiError(status, message, { type: "x", title: "x", status, detail: message });
}

describe("loading", () => {
  it("shows a skeleton while the reads are in flight", () => {
    vi.spyOn(client, "getCampaign").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listSenders").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listDomains").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listLists").mockReturnValue(new Promise(() => {}));
    const { container } = renderCampaign();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("error", () => {
  function failReads(error: Error) {
    vi.spyOn(client, "getCampaign").mockRejectedValue(error);
    vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
  }

  it("shows a real failure for a 500", async () => {
    failReads(new ApiError(500, "Internal error", null));
    renderCampaign();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load this campaign");
    expect(alert.textContent).toContain("Internal error");
  });

  it("shows the service's 404 for a campaign that is not this organization's", async () => {
    failReads(new ApiError(404, "No such campaign", null));
    renderCampaign();
    expect((await screen.findByRole("alert")).textContent).toContain("No such campaign");
  });

  it("shows Engage-not-active on a 402, with no alert", async () => {
    failReads(new ApiError(402, "engage is not active for this organization", null));
    renderCampaign();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a 403 a permission error carrying the service's sentence", async () => {
    failReads(new ApiError(403, "You do not have engage.campaign.campaign.read", null));
    renderCampaign();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "You do not have engage.campaign.campaign.read",
    );
  });
});

describe("a draft", () => {
  it("seeds every field from the saved draft", async () => {
    mockReads();
    renderCampaign();

    expect(await screen.findByDisplayValue(DRAFT.name)).toBeTruthy();
    expect((screen.getByLabelText("Send as") as HTMLSelectElement).value).toBe(TESTED.id);
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe(DRAFT.subject);
    expect((screen.getByLabelText(/HTML body/) as HTMLTextAreaElement).value).toBe(DRAFT.html_body);
    expect(screen.getByRole("radio", { name: /One list/ })).toHaveProperty("checked", true);
    expect((screen.getByLabelText("List") as HTMLSelectElement).value).toBe(LIST.id);
  });

  it("offers only senders on verified domains, and says which have not passed a test send", async () => {
    mockReads();
    renderCampaign();
    const select = (await screen.findByLabelText("Send as")) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);

    expect(labels).toContain("Vaayu Logistics Support <support@vaayu-logistics.example.com>");
    expect(labels).toContain("Priya Raghavan <priya@vaayu-logistics.example.com> — not tested yet");
    expect(labels.join("|")).not.toContain("desk@vaayu-freight.example.com");
  });

  it("keeps a sender whose domain has since lapsed selected, and says why it cannot send", async () => {
    mockReads({ ...DRAFT, sender_id: ON_PENDING_DOMAIN.id });
    renderCampaign();
    const select = (await screen.findByLabelText("Send as")) as HTMLSelectElement;
    expect(select.value).toBe(ON_PENDING_DOMAIN.id);
    expect(screen.getByText(/no longer verified, so it cannot send/)).toBeTruthy();
  });

  it("previews the HTML in a sandboxed frame with no permissions at all, never in the page", async () => {
    const hostile = '<p>Hi</p><script>window.__pwned = true</script><img src="x" onerror="window.__pwned = true">';
    mockReads({ ...DRAFT, html_body: hostile });
    const { container } = renderCampaign();

    const frame = (await screen.findByTitle("HTML preview of the campaign body")) as HTMLIFrameElement;
    // An empty `sandbox` attribute grants nothing: no scripts, forms, popups or same-origin access.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain(hostile);
    expect(frame.getAttribute("srcdoc")).toContain("Content-Security-Policy");
    // None of it reached this document.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("shows the plain-text body as text, tags and all", async () => {
    mockReads({ ...DRAFT, text_body: "Use <b>bold</b> sparingly" });
    renderCampaign();
    // The same words are in the plain-text field; the preview is the one rendered as text.
    const preview = await screen.findByText("Use <b>bold</b> sparingly", { selector: "pre" });
    expect(preview.querySelector("b")).toBeNull();
  });

  it("tells the truth about merge fields and the unsubscribe footer", async () => {
    mockReads();
    renderCampaign();
    await screen.findByDisplayValue(DRAFT.name);
    expect(screen.getByText(/merge fields such as \{\{first_name\}\} are not filled in/)).toBeTruthy();
    expect(screen.getByText(/unsubscribe link is added to the foot of every message/)).toBeTruthy();
  });
});

describe("saving a draft", () => {
  it("is disabled until something changes, then sends only what changed", async () => {
    mockReads();
    const spy = vi
      .spyOn(client, "editCampaign")
      .mockResolvedValue({ ...DRAFT, subject: "A new subject", updated_at: "2026-09-24T09:00:00Z" });
    const user = userEvent.setup();
    renderCampaign();

    const save = (await screen.findByRole("button", { name: "Save draft" })) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    const subject = screen.getByLabelText("Subject");
    await user.clear(subject);
    await user.type(subject, "A new subject");
    expect(save.disabled).toBe(false);
    expect(screen.getByText("You have unsaved changes.")).toBeTruthy();

    await user.click(save);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(DRAFT.id, { subject: "A new subject" }));
    // Saved: the unsaved marker goes and Save is inert again, without the page re-reading.
    await waitFor(() => expect(screen.queryByText("You have unsaved changes.")).toBeNull());
    expect((screen.getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(true);
    expect(client.getCampaign).toHaveBeenCalledTimes(1);
  });

  it("sends the audience and its list together, and clears the list by asking for everyone", async () => {
    mockReads();
    const spy = vi
      .spyOn(client, "editCampaign")
      .mockResolvedValue({ ...DRAFT, audience: "all_sendable", list_id: null });
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("radio", { name: /Everyone you may mail/ }));
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(DRAFT.id, { audience: "all_sendable" }));
  });

  it("refuses a missing name inline and never calls the service", async () => {
    mockReads();
    const spy = vi.spyOn(client, "editCampaign");
    const user = userEvent.setup();
    renderCampaign();

    const name = await screen.findByLabelText("Campaign name");
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    expect(screen.getByText("Give the campaign a name so you can find it later.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("maps the service's field error onto the control it names and keeps everything typed", async () => {
    mockReads();
    vi.spyOn(client, "editCampaign").mockRejectedValue(
      new ApiError(422, "Validation failed", {
        type: "validation",
        title: "Validation failed",
        status: 422,
        detail: "Validation failed",
        errors: [{ field: "subject", code: "value_error", message: "must not contain a carriage return or line feed" }],
      }),
    );
    const user = userEvent.setup();
    renderCampaign();

    const subject = await screen.findByLabelText("Subject");
    await user.clear(subject);
    await user.type(subject, "Edited subject");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("must not contain a carriage return or line feed")).toBeTruthy();
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe("Edited subject");
  });

  it("shows a failure with no field as one message above the form and keeps what was typed", async () => {
    mockReads();
    vi.spyOn(client, "editCampaign").mockRejectedValue(refused("Only a draft can be edited; this campaign is sending", 409));
    const user = userEvent.setup();
    renderCampaign();

    const subject = await screen.findByLabelText("Subject");
    await user.type(subject, "!");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("Only a draft can be edited; this campaign is sending")).toBeTruthy();
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe(`${DRAFT.subject}!`);
  });

  it("disables the button while a save is in flight, so a double click cannot save twice", async () => {
    mockReads();
    let release: (value: CampaignOut) => void = () => {};
    const spy = vi.spyOn(client, "editCampaign").mockReturnValue(
      new Promise<CampaignOut>((resolve) => {
        release = resolve;
      }),
    );
    const user = userEvent.setup();
    renderCampaign();

    await user.type(await screen.findByLabelText("Subject"), "!");
    const save = screen.getByRole("button", { name: "Save draft" });
    await user.click(save);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Saving…" }));
    expect(spy).toHaveBeenCalledTimes(1);
    release({ ...DRAFT, subject: `${DRAFT.subject}!` });
  });
});

describe("the send gate", () => {
  it("says what Send will do and disables it while the form has unsaved changes", async () => {
    mockReads();
    const user = userEvent.setup();
    renderCampaign();

    const send = (await screen.findByRole("button", { name: "Send campaign" })) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    expect(
      screen.getByText(/Sends the saved draft as Vaayu Logistics Support <support@vaayu-logistics.example.com> to Enterprise accounts, 1,240 contacts today/),
    ).toBeTruthy();

    await user.type(screen.getByLabelText("Subject"), "!");
    expect(send.disabled).toBe(true);
    expect(screen.getByText(/Save your changes first — sending .* uses the saved draft/)).toBeTruthy();
    // The preview send reads the same saved draft, so it is disabled by the same dirty state.
    expect((screen.getByRole("button", { name: "Send a preview" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("asks first, on the page, with the consequence, and lands focus on the safe choice", async () => {
    mockReads();
    const confirm = vi.spyOn(window, "confirm");
    const send = vi.spyOn(client, "sendCampaign");
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Send this campaign now?")).toBeTruthy();
    expect(dialog.textContent).toContain("cannot be recalled");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(send).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("sends once on confirm and shows the campaign as sending", async () => {
    vi.spyOn(client, "getCampaign")
      .mockResolvedValueOnce(DRAFT)
      .mockResolvedValue({ ...CAMPAIGN_SENDING, id: DRAFT.id, name: DRAFT.name });
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    const send = vi.spyOn(client, "sendCampaign").mockResolvedValue({ ...CAMPAIGN_SENDING, id: DRAFT.id });
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send).toHaveBeenCalledWith(DRAFT.id);
    expect(await screen.findByRole("heading", { level: 2, name: "Delivery" })).toBeTruthy();
    expect(screen.getAllByText("Sending").length).toBeGreaterThan(0);
    // No form is left to edit: what went out is a record.
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
  });

  const GATES: { message: string; action: string | RegExp; kind: "link" | "field" }[] = [
    { message: "Connect an email provider before sending a campaign.", action: "Open Connections", kind: "link" },
    {
      message: "support@vaayu-logistics.example.com has not passed a test send. Verify the sender before sending.",
      action: "Open Senders",
      kind: "link",
    },
    {
      message: "The domain behind support@vaayu-logistics.example.com is no longer verified. Re-verify its DNS records before sending.",
      action: "Open Domains",
      kind: "link",
    },
    { message: "Add a subject before sending.", action: "Add a subject", kind: "field" },
    { message: "Add a body before sending.", action: "Write the body", kind: "field" },
    { message: "The campaign's sender no longer exists. Choose another.", action: "Choose another sender", kind: "field" },
    { message: "The campaign's list no longer exists.", action: "Choose another list", kind: "field" },
  ];

  it.each(GATES)("shows “$message” verbatim, with $action, and says nothing was sent", async ({ message, action }) => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(refused(message));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    const alert = await screen.findByText("Not ready to send");
    const box = alert.closest("div[role]") as HTMLElement;
    expect(box.textContent).toContain(message);
    expect(box.textContent).toContain("That request sent nothing.");
    expect(within(box).getByText(action as string)).toBeTruthy();
    // The dialog closed: a refusal is a page-level fact, not a modal to retry inside.
    expect(screen.queryByRole("dialog")).toBeNull();
    // Still a draft, still editable.
    expect(screen.getByRole("button", { name: "Save draft" })).toBeTruthy();
  });

  it("takes a link-shaped fix to the screen that owns it", async () => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(
      refused("support@vaayu-logistics.example.com has not passed a test send. Verify the sender before sending."),
    );
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));
    await user.click(await screen.findByRole("link", { name: "Open Senders" }));
    expect(await screen.findByText("Senders page")).toBeTruthy();
  });

  it("moves focus to the field a field-shaped fix is about", async () => {
    mockReads({ ...DRAFT, subject: "" });
    vi.spyOn(client, "sendCampaign").mockRejectedValue(refused("Add a subject before sending."));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));
    await user.click(await screen.findByRole("button", { name: "Add a subject" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Subject"));
  });

  it("names Engage not being active on a 402 and points at the dashboard, not at a field", async () => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(new ApiError(402, "engage is not active for this organization", null));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    expect(await screen.findByText("Engage isn't active for this organization")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open Products in the dashboard" });
    expect(link.getAttribute("href")).toBe("https://app.opsgrids.com/");
  });

  it("says sending has its own permission on a 403", async () => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(new ApiError(403, "You do not have engage.campaign.campaign.send", null));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    expect(await screen.findByText("You can't send campaigns")).toBeTruthy();
    expect(screen.getByText("You do not have engage.campaign.campaign.send")).toBeTruthy();
    expect(screen.getByText(/Ask an Engage administrator to grant you/)).toBeTruthy();
  });

  it("shows a 409 as the service's sentence and does not retry it", async () => {
    mockReads();
    const send = vi.spyOn(client, "sendCampaign").mockRejectedValue(
      new ApiError(409, "Only a draft can be sent; this campaign is sending", null),
    );
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    expect(await screen.findByText("This campaign can't be sent again")).toBeTruthy();
    expect(screen.getByText("Only a draft can be sent; this campaign is sending")).toBeTruthy();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("says nothing was sent when the request never reached the service", async () => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));

    expect(await screen.findByText(/The campaign has not been sent — check your connection/)).toBeTruthy();
    expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  });

  it("clears a stale refusal when Send is pressed again", async () => {
    mockReads();
    vi.spyOn(client, "sendCampaign").mockRejectedValue(refused("Connect an email provider before sending a campaign."));
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Send campaign" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send campaign" }));
    await screen.findByText("Not ready to send");

    await user.click(screen.getByRole("button", { name: "Send campaign" }));
    expect(screen.queryByText("Not ready to send")).toBeNull();
  });
});

describe("permission gating", () => {
  it("tells a drafter who cannot send that sending is a separate permission, and offers no Send", async () => {
    mockReads();
    renderCampaign(DRAFT.id, ["engage.campaign.campaign.read:ORG", "engage.campaign.campaign.write:ORG"]);

    expect(await screen.findByText("You can draft this campaign, but not send it")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send campaign" })).toBeNull();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeTruthy();
  });

  it("hides Save, Delete and the test send from a read-only caller, and still shows the draft", async () => {
    mockReads();
    renderCampaign(DRAFT.id, ["engage.campaign.campaign.read:ORG"]);

    expect(await screen.findByDisplayValue(DRAFT.name)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Send a test message/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send campaign" })).toBeNull();
  });
});

describe("deleting a draft", () => {
  it("confirms on the page, deletes, and hands the sentence to the list to announce", async () => {
    mockReads();
    const confirm = vi.spyOn(window, "confirm");
    const spy = vi.spyOn(client, "deleteCampaign").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Delete draft" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Nothing has been sent from it");
    expect(spy).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Delete draft" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(DRAFT.id));
    expect(await screen.findByText("All campaigns page")).toBeTruthy();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("shows the service's sentence and keeps the draft when the delete is refused", async () => {
    mockReads();
    vi.spyOn(client, "deleteCampaign").mockRejectedValue(
      new ApiError(409, "Only a draft can be deleted; this campaign is sending", null),
    );
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: "Delete draft" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete draft" }));
    expect(await within(dialog).findByText("Only a draft can be deleted; this campaign is sending")).toBeTruthy();
    expect(screen.queryByText("All campaigns page")).toBeNull();
  });
});

describe("the sender's test send", () => {
  it("opens the sender's own test dialog, says it is not a preview, and flips the option on success", async () => {
    mockReads({ ...DRAFT, sender_id: UNTESTED.id });
    const test = vi.spyOn(client, "sendTestMessage").mockResolvedValue({
      ...UNTESTED,
      usable: true,
      verified_at: "2026-09-24T09:00:00Z",
    });
    const user = userEvent.setup();
    renderCampaign();

    await user.click(await screen.findByRole("button", { name: /Send a test message as priya@vaayu-logistics.example.com/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Send a test message")).toBeTruthy();
    expect(screen.getByText(/it does not preview this campaign/)).toBeTruthy();

    await user.type(within(dialog).getByLabelText("Send it to"), "me@vaayu-logistics.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send test" }));

    await waitFor(() =>
      expect(test).toHaveBeenCalledWith(UNTESTED.id, { to_address: "me@vaayu-logistics.example.com" }),
    );
    expect(await screen.findByText(/Test message delivered/)).toBeTruthy();
    const select = screen.getByLabelText("Send as") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent).join("|")).not.toContain("not tested yet");
  });
});

describe("a new campaign", () => {
  it("drafts with exactly what was typed and lands on the campaign's own page", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED, ON_PENDING_DOMAIN]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED, PENDING]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    const create = vi.spyOn(client, "createCampaign").mockResolvedValue({ ...DRAFT, id: "new-id" });
    vi.spyOn(client, "getCampaign").mockResolvedValue({ ...DRAFT, id: "new-id" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderNew();

    await user.type(await screen.findByLabelText("Campaign name"), "  October update  ");
    await user.selectOptions(screen.getByLabelText("Send as"), TESTED.id);
    await user.type(screen.getByLabelText("Subject"), "What's new");
    await user.type(screen.getByLabelText(/Plain-text body/), "Hello");
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "October update",
        sender_id: TESTED.id,
        subject: "What's new",
        html_body: "",
        text_body: "Hello",
        audience: "all_sendable",
      }),
    );
    // Now on the campaign's own page, with the draft's send gate.
    expect(await screen.findByRole("button", { name: "Send campaign" })).toBeTruthy();
  });

  it("drafts an A/B test with version B, its split, metric and wait, and turns on the tracking it needs", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    const create = vi.spyOn(client, "createCampaign").mockResolvedValue({ ...DRAFT, id: "new-id" });
    vi.spyOn(client, "getCampaign").mockResolvedValue({ ...DRAFT, id: "new-id" });
    vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderNew();

    await user.type(await screen.findByLabelText("Campaign name"), "Diwali");
    await user.selectOptions(screen.getByLabelText("Send as"), TESTED.id);
    await user.type(screen.getByLabelText("Subject"), "Subject A");
    await user.type(screen.getByLabelText(/^Plain-text body/), "Body A");
    await user.click(screen.getByLabelText(/A\/B test two versions/));
    await user.type(screen.getByLabelText("Version B subject"), "Subject B");
    await user.type(screen.getByLabelText(/Version B plain-text body/), "Body B");
    const split = screen.getByLabelText(/Test group size/);
    await user.clear(split);
    await user.type(split, "15");
    await user.selectOptions(screen.getByLabelText("Pick the winner by"), "click");
    const wait = screen.getByLabelText(/Wait before picking/);
    await user.clear(wait);
    await user.type(wait, "6");
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "Diwali",
        sender_id: TESTED.id,
        subject: "Subject A",
        html_body: "",
        text_body: "Body A",
        audience: "all_sendable",
        ab_test: true,
        subject_b: "Subject B",
        html_body_b: "",
        text_body_b: "Body B",
        ab_split_percent: 15,
        ab_metric: "click",
        ab_wait_minutes: 360,
        track_clicks: true,
      }),
    );
  });

  it("refuses an A/B split outside 1 to 50 per cent inline", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    const create = vi.spyOn(client, "createCampaign");
    const user = userEvent.setup();
    renderNew();

    await user.type(await screen.findByLabelText("Campaign name"), "Diwali");
    await user.selectOptions(screen.getByLabelText("Send as"), TESTED.id);
    await user.click(screen.getByLabelText(/A\/B test two versions/));
    const split = screen.getByLabelText(/Test group size/);
    await user.clear(split);
    await user.type(split, "60");
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    expect(await screen.findByText("Test each version on 1 to 50 per cent of the audience.")).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("marks every missing required field at once, and names a list when the audience is one", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([LIST]));
    const create = vi.spyOn(client, "createCampaign");
    const user = userEvent.setup();
    renderNew();

    await user.click(await screen.findByRole("radio", { name: /One list/ }));
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    expect(screen.getByText("Give the campaign a name so you can find it later.")).toBeTruthy();
    expect(screen.getByText("Choose the sender this campaign goes out as.")).toBeTruthy();
    expect(screen.getByText("Choose which list this campaign goes to.")).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("says there is nothing to choose when no list exists, rather than showing an empty picker", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([TESTED]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([VERIFIED]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderNew();

    await user.click(await screen.findByRole("radio", { name: /One list/ }));
    expect((screen.getByLabelText("List") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText(/Create a list under Audience → Lists first/)).toBeTruthy();
  });

  it("explains, instead of showing a form, to a caller who cannot draft", async () => {
    vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    renderNew(["engage.campaign.campaign.read:ORG"]);

    expect(await screen.findByText(/Drafting a campaign needs the/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create draft" })).toBeNull();
  });

  it("shows the read failure rather than a form it cannot fill in", async () => {
    vi.spyOn(client, "listSenders").mockRejectedValue(new ApiError(403, "You do not have engage.setup.sender.read", null));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    renderNew();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load what a campaign needs");
    expect(alert.textContent).toContain("You do not have engage.setup.sender.read");
    expect(screen.queryByRole("button", { name: "Create draft" })).toBeNull();
  });
});
