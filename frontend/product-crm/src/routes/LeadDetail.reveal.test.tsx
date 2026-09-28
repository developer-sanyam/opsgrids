/**
 * Revealing a lead's sealed phone number and email address, end to end on the real screen: the real
 * `useStepUp`, the real `StepUpDialog`, the real api client, and a stubbed gateway answering what
 * the auth service and `POST /leads/{id}:reveal` would.
 *
 * What is held to account, in order of what a mistake would cost: a real number that lingers
 * (after Hide, after 30 seconds, after leaving the lead, in storage, in the URL); the session token
 * standing in for the step-up token, or a lapsed step-up token signing the person out; a reveal
 * that starts without a code; and a refusal that is not said, or is said somewhere it is missed.
 */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearStepUpToken,
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from "@opsgrids/client";

import { LeadDetailPage } from "@/routes/LeadDetail";
import {
  codeField,
  enterCode,
  held,
  json,
  ME,
  problem,
  REFRESH,
  renderScreen,
  SESSION_TOKEN,
  stepUpDialog,
  stepUpToken,
  STEP_UP,
  STEP_UP_TOKEN,
  stubGateway,
  storedText,
} from "@/reveal/testKit";
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
    [STEP_UP]: () => stepUpToken(),
    [REVEAL]: revealAnswer,
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
  vi.restoreAllMocks();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearStepUpToken();
  localStorage.clear();
  sessionStorage.clear();
});

async function openLead(grants: string[] = GRANTED, links: string[] = []) {
  gateway = stubGateway(routes);
  renderScreen(<LeadDetailPage />, { at: "/leads/lead-1", path: "/leads/:id", grants, links });
  await screen.findByRole("heading", { name: "Meera Iyer" });
}

const revealPhone = () => screen.getByRole("button", { name: "Reveal phone number" });
const revealEmail = () => screen.getByRole("button", { name: "Reveal email address" });

async function revealPhoneWithCode(user: ReturnType<typeof userEvent.setup>) {
  await user.click(revealPhone());
  await enterCode(user);
  await screen.findByRole("link", { name: PHONE });
}

describe("LeadDetail reveal: who is offered it", () => {
  it("offers a Reveal for the phone and for the email to someone who holds the permission", async () => {
    await openLead();

    expect(revealPhone()).toBeTruthy();
    expect(revealEmail()).toBeTruthy();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.getByText("m•••@s•••")).toBeTruthy();
  });

  it("offers it at own scope too: the endpoint decides whose lead it is", async () => {
    await openLead(["crm.lead.lead.read_sensitive:OWN"]);

    expect(revealPhone()).toBeTruthy();
  });

  it("offers nothing without the grant, keeps the values masked and says who to ask", async () => {
    await openLead(["crm.lead.lead.write:ORG", "crm.book.contact.read_sensitive:ORG"]);

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.getByText(/Ask an administrator if you need the real values/)).toBeTruthy();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(0);
  });

  it("offers nothing without a second factor set up, keeps the values masked, and points at the Security page instead", async () => {
    routes[ME] = () => json({ user_id: "u-1", mfa_enabled: false });
    await openLead();

    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    const note = screen.getByText(/Email and phone are masked\./);
    expect(note.textContent).toContain("Set up two-factor authentication on the Security page");
    expect(screen.getByRole("link", { name: "Security page" })).toBeTruthy();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(0);
  });

  it("offers no Reveal for a value the lead does not have", async () => {
    routes["GET /api/v1/leads/lead-1"] = () => json({ ...LEAD, phone: null });
    await openLead();

    expect(screen.queryByRole("button", { name: "Reveal phone number" })).toBeNull();
    expect(revealEmail()).toBeTruthy();
    expect(screen.getByText("None on file")).toBeTruthy();
  });

  it("says what a reveal involves, and for a converted lead where the contact went", async () => {
    routes["GET /api/v1/leads/lead-1"] = () =>
      json({
        ...LEAD,
        status: "converted",
        converted_company_id: "comp-1",
        converted_contact_id: "con-1",
        converted_deal_id: "deal-1",
        converted_at: "2026-08-25T10:00:00Z",
      });
    await openLead();

    const note = screen.getByText(/Email and phone are masked\./);
    expect(note.textContent).toContain("A reveal needs a one-time code and is recorded.");
    expect(note.textContent).toContain("This lead has been converted; the contact created from it is under Contacts.");
  });
});

