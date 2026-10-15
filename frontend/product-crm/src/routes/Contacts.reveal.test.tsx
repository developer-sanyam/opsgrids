/**
 * Revealing a contact's sealed phone number from the expandable row on the Contacts screen. The
 * api client is the real one; the gateway is a stub. One tap, no code (ADR-0023 addendum,
 * 2026-10-15). The behaviour shared with the lead screen is pinned in
 * `LeadDetail.reveal.test.tsx`; what is pinned here is what is particular to a contact: the
 * endpoint, a number that may not be there, and a panel that closes and opens again.
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearRevealedValues, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { ContactsPage } from "@/routes/Contacts";
import { json, problem, renderScreen, SESSION_TOKEN, stubGateway } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const REVEAL_1 = "POST /api/v1/contacts/con-1/phone:reveal";
const REVEAL_2 = "POST /api/v1/contacts/con-2/phone:reveal";
const PHONE_1 = "+91 98765 43210";
const PHONE_2 = "+91 99765 43044";

function contact(id: string, name: string, phone: string) {
  return {
    id,
    full_name: name,
    title: "VP Operations",
    email: `${name.toLowerCase().replace(" ", ".")}@northwind.co`,
    phone,
    company_id: "comp-1",
    status: "customer",
    owner_id: "own-1",
    last_activity_at: "2026-08-22T11:20:00Z",
    created_at: "2024-02-12T09:00:00Z",
  };
}

const RAJESH = contact("con-1", "Rajesh Khanna", "+91 98••• ••210");
const ANITA = contact("con-2", "Anita Desai", "+91 99••• ••044");

const GRANTED = ["crm.book.contact.read_sensitive:ORG"];

let gateway: Gateway;
let routes: Record<string, Answer>;
let unauthorized: ReturnType<typeof vi.fn>;

beforeEach(() => {
  routes = {
    "GET /api/v1/contacts": () =>
      json({ items: [RAJESH, ANITA], total: 2, page: 1, size: 25, pages: 1 }),
    [REVEAL_1]: () => json({ contact_id: "con-1", phone: PHONE_1 }),
    [REVEAL_2]: () => json({ contact_id: "con-2", phone: PHONE_2 }),
  };
  setAccessToken(SESSION_TOKEN);
  clearRevealedValues();
  unauthorized = vi.fn();
  setUnauthorizedHandler(unauthorized);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearRevealedValues();
  localStorage.clear();
  sessionStorage.clear();
});

async function openContacts(grants: string[] = GRANTED) {
  gateway = stubGateway(routes);
  const view = renderScreen(<ContactsPage />, { at: "/contacts", path: "/contacts", grants });
  await screen.findByText("Rajesh Khanna");
  return view;
}

/** Opens the detail panel of the named contact. The list is sorted by name, so position is no guide. */
async function expand(user: ReturnType<typeof userEvent.setup>, name = "Rajesh Khanna") {
  const row = screen.getByText(name).closest("tr");
  if (row === null) throw new Error(`No row for ${name}`);
  await user.click(within(row).getByRole("button", { name: "Expand details" }));
}

const revealPhone = () => screen.getByRole("button", { name: "Reveal phone number" });

describe("Contacts reveal: who is offered it", () => {
  it("offers a Reveal in the open panel to someone who holds the permission", async () => {
    const user = userEvent.setup();
    await openContacts();

    // Nothing to reveal, and nothing offered, until a panel is open.
    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    await expand(user);

    expect(revealPhone()).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("offers nothing without the grant, and keeps the phone masked (negative authz)", async () => {
    const user = userEvent.setup();
    await openContacts(["crm.book.contact.write:ORG", "crm.lead.lead.read_sensitive:ORG"]);

    await expand(user);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
  });

  it("offers no email reveal: a contact's email is not sealed", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);

    expect(screen.queryByRole("button", { name: /email/i })).toBeNull();
    expect(screen.getByRole("link", { name: "rajesh.khanna@northwind.co" })).toBeTruthy();
  });

  it("offers no Reveal for a contact with no number at all", async () => {
    const user = userEvent.setup();
    routes["GET /api/v1/contacts"] = () =>
      json({ items: [{ ...RAJESH, phone: "" }], total: 1, page: 1, size: 25, pages: 1 });
    await openContacts();

    await expand(user);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("None on file")).toBeTruthy();
  });
});

describe("Contacts reveal: the happy path", () => {
  it("shows the number in place on one tap, as a tel link, with no code dialog and the session token", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);

    await user.click(revealPhone());

    const link = await screen.findByRole("link", { name: PHONE_1 });
    expect(link.getAttribute("href")).toBe("tel:+919876543210");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("+91 98••• ••210")).toBeNull();

    const [reveal] = gateway.callsTo(REVEAL_1);
    expect(reveal?.authorization).toBe(`Bearer ${SESSION_TOKEN}`);
    expect(reveal?.body).toBeUndefined();
    expect(gateway.calls.some((call) => call.key.includes("/auth/"))).toBe(false);
  });

  it("says so, kindly, when the contact has no number on file, and shows no number", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => json({ contact_id: "con-1", phone: null });
    await openContacts();
    await expand(user);

    await user.click(revealPhone());

    const sentence = await screen.findByText("No phone number is on file.");
    expect(sentence.closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(screen.queryByRole("link", { name: /\+91/ })).toBeNull();
    expect(revealPhone().hasAttribute("disabled")).toBe(false);
  });
});

describe("Contacts reveal: it stays for the session", () => {
  it("shows the number again, with no second call, when the panel is closed and opened", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE_1 });

    await user.click(screen.getByRole("button", { name: "Collapse details" }));
    await expand(user);

    expect(screen.getByRole("link", { name: PHONE_1 })).toBeTruthy();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(1);
  });

  it("keeps the next contact's panel masked, and reveals its number on its own tap", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE_1 });

    await expand(user, "Anita Desai");

    expect(screen.queryByRole("link", { name: PHONE_2 })).toBeNull();
    expect(screen.getByText("+91 99••• ••044")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Reveal phone number" }));
    expect(await screen.findByRole("link", { name: PHONE_2 })).toBeTruthy();
    expect(gateway.callsTo(REVEAL_2)).toHaveLength(1);
  });

  it("shows the number after the person leaves and returns to the screen, until the session ends", async () => {
    const user = userEvent.setup();
    const view = await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE_1 });
    view.unmount();

    const again = await openContacts();
    await expand(user);
    expect(screen.getByRole("link", { name: PHONE_1 })).toBeTruthy();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
    again.unmount();

    // Sign-out empties the cache: a new visit is masked again.
    clearRevealedValues();
    await openContacts();
    await expand(user);
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });
});

describe("Contacts reveal: a refusal is said, in words, under the number", () => {
  it("says a contact that is not this person's is not theirs to reveal", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => problem(403, "Out of scope for this resource", "forbidden");
    await openContacts();
    await expand(user);

    await user.click(revealPhone());

    expect(
      await screen.findByText("This contact is not assigned to you, so you cannot reveal its details."),
    ).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("says when the plan does not include it", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => problem(402, "Payment required", "payment-required");
    await openContacts();
    await expand(user);

    await user.click(revealPhone());

    expect(await screen.findByText("Revealing details isn't part of your plan.")).toBeTruthy();
  });

  it("says when the reveal budget is spent", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => problem(429, "Too many requests. Please slow down.", "rate-limited");
    await openContacts();
    await expand(user);

    await user.click(revealPhone());

    expect(await screen.findByText("Too many requests. Please slow down.")).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });
});
