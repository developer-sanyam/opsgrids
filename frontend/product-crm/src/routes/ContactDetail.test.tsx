/** The contact page's Messaging card: channels and timeline from the 360, entitlement gate, unavailable and failure states. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { ContactDetailPage } from "@/routes/ContactDetail";
import { json, problem, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const CONTACT = {
  id: "con-1",
  full_name: "Rajesh Khanna",
  title: "VP Operations",
  email: null,
  phone: "+91 98••• ••210",
  company_id: "comp-1",
  status: "customer",
  owner_id: "own-1",
  last_activity_at: null,
  created_at: "2024-02-12T09:00:00Z",
};
const AT = "2026-08-22T11:20:00Z";
const THREE_SIXTY = {
  channels: {
    whatsapp: { opted_in: true, window_open_until: null, last_message_at: AT, conversations: 2 },
    email: { subscribed: false, last_email_at: null },
  },
  timeline: [{ kind: "whatsapp_message", title: "Sent order update", at: AT }],
  unavailable: [],
};
const GET_360 = "GET /api/v1/customers/con-1/360";

let routes: Record<string, Answer>;
let gateway: Gateway;

function show(entitlements: string[]) {
  gateway = stubGateway(routes);
  return renderScreen(<ContactDetailPage />, {
    at: "/contacts/con-1",
    path: "/contacts/:id",
    grants: ["crm.book.contact.read:ORG"],
    entitlements,
  });
}

beforeEach(() => {
  routes = { "GET /api/v1/contacts/con-1": () => json(CONTACT), [GET_360]: () => json(THREE_SIXTY) };
  setAccessToken(SESSION_TOKEN);
  setUnauthorizedHandler(vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
});

describe("Messaging card", () => {
  it("renders the channels and the timeline", async () => {
    show(["crm", "engage"]);
    expect(await screen.findByText("Opted in")).toBeTruthy();
    expect(screen.getByText("24-hour window: Closed")).toBeTruthy();
    expect(screen.getByText("Unsubscribed")).toBeTruthy();
    expect(screen.getByText("Sent order update")).toBeTruthy();
  });

  it("is hidden, and never asks, without the engage entitlement", async () => {
    show(["crm"]);
    await screen.findByRole("heading", { name: "Rajesh Khanna" });
    expect(screen.queryByRole("heading", { name: "Messaging" })).toBeNull();
    expect(gateway.callsTo(GET_360)).toHaveLength(0);
  });

  it("says Not available for channels the service could not read", async () => {
    routes[GET_360] = () =>
      json({ ...THREE_SIXTY, channels: { whatsapp: null, email: null }, unavailable: ["channels"] });
    show(["engage"]);
    expect(await screen.findAllByText("Not available")).toHaveLength(2);
  });

  it("says No WhatsApp yet / No email yet when Engage has no record of the contact", async () => {
    routes[GET_360] = () =>
      json({ ...THREE_SIXTY, channels: { whatsapp: null, email: null }, unavailable: [] });
    show(["engage"]);
    expect(await screen.findByText("No WhatsApp yet")).toBeTruthy();
    expect(screen.getByText("No email yet")).toBeTruthy();
    expect(screen.queryByText("Not available")).toBeNull();
  });

  it("shows the empty state when there is nothing", async () => {
    routes[GET_360] = () => json({ channels: { whatsapp: null, email: null }, timeline: [], unavailable: [] });
    show(["engage"]);
    expect(await screen.findByText("No messaging yet")).toBeTruthy();
  });

  it("shows an error with a retry that works", async () => {
    let calls = 0;
    routes[GET_360] = () => (++calls === 1 ? problem(500, "Messaging is down.") : json(THREE_SIXTY));
    show(["engage"]);
    await userEvent.click(await screen.findByRole("button", { name: /try again|retry/i }));
    expect(await screen.findByText("Opted in")).toBeTruthy();
  });
});