describe("LeadDetail reveal: the happy path", () => {
  it("asks for a code before it asks for anything, then shows the number in place as a tel link", async () => {
    const user = userEvent.setup();
    await openLead();

    await user.click(revealPhone());

    expect(stepUpDialog()).toBeTruthy();
    // Nothing was asked of the reveal endpoint before the person proved anything.
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();

    await enterCode(user);

    const link = await screen.findByRole("link", { name: PHONE });
    expect(link.getAttribute("href")).toBe("tel:+919876504821");
    expect(screen.queryByText("•••••• 4821")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Hide phone number" })).toBeTruthy();
  });

  it("shows an email as a mailto link", async () => {
    const user = userEvent.setup();
    await openLead();

    await user.click(revealEmail());
    await enterCode(user);

    const link = await screen.findByRole("link", { name: EMAIL });
    expect(link.getAttribute("href")).toBe(`mailto:${EMAIL}`);
  });

  it("sends the code with the session token, and the reveal with the step-up token and never the session token", async () => {
    const user = userEvent.setup();
    await openLead();

    await revealPhoneWithCode(user);

    const [verify] = gateway.callsTo(STEP_UP);
    expect(verify?.body).toEqual({ code: "123456" });
    expect(verify?.authorization).toBe(`Bearer ${SESSION_TOKEN}`);

    const [reveal] = gateway.callsTo(REVEAL);
    expect(reveal?.body).toEqual({ field: "phone" });
    expect(reveal?.authorization).toBe(`Bearer ${STEP_UP_TOKEN}`);
    expect(reveal?.authorization).not.toContain(SESSION_TOKEN);
    expect(getAccessToken()).toBe(SESSION_TOKEN);
  });

  it("asks once for a phone and then an email: one code, one step-up call, no second dialog", async () => {
    const user = userEvent.setup();
    await openLead();
    await revealPhoneWithCode(user);

    await user.click(revealEmail());

    expect(await screen.findByRole("link", { name: EMAIL })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(gateway.callsTo(STEP_UP)).toHaveLength(1);
    expect(gateway.callsTo(REVEAL).map((call) => call.authorization)).toEqual([
      `Bearer ${STEP_UP_TOKEN}`,
      `Bearer ${STEP_UP_TOKEN}`,
    ]);
    // Both are showing at once.
    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
  });
});

describe("LeadDetail reveal: it does not stay", () => {
  it("hides the number again when the person says so, and the number is gone from the page", async () => {
    const user = userEvent.setup();
    await openLead();
    await revealPhoneWithCode(user);

    await user.click(screen.getByRole("button", { name: "Hide phone number" }));

    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(document.body.textContent).not.toContain("98765");
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(revealPhone()).toBeTruthy();
  });

  it("hides it by itself after 30 seconds, and not before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLead();
    await revealPhoneWithCode(user);

    await act(async () => {
      vi.advanceTimersByTime(25_000);
    });
    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });

    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(document.body.textContent).not.toContain("98765");
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(revealPhone()).toBeTruthy();
  });

  it("times each field on its own clock: the email hides 30 seconds after it was revealed, not after the phone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLead();
    await revealPhoneWithCode(user);
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    await user.click(revealEmail());
    await screen.findByRole("link", { name: EMAIL });

    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });

    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(screen.getByRole("link", { name: EMAIL })).toBeTruthy();
  });

  it("hides the value when the person goes to another lead on the same screen, and the code's access with it", async () => {
    const user = userEvent.setup();
    await openLead(GRANTED, ["/leads/lead-2"]);
    await revealPhoneWithCode(user);

    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-2" }));

    await screen.findByRole("heading", { name: "Devansh Rao" });
    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 1177")).toBeTruthy();
    // The next reveal is a new question: it asks for a code again.
    await user.click(revealPhone());
    expect(stepUpDialog()).toBeTruthy();
  });

  it("drops a reveal that was still on its way when the person moved to another lead", async () => {
    const user = userEvent.setup();
    const slow = held();
    routes[REVEAL] = slow.route;
    await openLead(GRANTED, ["/leads/lead-1", "/leads/lead-2"]);
    await user.click(revealPhone());
    await enterCode(user);
    await waitFor(() => expect(gateway.callsTo(REVEAL)).toHaveLength(1));

    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-2" }));
    await screen.findByRole("heading", { name: "Devansh Rao" });
    await act(async () => {
      slow.respond(json({ lead_id: "lead-1", field: "phone", value: PHONE }));
    });

    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 1177")).toBeTruthy();

    // And it is not waiting to appear when the person comes back to the lead it was for.
    await user.click(screen.getByRole("button", { name: "Go to /leads/lead-1" }));
    await screen.findByRole("heading", { name: "Meera Iyer" });
    expect(screen.queryByText(PHONE)).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });

  it("never writes the value, or the token, to web storage or the URL", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    await openLead();
    await revealPhoneWithCode(user);
    await user.click(revealEmail());
    await screen.findByRole("link", { name: EMAIL });

    const written = JSON.stringify(setItem.mock.calls);
    for (const secret of [PHONE, "98765", EMAIL, "meera.iyer", STEP_UP_TOKEN]) {
      expect(written).not.toContain(secret);
      expect(storedText()).not.toContain(secret);
      expect(screen.getByTestId("location").textContent).not.toContain(secret);
      expect(window.location.href).not.toContain(secret);
    }
    expect(screen.getByTestId("location").textContent).toBe("/leads/lead-1");
  });
});

