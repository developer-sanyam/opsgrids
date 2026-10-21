import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { LeadsPage } from "@/routes/Leads";
import { json, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import { setAccessToken } from "@opsgrids/client";

const lead = (id: string, name: string, company: string = "Acme Traders") => ({
  id,
  full_name: name,
  company,
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
});

interface LeadItem {
  id: string;
  full_name: string;
  company: string;
  email: string;
  phone: string | null;
  source: string;
  status: string;
  next_statuses: string[];
  owner_id: string | null;
  notes: string;
  converted_company_id: string | null;
  converted_contact_id: string | null;
  converted_deal_id: string | null;
  converted_at: string | null;
  created_at: string;
}

interface LeadPageData {
  items: LeadItem[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

describe("Leads list: search", () => {
  let leadsData: LeadPageData;

  beforeEach(() => {
    setAccessToken(SESSION_TOKEN);
    leadsData = {
      items: [
        lead("lead-1", "Priya Sharma", "Acme Traders"),
        lead("lead-abhay", "Abhay Kumar", "Abhay Pharma"),
      ],
      total: 2,
      page: 1,
      size: 25,
      pages: 1,
    };

    stubGateway({
      "GET /api/v1/leads": () => {
        // Read the last fetch call to inspect query params
        const fetchMock = globalThis.fetch as unknown as Mock;
        const lastCall = fetchMock.mock.calls.at(-1) as [string, RequestInit] | undefined;
        const urlStr = String(lastCall?.[0] || "");
        if (urlStr.includes("q=Abhay")) {
          return json({
            items: [lead("lead-abhay", "Abhay Kumar", "Abhay Pharma")],
            total: 1,
            page: 1,
            size: 25,
            pages: 1,
          });
        }
        if (urlStr.includes("q=Nonexistent")) {
          return json({
            items: [],
            total: 0,
            page: 1,
            size: 25,
            pages: 0,
          });
        }
        return json(leadsData);
      },
      "GET /api/v1/crm/import-export/specs": () => json({ specs: [] }),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the search input and filters leads by query", async () => {
    const user = userEvent.setup();
    renderScreen(<LeadsPage />, {
      at: "/leads",
      path: "/leads",
      grants: ["crm.lead.lead.read:ORG"],
    });

    // Wait for initial load
    expect(await screen.findByText("Priya Sharma")).toBeTruthy();
    expect(screen.getByText("Abhay Kumar")).toBeTruthy();

    const searchInput = screen.getByLabelText("Search leads");
    expect(searchInput).toBeTruthy();

    // Type "Abhay"
    await user.type(searchInput, "Abhay");

    // Wait for debounce and query
    await waitFor(() => {
      expect(screen.getByText("Abhay Kumar")).toBeTruthy();
      expect(screen.queryByText("Priya Sharma")).toBeNull();
    });

    const fetchMock = globalThis.fetch as unknown as Mock;
    expect(
      fetchMock.mock.calls.some((c: unknown[]) => String(c[0]).includes("q=Abhay"))
    ).toBe(true);
  });

  it("shows empty state with clear search button when no leads match", async () => {
    const user = userEvent.setup();
    renderScreen(<LeadsPage />, {
      at: "/leads",
      path: "/leads",
      grants: ["crm.lead.lead.read:ORG"],
    });

    expect(await screen.findByText("Priya Sharma")).toBeTruthy();

    const searchInput = screen.getByLabelText("Search leads");
    await user.type(searchInput, "Nonexistent");

    expect(await screen.findByText("No leads match that search")).toBeTruthy();
    const clearBtn = screen.getByRole("button", { name: "Clear search" });
    expect(clearBtn).toBeTruthy();

    await user.click(clearBtn);

    // After clearing, full list is back
    expect(await screen.findByText("Priya Sharma")).toBeTruthy();
  });
});
