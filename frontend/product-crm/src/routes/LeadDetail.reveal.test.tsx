/**
 * Revealing a lead's sealed phone number and email address, end to end on the real screen: the real
 * api client, and a stubbed gateway answering `POST /leads/{id}:reveal`.
 *
 * Owner decision 2026-10-15 (ADR-0023 addendum): a caller who holds the permission reveals with one
 * tap. No one-time code, no dialog, no auto-hide. What is held to account: nothing is fetched until
 * the tap; the call is an ordinary session-token call; a value, once revealed, stays for the
 * session and a second look at the same lead makes no second call (so no second audit row); it is
 * a tel:/mailto: link; it never reaches web storage or the URL; and a refusal is said in words.
 */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearRevealedValues, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { LeadDetailPage } from "@/routes/LeadDetail";
import { held, json, problem, renderScreen, SESSION_TOKEN, stubGateway, storedText } from "@/reveal/testKit";
import type { Answer, Gateway } from "@/reveal/testKit";

const REVEAL = "POST /api/v1/leads/lead-1:reveal";
const PHONE = "+91 98765 04821";
const EMAIL = "meera.iyer@solstice.example";

const LEAD = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: "Solstice Energy",
  email: "m•••@s•••",
  phone: "•••••• 4821",
  source: "form",
  status: "qualified",
  next_statuses: ["working", "disqualified"],
  owner_id: null,
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-08-20T09:00:00Z",
};

const GRANTED = ["crm.lead.lead.read_sensitive:ORG"];

let gateway: Gateway;
let routes: Record<string, Answer>;
let unauthorized: ReturnType<typeof vi.fn>;

function revealAnswer(call: { body: unknown }): Response {
  const { field } = call.body as { field: "phone" | "email" };
  return json({ lead_id: "lead-1", field, value: field === "phone" ? PHONE : EMAIL });
}

beforeEach(() => {
  routes = {
    "GET /api/v1/leads/lead-1": () => json(LEAD),
    "GET /api/v1/leads/lead-2": () => json({ ...LEAD, id: "lead-2", full_name: "Devansh Rao", phone: "•••••• 1177" }),
    [REVEAL]: revealAnswer,
  };
  setAccessToken(SESSION_TOKEN);
  clearRevealedValues();
  unauthorized = vi.fn();
  setUnauthorizedHandler(unauthorized);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearRevealedValues();
  localStorage.clear();
  sessionStorage.clear();
});

async function openLead(grants: string[] = GRANTED, links: string[] = []) {
  gateway = stubGateway(routes);
  const view = renderScreen(<LeadDetailPage />, { at: "/leads/lead-1", path: "/leads/:id", grants, links });
  await screen.findByRole("heading", { name: "Meera Iyer" });
  return view;
}

const revealPhone = () => screen.getByRole("button", { name: "Reveal phone number" });
const revealEmail = () => screen.getByRole("button", { name: "Reveal email address" });

describe("LeadDetail reveal: who is offered it", () => {
  it("offers a Reveal for the phone and for the email to someone who holds the permission", async () => {
    await openLead();

    expect(revealPhone()).toBeTruthy();
    expect(revealEmail()).toBeTruthy();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });

  it("offers it at own scope too: the endpoint decides whose lead it is", async () => {
    await openLead(["crm.lead.lead.read_sensitive:OWN"]);
    expect(revealPhone()).toBeTruthy();
  });

  it("offers nothing without the grant, keeps the values masked and says who to ask", async () => {
    await openLead(["crm.lead.lead.read:ORG"]);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.getByText(/Ask an administrator if you need the real values/)).toBeTruthy();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
  });

  it("offers no Reveal for a value the lead does not have", async () => {
    routes["GET /api/v1/leads/lead-1"] = () => json({ ...LEAD, phone: null });
    await openLead();

    expect(screen.queryByRole("button", { name: "Reveal phone number" })).toBeNull();
    expect(revealEmail()).toBeTruthy();
    expect(screen.getByText("None on file")).toBeTruthy();
  });

  it("says a reveal is recorded, with no mention of a code", async () => {
    await openLead();

    const note = screen.getByText(/Email and phone are masked\./);
    expect(note.textContent).toContain("Revealing one is recorded.");
    expect(note.textContent).not.toMatch(/code/i);
  });

  it("says, for a converted lead, where the contact went", async () => {
    routes["GET /api/v1/leads/lead-1"] = () =>
      json({ ...LEAD, status: "converted", converted_contact_id: "con-9", converted_at: "2026-08-21T09:00:00Z" });
    await openLead();

    expect(screen.getByText(/the contact created from it is under Contacts/)).toBeTruthy();
  });
});