describe("LeadDetail reveal: the code dialog", () => {
  it("leaves the mask, and says nothing, when the dialog is cancelled; focus goes back to Reveal", async () => {
    const user = userEvent.setup();
    await openLead();
    await user.click(revealPhone());

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent).not.toMatch(/couldn't|permission|expired|not assigned/);
    expect(gateway.callsTo(STEP_UP)).toHaveLength(0);
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    await waitFor(() => expect(document.activeElement).toBe(revealPhone()));
    expect(revealPhone().hasAttribute("disabled")).toBe(false);
    expect(revealEmail().hasAttribute("disabled")).toBe(false);
  });

  it("keeps the dialog open on a wrong code, and reveals on the next good one", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    routes[STEP_UP] = () => {
      attempts += 1;
      return attempts === 1 ? problem(401, "That code is not valid", "unauthorized") : stepUpToken();
    };
    await openLead();
    await user.click(revealPhone());

    await enterCode(user, "000000");

    await waitFor(() => expect(screen.getByText(/That code didn't work/)).toBeTruthy());
    expect(stepUpDialog()).toBeTruthy();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION_TOKEN);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();

    await enterCode(user, "123456");

    expect(await screen.findByRole("link", { name: PHONE })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("explains, in the dialog, that a second factor has to be set up first, and reveals nothing", async () => {
    const user = userEvent.setup();
    routes[STEP_UP] = () => problem(409, "No second factor", "mfa-not-enrolled");
    await openLead();
    await user.click(revealPhone());

    await enterCode(user);

    expect(await screen.findByText(/isn't set up on your account yet/)).toBeTruthy();
    // The Security page is the dashboard's, on the dashboard's host: this app has no such route.
    expect(screen.getByRole("link", { name: /Security page/ }).getAttribute("href")).toBe(
      "https://app.opsgrids.com/account/security",
    );
    expect(screen.queryByRole("textbox", { name: /6-digit code/ })).toBeNull();
    expect(gateway.callsTo(REVEAL)).toHaveLength(0);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(revealPhone()));
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
  ];

  it.each(CASES)("$name", async ({ status, detail, slug, message }) => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(status, detail, slug);
    await openLead();

    await user.click(revealPhone());
    await enterCode(user);

    const sentence = await screen.findByText(message);
    // In a live region that was already on the page, so it is said without anyone looking for it.
    expect(sentence.closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(screen.queryByRole("dialog")).toBeNull();
    // The mask stays, the value never appeared, and the button is there to try again.
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
    await revealPhoneWithCode(user);

    await user.click(revealEmail());

    expect(await screen.findByText("No email address is on file for this lead.")).toBeTruthy();
    expect(screen.getByRole("link", { name: PHONE })).toBeTruthy();
  });

  it("clears the sentence when the person tries again", async () => {
    const user = userEvent.setup();
    let calls = 0;
    routes[REVEAL] = () => {
      calls += 1;
      return calls === 1
        ? problem(403, "Out of scope for this resource", "forbidden")
        : json({ lead_id: "lead-1", field: "phone", value: PHONE });
    };
    await openLead();
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByText(/not assigned to you/);

    await user.click(revealPhone());

    expect(await screen.findByRole("link", { name: PHONE })).toBeTruthy();
    expect(screen.queryByText(/not assigned to you/)).toBeNull();
    // The token from the first code was kept: no second prompt.
    expect(gateway.callsTo(STEP_UP)).toHaveLength(1);
  });

  it("says so, in words, when the connection drops", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => {
      throw new TypeError("Failed to fetch");
    };
    await openLead();
    await user.click(revealPhone());
    await enterCode(user);

    expect(await screen.findByText("Couldn't reveal this right now. Try again.")).toBeTruthy();
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });

  it("asks for a fresh code, instead of failing the same way, when the server wants a second factor the token lacks", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(403, "Additional authentication required: otp, stepup", "forbidden");
    await openLead();
    await user.click(revealPhone());
    await enterCode(user);
    await screen.findByText(/A one-time code is needed to reveal this/);

    routes[REVEAL] = revealAnswer;
    await user.click(revealPhone());

    // The token that was refused is gone, so this asks.
    expect(stepUpDialog()).toBeTruthy();
  });
});

describe("LeadDetail reveal: the reveal never touches the session", () => {
  it("does not sign the person out or renew the session when the step-up token is refused with a 401", async () => {
    const user = userEvent.setup();
    routes[REVEAL] = () => problem(401, "Token has expired", "unauthorized");
    routes[REFRESH] = () => json({ status: "complete", access_token: "renewed", token_type: "Bearer", expires_in: 900 });
    await openLead();

    await user.click(revealPhone());
    await enterCode(user);

    expect(await screen.findByText("That one-time code has expired. Select Reveal to enter a new one.")).toBeTruthy();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION_TOKEN);
    expect(gateway.callsTo(REFRESH)).toHaveLength(0);
    expect(gateway.callsTo(REVEAL)).toHaveLength(1);
    expect(screen.getByText("•••••• 4821")).toBeTruthy();
  });

  it("asks for a new code, without signing out, when a kept token turns out to have lapsed", async () => {
    const user = userEvent.setup();
    let revealed = 0;
    routes[REVEAL] = (call) => {
      revealed += 1;
      return revealed === 2
        ? problem(401, "Token has expired", "unauthorized")
        : revealAnswer(call);
    };
    await openLead();
    await revealPhoneWithCode(user);

    await user.click(revealEmail());

    expect(stepUpDialog()).toBeTruthy();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION_TOKEN);
    expect(gateway.callsTo(REFRESH)).toHaveLength(0);
  });
});

describe("LeadDetail reveal: while it is under way", () => {
  it("disables both Reveal buttons while the dialog is open, and while the call is out", async () => {
    const user = userEvent.setup();
    const slow = held();
    routes[REVEAL] = slow.route;
    await openLead();

    await user.click(revealPhone());
    expect(revealPhone().hasAttribute("disabled")).toBe(true);
    expect(revealEmail().hasAttribute("disabled")).toBe(true);

    await enterCode(user);
    await waitFor(() => expect(gateway.callsTo(REVEAL)).toHaveLength(1));
    // The call is out: the one asking says it is working, and the other cannot start a second one.
    expect(revealPhone().getAttribute("aria-busy")).toBe("true");
    expect(revealPhone().hasAttribute("disabled")).toBe(true);
    expect(revealEmail().hasAttribute("disabled")).toBe(true);

    await act(async () => {
      slow.respond(json({ lead_id: "lead-1", field: "phone", value: PHONE }));
    });

    expect(await screen.findByRole("link", { name: PHONE })).toBeTruthy();
    expect(revealEmail().hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "Hide phone number" }).hasAttribute("disabled")).toBe(false);
  });

  it("sends a reveal once however many times the button is pressed", async () => {
    const user = userEvent.setup();
    const slow = held();
    routes[REVEAL] = slow.route;
    await openLead();
    await user.click(revealPhone());
    await enterCode(user);
    await waitFor(() => expect(gateway.callsTo(REVEAL)).toHaveLength(1));

    await user.click(revealPhone());
    await user.click(revealPhone());

    expect(gateway.callsTo(REVEAL)).toHaveLength(1);
    expect(gateway.callsTo(STEP_UP)).toHaveLength(1);
    await act(async () => {
      slow.respond(json({ lead_id: "lead-1", field: "phone", value: PHONE }));
    });
  });
});

