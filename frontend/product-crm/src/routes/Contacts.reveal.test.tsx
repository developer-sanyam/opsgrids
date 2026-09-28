/**
 * Revealing a contact's sealed phone number from the expandable row on the Contacts screen. The
 * step-up, the dialog and the api client are the real ones; the gateway is a stub. The behaviour
 * shared with the lead screen (one code per screen, 30 seconds, storage, focus) is pinned in
 * `LeadDetail.reveal.test.tsx`; what is pinned here is what is particular to a contact: the
 * endpoint, a number that may not be there, and the panel that closes.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearStepUpToken,
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from "@opsgrids/client";

import { ContactsPage } from "@/routes/Contacts";
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
    [STEP_UP]: () => stepUpToken(),
    [REVEAL_1]: () => json({ contact_id: "con-1", phone: PHONE_1 }),
    [REVEAL_2]: () => json({ contact_id: "con-2", phone: PHONE_2 }),
    [ME]: () => json({ user_id: "u-1", mfa_enabled: true }),
  };
  setAccessToken(SESSION_TOKEN);
  clearStepUpToken();
  unauthorized = vi.fn();
  setUnauthorizedHandler(unauthorized);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearStepUpToken();
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

  it("offers nothing without the grant, and keeps the phone masked", async () => {
    const user = userEvent.setup();
    await openContacts(["crm.book.contact.write:ORG", "crm.lead.lead.read_sensitive:ORG"]);

    await expand(user);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(0);
  });

  it("offers nothing without a second factor set up, and keeps the phone masked", async () => {
    const user = userEvent.setup();
    routes[ME] = () => json({ user_id: "u-1", mfa_enabled: false });
    await openContacts();

    await expand(user);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(0);
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
  it("asks for a code, then shows the number in place as a tel link, calling the contact's own endpoint with the step-up token", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);

    await user.click(revealPhone());
    expect(stepUpDialog()).toBeTruthy();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
    await enterCode(user);

    const link = await screen.findByRole("link", { name: PHONE_1 });
    expect(link.getAttribute("href")).toBe("tel:+919876543210");
    expect(screen.queryByText("+91 98••• ••210")).toBeNull();

    const [reveal] = gateway.callsTo(REVEAL_1);
    expect(reveal?.authorization).toBe(`Bearer ${STEP_UP_TOKEN}`);
    expect(reveal?.authorization).not.toContain(SESSION_TOKEN);
    expect(reveal?.body).toBeUndefined();
    expect(getAccessToken()).toBe(SESSION_TOKEN);
  });

  it("hides it again after 30 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByRole("link", { name: PHONE_1 });

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    expect(screen.queryByRole("link", { name: PHONE_1 })).toBeNull();
    expect(document.body.textContent).not.toContain("98765");
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
  });

  it("hides it when the person says so", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByRole("link", { name: PHONE_1 });

    await user.click(screen.getByRole("button", { name: "Hide phone number" }));

    expect(document.body.textContent).not.toContain("98765");
    expect(revealPhone()).toBeTruthy();
  });

  it("says so, kindly, when the contact has no number on file, and shows no number", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => json({ contact_id: "con-1", phone: null });
    await openContacts();
    await expand(user);

    await user.click(revealPhone());
    await enterCode(user);

    const sentence = await screen.findByText("No phone number is on file.");
    expect(sentence.closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(screen.queryByRole("link", { name: /\+91/ })).toBeNull();
    expect(revealPhone().hasAttribute("disabled")).toBe(false);
  });
});

describe("Contacts reveal: it does not outlive the panel", () => {
  it("drops the number, and the code's access, when the panel closes", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByRole("link", { name: PHONE_1 });

    await user.click(screen.getByRole("button", { name: "Collapse details" }));
    expect(document.body.textContent).not.toContain("98765");

    await expand(user);
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    await user.click(revealPhone());
    // The panel that earned the code is gone, and so is what the code allowed.
    expect(stepUpDialog()).toBeTruthy();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(1);
  });

  it("shows the next contact's panel masked, and asks again for its number", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByRole("link", { name: PHONE_1 });

    await expand(user, "Anita Desai");

    expect(screen.queryByRole("link", { name: PHONE_1 })).toBeNull();
    expect(document.body.textContent).not.toContain("98765");
    expect(screen.getByText("+91 99••• ••044")).toBeTruthy();
    await user.click(revealPhone());
    expect(stepUpDialog()).toBeTruthy();
    await enterCode(user);
    expect(await screen.findByRole("link", { name: PHONE_2 })).toBeTruthy();
    expect(gateway.callsTo(REVEAL_2)).toHaveLength(1);
  });

  it("does not leave the number, or the code's access, behind when the person leaves the screen", async () => {
    const user = userEvent.setup();
    const view = await openContacts();
    await expand(user);
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByRole("link", { name: PHONE_1 });

    view.unmount();
    expect(document.body.textContent).not.toContain("98765");

    // Coming back is a new visit: the number is masked and the code is asked for again.
    await openContacts();
    await expand(user);
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    await user.click(revealPhone());
    expect(stepUpDialog()).toBeTruthy();
  });
});

describe("Contacts reveal: a refusal is said, in words, under the number", () => {
  it("says a contact that is not this person's is not theirs to reveal", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => problem(403, "Out of scope for this resource", "forbidden");
    await openContacts();
    await expand(user);

    await user.click(revealPhone());
    await enterCode(user);

    expect(
      await screen.findByText("This contact is not assigned to you, so you cannot reveal its details."),
    ).toBeTruthy();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("says when the plan does not include it", async () => {
    const user = userEvent.setup();
    routes[REVEAL_1] = () => problem(402, "Payment required", "payment-required");
    await openContacts();
    await expand(user);

    await user.click(revealPhone());
    await enterCode(user);

    expect(await screen.findByText("Revealing details isn't part of your plan.")).toBeTruthy();
  });
});

describe("Contacts reveal: the code dialog", () => {
  it("leaves the mask and says nothing when cancelled, and gives focus back to Reveal", async () => {
    const user = userEvent.setup();
    await openContacts();
    await expand(user);
    await user.click(revealPhone());

    await user.click(within(stepUpDialog()).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("+91 98••• ••210")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
    await waitFor(() => expect(document.activeElement).toBe(revealPhone()));
  });

  it("explains that a second factor has to be set up first", async () => {
    const user = userEvent.setup();
    routes[STEP_UP] = () => problem(409, "No second factor", "mfa-not-enrolled");
    await openContacts();
    await expand(user);
    await user.click(revealPhone());

    await enterCode(user);

    expect(await screen.findByText(/isn't set up on your account yet/)).toBeTruthy();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
  });

  it("keeps the dialog on a wrong code", async () => {
    const user = userEvent.setup();
    routes[STEP_UP] = () => problem(401, "That code is not valid", "unauthorized");
    await openContacts();
    await expand(user);
    await user.click(revealPhone());

    await enterCode(user, "000000");

    await waitFor(() => expect(screen.getByText(/That code didn't work/)).toBeTruthy());
    expect(stepUpDialog()).toBeTruthy();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(gateway.callsTo(REVEAL_1)).toHaveLength(0);
  });
});
