/**
 * The phone fixes in this app, one behaviour each: the pipeline board says how many stages are in
 * view, a lead that cannot be converted says why in words, the phone and currency fields are the
 * right kind of control, and the contacts list is a list of cards below 768px.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { Company, Deal, DealStage, Owner } from "@/api/types";
import { DirectoryContext, EMPTY_DIRECTORY } from "@/data/directoryContext";
import { STAGES } from "@/data/stages";
import { ContactFormDialog } from "@/forms/ContactFormDialog";
import { DealFormDialog } from "@/forms/DealFormDialog";
import { LeadDetailPage } from "@/routes/LeadDetail";
import { ContactsPage } from "@/routes/Contacts";
import { KanbanBoard } from "@/ui/KanbanBoard";
import { renderScreen, stubGateway, json, SESSION_TOKEN } from "@/reveal/testKit";
import { setAccessToken } from "@opsgrids/client";

const OWNER: Owner = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true };
const COMPANY: Company = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: "own-1",
  annual_revenue_minor: 0,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

function stubPhone(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setAccessToken(null);
});

describe("KanbanBoard on a phone", () => {
  it("says which stages are in view, out of how many", () => {
    const groups = Object.fromEntries(STAGES.map((s) => [s.stage, [] as Deal[]])) as Record<DealStage, Deal[]>;
    render(
      <MemoryRouter>
        <DirectoryContext.Provider value={{ ...EMPTY_DIRECTORY, status: "success" }}>
          <KanbanBoard groups={groups} canMove onMoved={vi.fn()} onMoveFailed={vi.fn()} reload={vi.fn()} />
        </DirectoryContext.Provider>
      </MemoryRouter>,
    );
    const cue = screen.getByTestId("stage-cue");
    expect(cue.textContent).toContain(`of ${STAGES.length}`);
    expect(cue.textContent).toContain("swipe for the rest");
  });
});

describe("LeadDetail: converting", () => {
  const LEAD = {
    id: "lead-1",
    full_name: "Meera Iyer",
    company: "Solstice Energy",
    email: null,
    phone: null,
    source: "form",
    status: "working",
    owner_id: null,
    notes: "",
    converted_company_id: null,
    converted_contact_id: null,
    converted_deal_id: null,
    converted_at: null,
    created_at: "2026-08-20T09:00:00Z",
  };

  it("keeps Convert disabled and says, next to it, to qualify the lead first", async () => {
    setAccessToken(SESSION_TOKEN);
    stubGateway({ "GET /api/v1/leads/lead-1": () => json(LEAD) });
    renderScreen(<LeadDetailPage />, {
      at: "/leads/lead-1",
      path: "/leads/:id",
      grants: ["crm.lead.lead.convert:ORG"],
    });
    await screen.findByRole("heading", { name: "Meera Iyer" });

    const convert = screen.getByRole("button", { name: "Convert" });
    expect(convert).toHaveProperty("disabled", true);
    const reason = screen.getByText(/Qualify first/);
    expect(convert.getAttribute("aria-describedby")).toBe(reason.id);
  });

  it("shows no reason once the lead is qualified, and Convert is available", async () => {
    setAccessToken(SESSION_TOKEN);
    stubGateway({ "GET /api/v1/leads/lead-1": () => json({ ...LEAD, status: "qualified" }) });
    renderScreen(<LeadDetailPage />, {
      at: "/leads/lead-1",
      path: "/leads/:id",
      grants: ["crm.lead.lead.convert:ORG"],
    });
    await screen.findByRole("heading", { name: "Meera Iyer" });

    expect(screen.getByRole("button", { name: "Convert" })).toHaveProperty("disabled", false);
    expect(screen.queryByText(/Qualify first/)).toBeNull();
  });
});

describe("Forms: the right control for the value", () => {
  it("asks a phone for a telephone keyboard and lets the browser fill the number", () => {
    render(
      <ContactFormDialog
        contact={null}
        companies={[COMPANY]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    const phone = screen.getByLabelText(/^phone/i);
    expect(phone.getAttribute("type")).toBe("tel");
    expect(phone.getAttribute("autocomplete")).toBe("tel");
  });

  it("offers a deal's currency as a choice, with the one it already has", () => {
    render(
      <DealFormDialog
        deal={null}
        companies={[COMPANY]}
        contacts={[]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    const currency = screen.getByLabelText("Currency") as HTMLSelectElement;
    expect(currency.tagName).toBe("SELECT");
    expect(currency.value).toBe("INR");
    expect(Array.from(currency.options).map((o) => o.value)).toContain("USD");
    expect(screen.getByLabelText(/^value/i).getAttribute("inputmode")).toBe("decimal");
  });
});

describe("Contacts on a phone", () => {
  it("is a list of cards, not a table, and a tap opens the contact's details in place", async () => {
    stubPhone();
    vi.spyOn(client, "listContacts").mockResolvedValue({
      items: [
        {
          id: "cont-1",
          full_name: "Asha Rao",
          title: "VP Ops",
          email: "asha@northwind.co",
          phone: "+91 98••• ••210",
          company_id: "comp-1",
          status: "customer",
          owner_id: "own-1",
          last_activity_at: null,
          created_at: "2024-02-12T09:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      size: 25,
      pages: 1,
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DirectoryContext.Provider value={{ ...EMPTY_DIRECTORY, status: "success", owners: [OWNER], companies: [COMPANY] }}>
          <ContactsPage />
        </DirectoryContext.Provider>
      </MemoryRouter>,
    );

    await screen.findByText("Asha Rao");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText("Last activity")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /Asha Rao/ }));
    await waitFor(() => expect(screen.getByText("asha@northwind.co")).toBeTruthy());
  });
});