describe("LeadDetail reveal: one tap, no code", () => {
  it("fetches nothing until the tap, then shows the number as a tel link with no dialog", async () => {
    const user = userEvent.setup();
    await openLead();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    expect(document.body.textContent).not.toContain("98765");

    await user.click(revealPhone());

    const link = await screen.findByRole("link", { name: PHONE });
    expect(link.getAttribute("href")).toBe("tel:+919876504821");
    expect(link.className).toContain("min-h-11");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(gateway.callsTo(REVEAL)).toHaveLength(1);
    expect(gateway.callsTo(REVEAL)[0]?.body).toEqual({ field: "phone" });
  });

  it("shows an email as a mailto link", async () => {
    const user = userEvent.setup();
    await openLead();

    await user.click(revealEmail());

    expect((await screen.findByRole("link", { name: EMAIL })).getAttribute("href")).toBe(`mailto:${EMAIL}`);
  });

  it("calls with the session token, and there is no step-up call at all", async () => {
    const user = userEvent.setup();
    await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });

    expect(gateway.callsTo(REVEAL)[0]?.authorization).toBe(`Bearer ${SESSION_TOKEN}`);
    expect(gateway.calls.some((call) => call.key.includes("/auth/"))).toBe(false);
  });

  it("reveals a phone and then an email with one tap each", async () => {
    const user = userEvent.setup();
    await openLead();

    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });
    await user.click(revealEmail());

    expect(await screen.findByRole("link", { name: EMAIL })).toBeTruthy();
    expect(gateway.callsTo(REVEAL)).toHaveLength(2);
  });
});

describe("LeadDetail reveal: it stays for the session", () => {
  it("leaves no Hide control, and does not hide by itself", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });

    expect(screen.queryByRole("button", { name: /hide/i })).toBeNull();
    expect(screen.queryByText(/hides again/i)).toBeNull();
    act(() => {
      vi.advanceTimersByTime(10 * 60 * 1000);
    });

    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
  });

  it("shows it again, with no second call, when the person leaves the lead and comes back", async () => {
    const user = userEvent.setup();
    await openLead(GRANTED, ["/leads/lead-2", "/leads/lead-1"]);
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });

    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-2" }));
    await screen.findByRole("heading", { name: "Devansh Rao" });
    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 1177")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-1" }));
    await screen.findByRole("heading", { name: "Meera Iyer" });

    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reveal phone number" })).toBeNull();
    expect(gateway.callsTo(REVEAL)).toHaveLength(1);
  });

  it("shows it after the screen is left and re-opened in the same session, with no second call", async () => {
    const user = userEvent.setup();
    const view = await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });
    view.unmount();

    await openLead();

    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
  });

  it("is masked again once the session ends (the cache is emptied on sign-out)", async () => {
    const user = userEvent.setup();
    const view = await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });
    view.unmount();

    clearRevealedValues();
    await openLead();

    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(revealPhone()).toBeTruthy();
  });

  it("drops a reveal that was still on its way when the person moved to another lead", async () => {
    const user = userEvent.setup();
    const slow = held();
    routes[REVEAL] = slow.route;
    await openLead(GRANTED, ["/leads/lead-1", "/leads/lead-2"]);
    await user.click(revealPhone());
    await waitFor(() => expect(gateway.callsTo(REVEAL)).toHaveLength(1));

    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-2" }));
    await screen.findByRole("heading", { name: "Devansh Rao" });
    await act(async () => {
      slow.respond(json({ lead_id: "lead-1", field: "phone", value: PHONE }));
    });

    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 1177")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-1" }));
    await screen.findByRole("heading", { name: "Meera Iyer" });
    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });

  it("never writes the value to web storage or the URL", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });
    await user.click(revealEmail());
    await screen.findByRole("link", { name: EMAIL });

    const written = JSON.stringify(setItem.mock.calls);
    for (const secret of [PHONE, "98765", EMAIL, "meera.iyer"]) {
      expect(written).not.toContain(secret);
      expect(storedText()).not.toContain(secret);
      expect(screen.getByTestId("location").textContent).not.toContain(secret);
      expect(window.location.href).not.toContain(secret);
    }
    expect(screen.getByTestId("location").textContent).toBe("/leads/lead-1");
  });
});

