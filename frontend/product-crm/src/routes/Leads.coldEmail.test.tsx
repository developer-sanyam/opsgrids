/**
 * Cold email to leads, from the Leads list: who may see the control, the selection that reaches the
 * server, the counts and preview the dialog shows, the create-then-send sequence, and a lead's own
 * "Cold emails" card. Every gate is the server's; these tests pin what the screen hides and sends.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@opsgrids/ui";
import { setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { LeadDetailPage } from "@/routes/LeadDetail";
import { LeadsPage } from "@/routes/Leads";
import { json, problem, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const lead = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  full_name: name,
  company: "Acme Traders",
  email: "p•••@a•••",
  phone: null,
  source: "form",
  status: "new",
  next_statuses: ["working"],
  owner_id: null,
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-08-20T09:00:00Z",
  ...over,
});
const PAGE = {
  items: [lead("lead-1", "Priya Sharma"), lead("lead-2", "Ravi Kumar")],
  total: 2,
  page: 1,
  size: 25,
  pages: 1,
};
const PREVIEW_OUT = {
  selected: 2,
  with_email: 2,
  skipped_status: 0,
  excluded: 1,
  sendable: 1,
  daily_cap: 200,
  sent_today: 195,
  sample: {
    lead_id: "lead-1",
    full_name: "Priya Sharma",
    subject: "Hi Priya at Acme Traders",
    text_body: "Hello Priya",
  },
  unknown_fields: [],
};
const CREATED = {
  ...PREVIEW_OUT,
  campaign: { id: "camp-1", name: "Cold email", status: "draft" },
};

const READ = "crm.lead.lead.read:ORG";
const FULL = [READ, "engage.campaign.campaign.write:ORG", "engage.campaign.campaign.send:ORG"];
const PREVIEW = "POST /api/v1/engage/lead-campaigns/preview";
const CREATE = "POST /api/v1/engage/lead-campaigns";
const SEND = "POST /api/v1/engage/campaigns/camp-1/send";
const SCHEDULE = "POST /api/v1/engage/campaigns/camp-1/schedule";

let routes: Record<string, Answer>;
let gateway: Gateway;

function show(grants: string[], entitlements: string[]) {
  gateway = stubGateway(routes);
  return renderScreen(
    <Toaster>
      <LeadsPage />
    </Toaster>,
    { at: "/leads", path: "/leads", grants, entitlements },
  );
}

beforeEach(() => {
  routes = {
    "GET /api/v1/leads": () => json(PAGE),
    "GET /api/v1/engage/senders": () =>
      json({ items: [{ id: "snd-1", name: "Sales", address: "sales@acme.test", usable: true }] }),
    "GET /api/v1/engage/email/templates": () =>
      json({ items: [{ id: "intro", subject: "Hello {{first_name}}", body: "Hi {{first_name|there}}," }] }),
    [PREVIEW]: () => json(PREVIEW_OUT),
    [CREATE]: () => json(CREATED, 201),
    [SEND]: () => json({}, 202),
    [SCHEDULE]: () => json({}, 202),
  };
  setAccessToken(SESSION_TOKEN);
  setUnauthorizedHandler(vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
});

async function openForSelected(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("checkbox", { name: "Select Priya Sharma" }));
  await user.click(screen.getByRole("button", { name: "Email selected" }));
  const dialog = await screen.findByRole("dialog", { name: "Email these leads" });
  await within(dialog).findByText("Leads selected");
  return dialog;
}

async function fill(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.selectOptions(within(dialog).getByLabelText("Send as"), "snd-1");
  await user.type(within(dialog).getByLabelText(/Subject/), "Hi");
  await user.type(within(dialog).getByLabelText(/Message/), "Hello");
}

describe("who sees the control", () => {
  it("shows it with the entitlement and both Engage permissions", async () => {
    show(FULL, ["crm", "engage"]);
    expect(await screen.findByRole("button", { name: "Email these leads" })).toBeTruthy();
  });

  it.each([
    ["no Engage entitlement", FULL, ["crm"]],
    ["no campaign write", [READ, "engage.campaign.campaign.send:ORG"], ["crm", "engage"]],
    ["no campaign send", [READ, "engage.campaign.campaign.write:ORG"], ["crm", "engage"]],
  ])("hides it with %s", async (_why, grants, entitlements) => {
    show(grants, entitlements);
    await screen.findByText("Priya Sharma");
    expect(screen.queryByRole("button", { name: "Email these leads" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Select Priya Sharma" })).toBeNull();
  });
});

describe("the dialog", () => {
  it("sends the ticked ids and shows the counts, the cap and a real lead", async () => {
    const user = userEvent.setup();
    routes[PREVIEW] = () => json({ ...PREVIEW_OUT, sendable: 10 });
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);

    await within(dialog).findByText("How it reads for Priya Sharma");
    const preview = gateway.callsTo(PREVIEW).at(-1)?.body as Record<string, unknown>;
    expect(preview.lead_ids).toEqual(["lead-1"]);
    expect(preview).not.toHaveProperty("status");
    expect(within(dialog).getByText("Hi Priya at Acme Traders")).toBeTruthy();
    expect(within(dialog).getByText("Leads selected").nextSibling?.textContent).toBe("2");
    expect(within(dialog).getByText("Left out: unsubscribed or suppressed").nextSibling?.textContent).toBe("1");
    expect(within(dialog).getByText("Will be emailed").nextSibling?.textContent).toBe("10");
    expect(within(dialog).getByText(/195 sent today/)).toBeTruthy();
    expect(within(dialog).getByText(/The first 5 can go today/)).toBeTruthy();
  });

  it("fills the subject and message from a template", async () => {
    const user = userEvent.setup();
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    await user.selectOptions(await within(dialog).findByLabelText("Start from a template"), "intro");
    expect((within(dialog).getByLabelText(/Subject/) as HTMLInputElement).value).toBe("Hello {{first_name}}");
    expect((within(dialog).getByLabelText(/Message/) as HTMLTextAreaElement).value).toBe("Hi {{first_name|there}},");
  });

  it("emails every lead the current filter matches, with no ids", async () => {
    const user = userEvent.setup();
    show(FULL, ["crm", "engage"]);
    await user.click(await screen.findByRole("button", { name: "New" }));
    await user.selectOptions(screen.getByLabelText("Source"), "import");
    await user.click(screen.getByRole("button", { name: "Email these leads" }));
    const dialog = await screen.findByRole("dialog", { name: "Email these leads" });
    await within(dialog).findByText("Leads selected");
    const body = gateway.callsTo(PREVIEW).at(-1)?.body as Record<string, unknown>;
    expect(body).toMatchObject({ status: "new", source: "import" });
    expect(body).not.toHaveProperty("lead_ids");
    expect(within(dialog).getByText(/new leads from import/)).toBeTruthy();
  });

  it("creates the draft, then sends it, and says so", async () => {
    const user = userEvent.setup();
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Send now" }));

    await waitFor(() => expect(gateway.callsTo(SEND)).toHaveLength(1));
    expect(gateway.callsTo(CREATE)[0]?.body).toMatchObject({
      lead_ids: ["lead-1"],
      sender_id: "snd-1",
      subject: "Hi",
      text_body: "Hello",
    });
    expect((await screen.findAllByText(/Sending to 1 lead/)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("schedules instead of sending when asked", async () => {
    const user = userEvent.setup();
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    await fill(user, dialog);
    await user.selectOptions(within(dialog).getByLabelText("When"), "later");
    expect((within(dialog).getByRole("button", { name: "Schedule send" }) as HTMLButtonElement).disabled).toBe(true);
    const when = within(dialog).getByLabelText("Send at") as HTMLInputElement;
    const future = new Date(Date.now() + 86_400_000);
    const pad = (n: number) => String(n).padStart(2, "0");
    await user.type(
      when,
      `${future.getFullYear()}-${pad(future.getMonth() + 1)}-${pad(future.getDate())}T10:30`,
    );
    await user.click(within(dialog).getByRole("button", { name: "Schedule send" }));
    await waitFor(() => expect(gateway.callsTo(SCHEDULE)).toHaveLength(1));
    expect(gateway.callsTo(SEND)).toHaveLength(0);
    expect(typeof (gateway.callsTo(SCHEDULE)[0]?.body as { scheduled_at: string }).scheduled_at).toBe("string");
  });

  it("keeps the draft when the send is refused, and a retry sends that same draft", async () => {
    const user = userEvent.setup();
    let refuse = true;
    routes[SEND] = () => (refuse ? problem(422, "Acme has not passed a test send. Verify the sender before sending.") : json({}, 202));
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Send now" }));

    expect(await within(dialog).findByText(/Verify the sender before sending/)).toBeTruthy();
    expect(within(dialog).getByText(/The draft is saved in Engage/)).toBeTruthy();
    refuse = false;
    await user.click(within(dialog).getByRole("button", { name: "Send now" }));
    await waitFor(() => expect(gateway.callsTo(SEND)).toHaveLength(2));
    expect(gateway.callsTo(CREATE)).toHaveLength(1);
  });

  it("will not send with a merge field no lead can fill, or to nobody", async () => {
    const user = userEvent.setup();
    routes[PREVIEW] = () => json({ ...PREVIEW_OUT, unknown_fields: ["compny_name"] });
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    await fill(user, dialog);
    expect(await within(dialog).findByText("Some merge fields can't be filled")).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Send now" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("tells the rep when no sender is verified", async () => {
    const user = userEvent.setup();
    routes["GET /api/v1/engage/senders"] = () => json({ items: [{ id: "x", name: "N", address: "n@a.test", usable: false }] });
    show(FULL, ["crm", "engage"]);
    const dialog = await openForSelected(user);
    expect(await within(dialog).findByText("No verified sender yet")).toBeTruthy();
  });

  it("shows a failed count as an error, not as zero", async () => {
    const user = userEvent.setup();
    routes[PREVIEW] = () => problem(503, "Leads could not be read right now. Try again.");
    show(FULL, ["crm", "engage"]);
    await user.click(await screen.findByRole("checkbox", { name: "Select Priya Sharma" }));
    await user.click(screen.getByRole("button", { name: "Email selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Email these leads" });
    expect(await within(dialog).findByText("Couldn't count who would get this")).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Send now" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("a lead's Cold emails card", () => {
  const GET = "GET /api/v1/engage/lead-campaigns/leads/lead-1/emails";
  function showLead(grants: string[], entitlements: string[]) {
    gateway = stubGateway({
      "GET /api/v1/leads/lead-1": () => json(lead("lead-1", "Priya Sharma")),
      ...routes,
    });
    return renderScreen(<LeadDetailPage />, { at: "/leads/lead-1", path: "/leads/:id", grants, entitlements });
  }

  it("lists the subject lines and nothing else", async () => {
    routes[GET] = () =>
      json([{ campaign_id: "camp-1", title: "Cold email sent: Hi Priya at Acme Traders", at: "2026-10-16T09:00:00Z" }]);
    showLead([READ, "engage.campaign.campaign.read:ORG"], ["crm", "engage"]);
    expect(await screen.findByText("Cold email sent: Hi Priya at Acme Traders")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Cold emails" })).toBeTruthy();
  });

  it("shows no card for a lead nobody has emailed", async () => {
    routes[GET] = () => json([]);
    showLead([READ, "engage.campaign.campaign.read:ORG"], ["crm", "engage"]);
    await screen.findByRole("heading", { name: "Priya Sharma" });
    await waitFor(() => expect(gateway.callsTo(GET)).toHaveLength(1));
    expect(screen.queryByRole("heading", { name: "Cold emails" })).toBeNull();
  });

  it.each([
    ["the Engage entitlement", [READ, "engage.campaign.campaign.read:ORG"], ["crm"]],
    ["the campaign read permission", [READ], ["crm", "engage"]],
  ])("never asks without %s", async (_why, grants, entitlements) => {
    showLead(grants, entitlements);
    await screen.findByRole("heading", { name: "Priya Sharma" });
    expect(gateway.callsTo(GET)).toHaveLength(0);
  });

  it("says when it could not load", async () => {
    routes[GET] = () => problem(503, "Leads could not be read right now. Try again.");
    showLead([READ, "engage.campaign.campaign.read:ORG"], ["crm", "engage"]);
    expect(await screen.findByText("Couldn't load cold emails")).toBeTruthy();
  });
});
