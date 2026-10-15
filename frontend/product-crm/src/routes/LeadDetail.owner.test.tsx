/**
 * The lead screen's owner control (2026-10-15 customer retest).
 *
 * A lead captured on "Auto-assign" in a one-person organization came out Unassigned. The screen
 * then said "Ready to convert", Convert answered 422 "Assign an owner to this lead before
 * converting it", and nothing on the screen could assign one: the lead was stuck.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearRevealedValues, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import type { Owner } from "@/api/types";
import { LeadDetailPage } from "@/routes/LeadDetail";
import { json, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer } from "@/reveal/testKit";

const UNASSIGNED = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: "Solstice Energy",
  email: "m•••@s•••",
  phone: null,
  source: "manual",
  status: "qualified",
  next_statuses: ["disqualified"],
  owner_id: null,
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-10-15T09:00:00Z",
};

const OWNERS: Owner[] = [
  { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true },
  { id: "own-2", full_name: "Former Rep", email: "former@example.com", role: "AE", is_active: false },
];

const WRITE = ["crm.lead.lead.read:ORG", "crm.lead.lead.write:ORG", "crm.lead.lead.convert:ORG"];
const READ_ONLY = ["crm.lead.lead.read:ORG", "crm.lead.lead.convert:ORG"];

let routes: Record<string, Answer>;

beforeEach(() => {
  routes = {};
  setAccessToken(SESSION_TOKEN);
  clearRevealedValues();
  setUnauthorizedHandler(vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setUnauthorizedHandler(null);
  setAccessToken(null);
});

async function openLead(lead: Record<string, unknown>, grants: string[] = WRITE) {
  routes["GET /api/v1/leads/lead-1"] = () => json(lead);
  stubGateway(routes);
  renderScreen(<LeadDetailPage />, {
    at: "/leads/lead-1",
    path: "/leads/:id",
    grants,
    directory: { owners: OWNERS },
  });
  await screen.findByRole("heading", { name: "Meera Iyer" });
  return userEvent.setup();
}

describe("an unassigned lead", () => {
  it("is not called ready to convert, and Convert says why it is waiting", async () => {
    await openLead(UNASSIGNED);
    expect(screen.queryByText("Ready to convert.")).toBeNull();
    const convert = screen.getByRole("button", { name: "Convert" });
    expect((convert as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Assign an owner first/)).toBeTruthy();
  });

  it("can be given an owner from the lead screen, through PATCH /leads/{id}", async () => {
    let sent: unknown = null;
    routes["PATCH /api/v1/leads/lead-1"] = (call) => {
      sent = call.body;
      return json({ ...UNASSIGNED, owner_id: "own-1" });
    };
    const user = await openLead(UNASSIGNED);

    const picker = screen.getByLabelText(/^owner/i);
    expect(screen.queryByRole("option", { name: "Former Rep" })).toBeNull();
    routes["GET /api/v1/leads/lead-1"] = () => json({ ...UNASSIGNED, owner_id: "own-1" });
    await user.selectOptions(picker, "own-1");

    await waitFor(() => expect(sent).toEqual({ owner_id: "own-1" }));
    expect(await screen.findByText("Ready to convert.")).toBeTruthy();
  });

  it("offers no owner control without lead write: the server would refuse it anyway", async () => {
    await openLead(UNASSIGNED, READ_ONLY);
    expect(screen.queryByLabelText(/^owner/i)).toBeNull();
    expect(screen.getByText("Unassigned")).toBeTruthy();
  });
});

describe("an assigned, qualified lead", () => {
  it("is ready to convert", async () => {
    await openLead({ ...UNASSIGNED, owner_id: "own-1" });
    expect(screen.getByText("Ready to convert.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Convert" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("a converted lead", () => {
  it("links to the company and the deal it became", async () => {
    await openLead({
      ...UNASSIGNED,
      owner_id: "own-1",
      status: "converted",
      next_statuses: [],
      converted_company_id: "comp-9",
      converted_contact_id: "cont-9",
      converted_deal_id: "deal-9",
      converted_at: "2026-10-15T10:00:00Z",
    });
    expect(screen.getByRole("link", { name: "Open the company" }).getAttribute("href")).toBe("/companies/comp-9");
    expect(screen.getByRole("link", { name: "Open the deal" }).getAttribute("href")).toBe("/deals/deal-9");
    expect(screen.getByRole("link", { name: "See the contact" }).getAttribute("href")).toBe("/companies/comp-9");
    expect(screen.queryByLabelText(/^owner/i)).toBeNull();
  });
});
