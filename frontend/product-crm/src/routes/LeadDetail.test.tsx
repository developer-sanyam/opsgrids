/**
 * The lead screen's status control and its Convert dialog, on the real screen with a stubbed
 * gateway.
 *
 * Two defects from a real first-day owner: the status dropdown offered moves the server refuses
 * (New -> Qualified answered a bare 409, and so did re-selecting the current status), and Convert
 * asked for nothing, made no contact and named the deal with an id fragment and a value of zero.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@opsgrids/ui";
import { clearRevealedValues, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { LeadDetailPage } from "@/routes/LeadDetail";
import { json, problem, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const LEAD = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: "Solstice Energy",
  email: "m•••@s•••",
  phone: "•••••• 4821",
  source: "form",
  status: "new",
  next_statuses: ["working", "disqualified"],
  owner_id: "own-1",
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-08-20T09:00:00Z",
};

const GRANTS = ["crm.lead.lead.read:ORG", "crm.lead.lead.write:ORG", "crm.lead.lead.convert:ORG"];
const PATCH = "PATCH /api/v1/leads/lead-1";
const CONVERT = "POST /api/v1/leads/lead-1:convert";

let gateway: Gateway;
let routes: Record<string, Answer>;

beforeEach(() => {
  routes = { "GET /api/v1/leads/lead-1": () => json(LEAD) };
  setAccessToken(SESSION_TOKEN);
  clearRevealedValues();
  setUnauthorizedHandler(vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  localStorage.clear();
  sessionStorage.clear();
});

async function openLead(lead: Record<string, unknown> = LEAD) {
  routes["GET /api/v1/leads/lead-1"] = () => json(lead);
  gateway = stubGateway(routes);
  renderScreen(<LeadDetailPage />, { at: "/leads/lead-1", path: "/leads/:id", grants: GRANTS });
  await screen.findByRole("heading", { name: "Meera Iyer" });
  return userEvent.setup();
}

function optionLabels(select: HTMLElement): string[] {
  return within(select).getAllByRole("option").map((o) => o.textContent ?? "");
}

describe("the status control offers only the moves the server allows", () => {
  it("a new lead can be marked Working or Disqualified -- never Qualified", async () => {
    await openLead();
    const labels = optionLabels(screen.getByLabelText(/^status/i));
    expect(labels).toEqual(["New (current)", "Mark as Working", "Mark as Disqualified"]);
    expect(labels.join()).not.toMatch(/Qualified(?!$)|Converted/);
  });

  it("follows whatever the server says comes next, not a table in the screen", async () => {
    await openLead({ ...LEAD, status: "working", next_statuses: ["new", "qualified", "disqualified"] });
    expect(optionLabels(screen.getByLabelText(/^status/i))).toEqual([
      "Working (current)",
      "Mark as New",
      "Mark as Qualified",
      "Mark as Disqualified",
    ]);
  });

  it("sends a chosen move, and re-selecting the current status sends nothing", async () => {
    routes[PATCH] = () => json({ ...LEAD, status: "working", next_statuses: ["new", "qualified", "disqualified"] });
    const user = await openLead();
    const select = screen.getByLabelText(/^status/i);

    await user.selectOptions(select, "new");
    expect(gateway.callsTo(PATCH)).toHaveLength(0);

    await user.selectOptions(select, "working");
    await waitFor(() => expect(gateway.callsTo(PATCH)).toHaveLength(1));
    expect(gateway.calls.find((c) => c.key === PATCH)?.body).toEqual({ status: "working" });
  });

  it("says a refusal in the server's own plain words", async () => {
    routes[PATCH] = () =>
      problem(409, "A New lead has to be marked Working before it can be Qualified.");
    const user = await openLead();
    await user.selectOptions(screen.getByLabelText(/^status/i), "working");
    expect(await screen.findByText(/has to be marked Working before it can be Qualified/i)).toBeTruthy();
  });

  it("offers no status control once converted", async () => {
    await openLead({ ...LEAD, status: "converted", next_statuses: [], converted_at: "2026-09-01T00:00:00Z" });
    expect(screen.queryByLabelText(/^status/i)).toBeNull();
  });
});

describe("converting a lead", () => {
  const QUALIFIED = { ...LEAD, status: "qualified", next_statuses: ["working", "disqualified"] };

  it("opens a dialog that says what is created and lets the rep set the deal", async () => {
    const user = await openLead(QUALIFIED);
    await user.click(screen.getByRole("button", { name: "Convert" }));

    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });
    expect(within(dialog).getByText(/becomes a company, a contact and a deal/i)).toBeTruthy();
    expect(within(dialog).getByLabelText(/deal value/i)).toBeTruthy();
    expect(within(dialog).getByLabelText(/deal stage/i)).toBeTruthy();
  });

  it("sends the value in minor units and the chosen stage", async () => {
    routes[CONVERT] = () =>
      json({ lead_id: "lead-1", company_id: "c", contact_id: "k", deal_id: "d" });
    const user = await openLead(QUALIFIED);
    await user.click(screen.getByRole("button", { name: "Convert" }));
    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });

    await user.type(within(dialog).getByLabelText(/deal value/i), "2,500");
    await user.selectOptions(within(dialog).getByLabelText(/deal stage/i), "proposal");
    await user.click(within(dialog).getByRole("button", { name: "Convert" }));

    await waitFor(() => expect(gateway.calls.some((c) => c.key === CONVERT)).toBe(true));
    expect(gateway.calls.find((c) => c.key === CONVERT)?.body).toEqual({
      deal_value_minor: 250000,
      deal_stage: "proposal",
    });
  });

  it("converts with no body at all when the rep sets nothing", async () => {
    routes[CONVERT] = () =>
      json({ lead_id: "lead-1", company_id: "c", contact_id: null, deal_id: "d" });
    const user = await openLead(QUALIFIED);
    await user.click(screen.getByRole("button", { name: "Convert" }));
    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });
    await user.click(within(dialog).getByRole("button", { name: "Convert" }));

    await waitFor(() => expect(gateway.calls.some((c) => c.key === CONVERT)).toBe(true));
    expect(gateway.calls.find((c) => c.key === CONVERT)?.body).toBeUndefined();
  });

  it("says the contact was made too, when the conversion made one", async () => {
    routes[CONVERT] = () =>
      json({ lead_id: "lead-1", company_id: "c", contact_id: "ct", deal_id: "d" });
    routes["GET /api/v1/leads/lead-1"] = () => json(QUALIFIED);
    gateway = stubGateway(routes);
    renderScreen(
      <Toaster>
        <LeadDetailPage />
      </Toaster>,
      { at: "/leads/lead-1", path: "/leads/:id", grants: GRANTS },
    );
    await screen.findByRole("heading", { name: "Meera Iyer" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Convert" }));
    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });
    await user.click(within(dialog).getByRole("button", { name: "Convert" }));
    expect(await screen.findByText("Lead converted — a company, contact and deal now exist for it.")).toBeTruthy();
  });

  it("refuses a value that is not money, in words, and sends nothing", async () => {
    const user = await openLead(QUALIFIED);
    await user.click(screen.getByRole("button", { name: "Convert" }));
    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });
    await user.type(within(dialog).getByLabelText(/deal value/i), "lots");
    await user.click(within(dialog).getByRole("button", { name: "Convert" }));

    expect(await within(dialog).findByText(/enter an amount in digits/i)).toBeTruthy();
    expect(gateway.calls.some((c) => c.key === CONVERT)).toBe(false);
  });

  it("says the contact is not created for a lead with no email or phone", async () => {
    const user = await openLead({ ...QUALIFIED, email: null, phone: null });
    await user.click(screen.getByRole("button", { name: "Convert" }));
    const dialog = await screen.findByRole("dialog", { name: /convert this lead/i });
    expect(within(dialog).getByText(/becomes a company and a deal/i)).toBeTruthy();
  });

  it("is not offered to someone who may not convert", async () => {
    routes["GET /api/v1/leads/lead-1"] = () => json(QUALIFIED);
    gateway = stubGateway(routes);
    renderScreen(<LeadDetailPage />, {
      at: "/leads/lead-1",
      path: "/leads/:id",
      grants: ["crm.lead.lead.read:ORG", "crm.lead.lead.write:ORG"],
    });
    await screen.findByRole("heading", { name: "Meera Iyer" });
    expect(screen.queryByRole("button", { name: "Convert" })).toBeNull();
  });
});

describe("Convert when it is not available yet", () => {
  it("stays focusable, says every reason, and does not open the dialog", async () => {
    const user = await openLead({ ...LEAD, status: "working", owner_id: null });
    const convert = screen.getByRole("button", { name: "Convert" });
    expect(convert.getAttribute("aria-disabled")).toBe("true");
    expect((convert as HTMLButtonElement).disabled).toBe(false);
    const reasons = document.getElementById(convert.getAttribute("aria-describedby") ?? "") as HTMLElement;
    expect(reasons.textContent).toMatch(/Qualify first/);
    expect(reasons.textContent).toMatch(/Assign an owner first/);
    await user.click(convert);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("a converted lead", () => {
  it("links the company and the deal once each, underlined, with matching verbs", async () => {
    await openLead({
      ...LEAD,
      status: "converted",
      next_statuses: [],
      converted_at: "2026-09-01T00:00:00Z",
      converted_company_id: "co-1",
      converted_contact_id: "ct-1",
      converted_deal_id: "d-1",
    });
    const links = screen.getAllByRole("link").filter((a) => /\/(companies|deals)\//.test(a.getAttribute("href") ?? ""));
    expect(links.map((a) => a.textContent)).toEqual(["Open the company", "Open the deal"]);
    for (const a of links) expect(a.className).toContain("underline");
  });
});