describe("LeadDetail reveal: keyboard and screen reader", () => {
  it("is a real button for each field, named for the field it reveals", async () => {
    await openLead();

    expect(revealPhone().tagName).toBe("BUTTON");
    expect(revealEmail().tagName).toBe("BUTTON");
    expect(revealPhone().textContent).toBe("Reveal");
  });

  it("gives each masked value a name a screen reader can tell apart", async () => {
    await openLead();

    expect(screen.getByText("Phone number:", { exact: false }).textContent).toContain("Phone number");
    expect(screen.getByText("Email address:", { exact: false }).textContent).toContain("Email address");
  });

  it("can be done from the keyboard alone: Enter on Reveal, the code, Enter on Hide", async () => {
    const user = userEvent.setup();
    await openLead();

    const reveal = revealPhone();
    reveal.focus();
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(codeField());
    await user.keyboard("123456");

    // The same element turns into Hide, and focus is on it: nothing was removed from under it.
    const hide = await screen.findByRole("button", { name: "Hide phone number" });
    expect(hide).toBe(reveal);
    await waitFor(() => expect(document.activeElement).toBe(hide));

    await user.keyboard("{Enter}");

    expect(screen.queryByRole("link", { name: PHONE })).toBeNull();
    expect(screen.getByRole("button", { name: "Reveal phone number" })).toBe(reveal);
    expect(document.activeElement).toBe(reveal);
  });

  it("returns focus to Reveal when Escape closes the dialog", async () => {
    const user = userEvent.setup();
    await openLead();

    revealPhone().focus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(revealPhone()));
  });

  it("has the live regions on the page before anything is said in them, and announces the reveal and the hide without saying the number", async () => {
    const user = userEvent.setup();
    await openLead();
    const regions = () => document.querySelectorAll('[aria-live="polite"]');
    const before = regions().length;
    expect(before).toBeGreaterThanOrEqual(2);

    await revealPhoneWithCode(user);

    expect(regions().length).toBe(before);
    const announced = screen.getByText(/Phone number revealed\./).closest("[aria-live]");
    expect(announced?.textContent).toContain("Hides again in 30 seconds.");
    expect(announced?.textContent).not.toContain("98765");

    await user.click(screen.getByRole("button", { name: "Hide phone number" }));

    expect(screen.getByText("Phone number hidden.").closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(document.querySelectorAll('[aria-live="polite"]').length).toBe(before);
  });
});
