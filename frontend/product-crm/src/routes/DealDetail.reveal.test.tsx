/**
 * The deal's primary contact carries the same Reveal as the Contacts screen — the same field, one
 * tap, no code, and it stays for the session (ADR-0023 addendum, 2026-10-15). `LeadDetail.reveal.test.tsx` pins the behaviour in depth;
 * here it is enough to see it work where the deal puts it, be absent without the grant, and be
 * called for the right contact.
 */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearRevealedValues, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { DealDetailPage } from "@/routes/DealDetail";
import { json, problem, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const REVEAL = "POST /api/v1/contacts/con-1/phone:reveal";
const PHONE = "+91 98765 43210";

const COMPANY = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer" as const,
  owner_id: "own-1",
  annual_revenue_minor: 840000000000,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

const CONTACT = {
  id: "con-1",
  full_name: "Rajesh Khanna",
  title: "VP Operations",
  email: "rajesh.khanna@northwind.co",
  phone: "+91 98••• ••210",
  company_id: "comp-1",
  status: "customer" as const,
  owner_id: "own-1",
  last_activity_at: "2026-08-22T11:20:00Z",
  created_at: "2024-02-12T09:00:00Z",
};

const DEAL = {
  id: "deal-1",
  name: "Fleet telematics rollout",
  company_id: "comp-1",
  primary_contact_id: "con-1",
  value_minor: 1_000_000,
  currency: "INR",
  stage: "proposal",
  owner_id: "own-1",
  probability: 60,
  close_date: "2026-12-31",
  created_at: "2026-08-01T09:00:00Z",
  last_activity_at: null,
  source: "referral",
};

const GRANTED = ["crm.book.contact.read_sensitive:ORG"];

let gateway: Gateway;
let routes: Record<string, Answer>;

beforeEach(() => {
  routes = {
    "GET /api/v1/deals/deal-1": () => json(DEAL),
    "GET /api/v1/deals/activities": () => json({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
    [REVEAL]: () => json({ contact_id: "con-1", phone: PHONE }),
  };
  setAccessToken(SESSION_TOKEN);
  clearRevealedValues();
  setUnauthorizedHandler(vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearRevealedValues();
  localStorage.clear();
  sessionStorage.clear();
});

async function openDeal(grants: string[] = GRANTED) {
  gateway = stubGateway(routes);
  renderScreen(<DealDetailPage />, {
    at: "/deals/deal-1",
    path: "/deals/:id",
    grants,
    directory: { companies: [COMPANY], contacts: [CONTACT] },
  });
  await screen.findByRole("heading", { name: "Fleet telematics rollout" });
}

const revealPhone = () => screen.getByRole("button", { name: "Reveal phone number" });

describe("DealDetail reveal", () => {
  it("offers a Reveal for the primary contact's phone to someone who holds the permission", async () => {
    await openDeal();

    expect(revealPhone()).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("offers nothing without the grant, and keeps the phone masked", async () => {
    await openDeal(["crm.pipeline.deal.write:ORG", "crm.lead.lead.read_sensitive:ORG"]);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("reveals the number on one tap, with no code dialog, from the contact's endpoint with the session token", async () => {
    const user = userEvent.setup();
    await openDeal();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);

    await user.click(revealPhone());

    const link = await screen.findByRole("link", { name: PHONE });
    expect(link.getAttribute("href")).toBe("tel:+919876543210");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(gateway.callsTo(REVEAL)[0]?.authorization).toBe(`Bearer ${SESSION_TOKEN}`);
    // It stays: no Hide, and it is not re-fetched on another look.
    expect(screen.queryByRole("button", { name: /hide/i })).toBeNull();
    expect(gateway.callsTo(REVEAL)).toHaveLength(1);
  });

  it("says why, under the number, when the contact is not this person's", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(403, "Out of scope for this resource", "forbidden");
    await openDeal();

    await user.click(revealPhone());

    expect(
      await screen.findByText("This contact is not assigned to you, so you cannot reveal its details."),
    ).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });
});