describe("LeadDetail reveal: a refusal is said, in words, under the field", () => {
  const CASES: { name: string; status: number; detail: string; slug: string; message: string }[] = [
    {
      name: "a lead that is not this person's",
      status: 403,
      detail: "Out of scope for this resource",
      slug: "forbidden",
      message: "This lead is not assigned to you, so you cannot reveal its details.",
    },
    {
      name: "a missing permission",
      status: 403,
      detail: "Missing permission: crm.lead.lead.read_sensitive",
      slug: "forbidden",
      message: "You don't have permission to reveal this lead's details. Ask an administrator for access.",
    },
    {
      name: "a plan that does not include it",
      status: 402,
      detail: "Payment required",
      slug: "payment-required",
      message: "Revealing details isn't part of your plan.",
    },
    {
      name: "a phone number that is not on file",
      status: 404,
      detail: "No phone number is on file for this lead",
      slug: "not-found",
      message: "No phone number is on file for this lead.",
    },
    {
      name: "a lead that is not in this organization",
      status: 404,
      detail: "That lead is not part of this organization",
      slug: "not-found",
      message: "That lead is not part of this organization.",
    },
    {
      name: "a reveal budget that is spent",
      status: 429,
      detail: "Too many requests. Please slow down.",
      slug: "rate-limited",
      message: "Too many requests. Please slow down.",
    },
  ];

  it.each(CASES)("$name", async ({ status, detail, slug, message }) => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(status, detail, slug);
    await openLead();

    await user.click(revealPhone());

    const sentence = await screen.findByText(message);
    // In a live region that was already on the page, so it is said without anyone looking for it.
    expect(sentence.closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(revealPhone().hasAttribute("disabled")).toBe(false);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("keeps the other field's value when one field is refused", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = (call) =>
      (call.body as { field: string }).field === "phone"
        ? json({ lead_id: "lead-1", field: "phone", value: PHONE })
        : problem(404, "No email address is on file for this lead", "not-found");
    await openLead();
    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });

    await user.click(revealEmail());

    expect(await screen.findByText("No email address is on file for this lead.")).toBeTruthy();
    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
  });

  it("clears the sentence when the person tries again", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(403, "Out of scope for this resource", "forbidden");
    await openLead();
    await user.click(revealPhone());
    await screen.findByText(/not assigned to you/);

    routes[REVEAL] = revealAnswer;
    await user.click(revealPhone());

    await screen.findByRole("link", { name: PHONE });
    expect(screen.queryByText(/not assigned to you/)).toBeNull();
  });

  it("says so, in words, when the connection drops", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => {
      throw new TypeError("Failed to fetch");
    };
    await openLead();
    await user.click(revealPhone());

    expect(await screen.findByText("Couldn't reveal this right now. Try again.")).toBeTruthy();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });
});

describe("LeadDetail reveal: while it is under way", () => {
  it("sends a reveal once however many times the button is pressed", async () => {
    const user = userEvent.setup();
    const slow = held();
    routes[REVEAL] = slow.route;
    await openLead();

    await user.click(revealPhone());
    await user.click(revealPhone());
    await user.click(revealPhone());
    expect(gateway.callsTo(REVEAL)).toHaveLength(1);

    await act(async () => {
      slow.respond(json({ lead_id: "lead-1", field: "phone", value: PHONE }));
    });
    expect(await screen.findByRole("link", { name: PHONE })).toBeTruthy();
  });
});

describe("LeadDetail reveal: keyboard and screen reader", () => {
  it("is a real button for each field, named for the field it reveals", async () => {
    await openLead();
    expect(revealPhone().tagName).toBe("BUTTON");
    expect(revealEmail().tagName).toBe("BUTTON");
  });

  it("can be done from the keyboard alone: Enter on Reveal", async () => {
    const user = userEvent.setup();
    await openLead();
    revealPhone().focus();

    await user.keyboard("{Enter}");

    expect(await screen.findByRole("link", { name: PHONE })).toBeTruthy();
  });

  it("announces the reveal in a live region that was on the page before, without saying the number", async () => {
    const user = userEvent.setup();
    await openLead();
    const regions = () => document.querySelectorAll('[aria-live="polite"]');
    const before = regions().length;
    expect(before).toBeGreaterThanOrEqual(2);

    await user.click(revealPhone());
    await screen.findByRole("link", { name: PHONE });

    expect(regions().length).toBe(before);
    const announced = screen.getByText(/Phone number revealed\./).closest("[aria-live]");
    expect(announced?.textContent).not.toContain("98765");
  });
});
