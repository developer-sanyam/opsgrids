/**
 * The deal's primary contact carries the same Reveal as the Contacts screen — the same field, the
 * same step-up, the same 30 seconds. `LeadDetail.reveal.test.tsx` pins the behaviour in depth;
 * here it is enough to see it work where the deal puts it, be absent without the grant, and be
 * called for the right contact.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearStepUpToken,
  setAccessToken,
  setUnauthorizedHandler,
} from "@opsgrids/client";

import { DealDetailPage } from "@/routes/DealDetail";
import {
  enterCode,
  json,
  ME,
  problem,
  renderScreen,
  SESSION_TOKEN,
  stepUpDialog,
  stepUpToken,
  STEP_UP,
  STEP_UP_TOKEN,
  stubGateway,
} from "@/reveal/testKit";
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
    [STEP_UP]: () => stepUpToken(),
    [REVEAL]: () => json({ contact_id: "con-1", phone: PHONE }),
    [ME]: () => json({ user_id: "u-1", mfa_enabled: true }),
  };
  setAccessToken(SESSION_TOKEN);
  clearStepUpToken();
  setUnauthorizedHandler(vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearStepUpToken();
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

  it("offers nothing without a second factor set up, and keeps the phone masked", async () => {
    routes[ME] = () => json({ user_id: "u-1", mfa_enabled: false });
    await openDeal();

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("reveals the number after a code, from the contact's endpoint with the step-up token, and hides it on Hide", async () => {
    const user = userEvent.setup();
    await openDeal();

    await user.click(revealPhone());
    expect(stepUpDialog()).toBeTruthy();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    await enterCode(user);

    const link = await screen.findByRole("link", { name: PHONE });
    expect(link.getAttribute("href")).toBe("tel:+919876543210");
    expect(gateway.callsTo(REVEAL)[0]?.authorization).toBe(`Bearer ${STEP_UP_TOKEN}`);

    await user.click(screen.getByRole("button", { name: "Hide phone number" }));

    expect(document.body.textContent).not.toContain("98765");
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("leaves the mask and says nothing when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    await openDeal();
    await user.click(revealPhone());

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(revealPhone()));
  });

  it("says why, under the number, when the contact is not this person's", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(403, "Out of scope for this resource", "forbidden");
    await openDeal();

    await user.click(revealPhone());
    await enterCode(user);

    expect(
      await screen.findByText("This contact is not assigned to you, so you cannot reveal its details."),
    ).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });
});
