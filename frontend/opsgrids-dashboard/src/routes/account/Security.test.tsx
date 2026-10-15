/**
 * Adding an authenticator on the Security page, against the contract of `POST /auth/mfa/enrol` and
 * `/enrol/confirm` (ADR-0023): the real `apiRequest`, the real `useMutation`, and `fetch` stubbed
 * with the problem documents the auth service produces. What is pinned is what a person sees and
 * what goes over the wire — which fields exist, which control each refusal lands on, and when the
 * recovery codes are shown — because a wrong mapping here is a wrong sentence under a wrong field.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthContext, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";
import { Toaster } from "@opsgrids/ui";

import { SecurityPage } from "./Security";

const ENROL = "/api/v1/auth/mfa/enrol";
const CONFIRM = "/api/v1/auth/mfa/enrol/confirm";
const SWITCH = "/api/v1/auth/switch-org";
const FACTOR_ID = "3f9c2a4e-7d1b-4c58-9a06-2b8e5d7f1a90";
const PASSWORD = "correct horse battery staple"; // pragma: allowlist secret

const ORG_A = { organization_id: "org-a", slug: "acme-india", name: "Acme India", roles: ["OWNER"] };
const ORG_B = { organization_id: "org-b", slug: "acme-uk", name: "Acme UK", roles: ["OWNER"] };

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown>;
}

let calls: Call[];
let routes: Record<string, (call: Call) => Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

/** A problem document the way `platform_core.errors` writes one, with its field detail when it has any. */
function problem(status: number, slug: string, detail: string, field?: string): Response {
  return json(
    {
      type: `https://errors.opsgrids.dev/${slug}`,
      title: detail,
      status,
      detail,
      ...(field !== undefined ? { errors: [{ field, code: slug.replace(/-/g, "_"), message: detail }] } : {}),
    },
    status,
  );
}

const ENROLMENT = {
  factor_id: FACTOR_ID,
  secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret — the well-known documentation TOTP seed
  otpauth_uri: "otpauth://totp/OpsGrids:priya@acme.example.com?secret=JBSWY3DPEHPK3PXP&issuer=OpsGrids",
};

const TEN_CODES = Array.from({ length: 10 }, (_, index) => `rc${String(index).padStart(2, "0")}-abcd-efgh`);

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const raw = typeof init?.body === "string" ? init.body : "{}";
      const call: Call = { url, method: init?.method ?? "GET", body: JSON.parse(raw) as Record<string, unknown> };
      calls.push(call);
      const route = routes[url];
      if (!route) return Promise.reject(new TypeError(`No stub for ${url}`));
      return Promise.resolve(route(call));
    }),
  );
}

function callsTo(url: string): Call[] {
  return calls.filter((call) => call.url === url);
}

function me(overrides: Partial<MeOut> = {}): MeOut {
  return {
    user_id: "u-1",
    email: "priya@acme.example.com",
    full_name: "Priya Nair",
    organization_id: ORG_A.organization_id,
    membership_id: "mem-1",
    roles: ["OWNER"],
    amr: ["pwd"],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [ORG_A],
    ...overrides,
  };
}

function renderPage(user: MeOut) {
  const value: AuthContextValue = {
    status: "authenticated",
    user,
    login: vi.fn(),
    verifyMfa: vi.fn(),
    selectOrg: vi.fn(),
    logout: vi.fn(),
  };
  return render(
    <Toaster>
      <AuthContext.Provider value={value}>
        <SecurityPage />
      </AuthContext.Provider>
    </Toaster>,
  );
}

type User = ReturnType<typeof userEvent.setup>;

/** The text a control is described by — its error, and its helper when it has one. */
function describedBy(control: HTMLElement): string {
  return (control.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ")
    .trim();
}

/** The alerts on the page that say something: the toaster keeps two empty assertive regions of its own. */
function pageAlerts(): HTMLElement[] {
  return screen.queryAllByRole("alert").filter((node) => (node.textContent ?? "") !== "");
}

async function findAlert(): Promise<HTMLElement> {
  return waitFor(() => {
    const [first] = pageAlerts();
    if (first === undefined) throw new Error("No alert on the page yet");
    return first;
  });
}

const passwordField = () => screen.getByLabelText("Password");
const currentCodeField = () => screen.getByLabelText("Current authenticator code");
const scanCodeField = () => screen.getByLabelText("6-digit code");

async function openStartForm(user: User, name: string) {
  await user.click(screen.getByRole("button", { name }));
  await screen.findByLabelText("Password");
}

async function submitStart(user: User) {
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

beforeEach(() => {
  calls = [];
  routes = {};
  setAccessToken("session-access-token");
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  localStorage.clear();
  sessionStorage.clear();
});

describe("Security: the account's first authenticator", () => {
  it("offers to set one up and asks for the password only", async () => {
    const user = userEvent.setup();
    renderPage(me());

    expect(screen.getByText(/Not set up yet/)).toBeTruthy();
    await openStartForm(user, "Set up two-factor authentication");

    expect(passwordField().getAttribute("type")).toBe("password");
    expect(passwordField().hasAttribute("required")).toBe(true);
    expect(screen.queryByLabelText("Current authenticator code")).toBeNull();
    expect(callsTo(ENROL)).toHaveLength(0);
  });

  it("sends the password alone, then shows the code to scan", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => json(ENROLMENT, 201);
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");

    await user.type(passwordField(), PASSWORD);
    await submitStart(user);

    await screen.findByRole("img", { name: "Two-factor authentication setup code" });
    expect(screen.getByText(ENROLMENT.secret)).toBeTruthy();
    const [call] = callsTo(ENROL);
    expect(call?.method).toBe("POST");
    expect(call?.body).toEqual({ password: PASSWORD });
  });

  it("shows the ten recovery codes once, and says so, after the first authenticator is confirmed", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => json(ENROLMENT, 201);
    routes[CONFIRM] = () => json({ recovery_codes: TEN_CODES });
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);
    await submitStart(user);

    await user.type(await screen.findByLabelText("6-digit code"), "482913");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await screen.findByRole("heading", { name: "Recovery codes" });
    expect(screen.getByText("Shown only once — save these somewhere safe.")).toBeTruthy();
    for (const code of TEN_CODES) expect(screen.getByText(code)).toBeTruthy();
    expect(callsTo(CONFIRM)[0]?.body).toEqual({ factor_id: FACTOR_ID, code: "482913" });

    await user.click(screen.getByRole("button", { name: "I've saved these codes" }));

    expect(screen.queryByText(TEN_CODES[0] ?? "")).toBeNull();
    expect(screen.getByText("Enabled for your account.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add another authenticator" })).toBeTruthy();
  });

  it("says an empty password is empty, and sends nothing", async () => {
    const user = userEvent.setup();
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");

    await submitStart(user);

    expect(describedBy(passwordField())).toBe("Enter your password.");
    expect(passwordField().getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(passwordField());
    expect(calls).toHaveLength(0);
  });

  it("puts a wrong password under the password field, empties it, and does not raise a banner", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => problem(422, "password-incorrect", "That password is not correct.", "password");
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), "wrong-guess");

    await submitStart(user);

    await waitFor(() => expect(describedBy(passwordField())).toBe("That password is not correct."));
    expect(passwordField().getAttribute("aria-invalid")).toBe("true");
    expect(passwordField()).toHaveProperty("value", "");
    expect(document.activeElement).toBe(passwordField());
    expect(pageAlerts()).toHaveLength(0);
    expect(screen.queryByLabelText("Current authenticator code")).toBeNull();
  });

  it("clears the error as soon as the password is edited", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => problem(422, "password-incorrect", "That password is not correct.", "password");
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), "wrong-guess");
    await submitStart(user);
    await waitFor(() => expect(describedBy(passwordField())).not.toBe(""));

    await user.type(passwordField(), "x");

    expect(describedBy(passwordField())).toBe("");
    expect(passwordField().getAttribute("aria-invalid")).toBeNull();
  });

  it("shows the code field when the server says the account has an authenticator after all", async () => {
    const user = userEvent.setup();
    routes[ENROL] = (call) =>
      call.body.current_code === undefined
        ? problem(422, "mfa-current-code-required", "A code from your current authenticator is required.", "current_code")
        : json(ENROLMENT, 201);
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);

    await submitStart(user);

    const field = await screen.findByLabelText("Current authenticator code");
    expect(describedBy(field)).toContain("A code from your current authenticator is required.");
    expect(document.activeElement).toBe(field);
    expect(pageAlerts()).toHaveLength(0);

    await user.type(field, "482913");
    await submitStart(user);

    await screen.findByRole("img", { name: "Two-factor authentication setup code" });
    expect(callsTo(ENROL)[1]?.body).toEqual({ password: PASSWORD, current_code: "482913" });
  });

  it("says a lockout above the button, not under a field, and offers no way around it", async () => {
    const user = userEvent.setup();
    const detail = "This account is temporarily locked after several failed authentication attempts. Try again shortly.";
    routes[ENROL] = () => problem(403, "password-locked", detail);
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);

    await submitStart(user);

    const alert = await findAlert();
    expect(alert.textContent).toContain(detail);
    expect(passwordField().getAttribute("aria-invalid")).toBeNull();
    expect(callsTo(ENROL)).toHaveLength(1);
  });

  it("says an upgrade in progress is not the person's doing", async () => {
    const user = userEvent.setup();
    const detail = "Two-factor authentication is being upgraded for your account. Try again shortly.";
    routes[ENROL] = () => problem(503, "mfa-reseal-pending", detail);
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);

    await submitStart(user);

    expect((await findAlert()).textContent).toContain(detail);
    expect(passwordField().getAttribute("aria-invalid")).toBeNull();
  });

  it("goes back to where things stand on Cancel, without sending anything", async () => {
    const user = userEvent.setup();
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Set up two-factor authentication" })).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(calls).toHaveLength(0);
  });
});

describe("Security: confirming the code from the new authenticator", () => {
  async function toScanStep(user: User) {
    routes[ENROL] = () => json(ENROLMENT, 201);
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);
    await submitStart(user);
    await screen.findByLabelText("6-digit code");
  }

  it("puts a wrong code under the code field and empties it, without a banner", async () => {
    const user = userEvent.setup();
    routes[CONFIRM] = () => problem(422, "mfa-code-invalid", "That code is not valid. Check your device's clock.", "code");
    await toScanStep(user);

    await user.type(scanCodeField(), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(describedBy(scanCodeField())).toBe("That code is not valid. Check your device's clock."));
    expect(scanCodeField()).toHaveProperty("value", "");
    expect(pageAlerts()).toHaveLength(0);
    // Still the same enrolment: the person keeps their QR code and can try the next code.
    expect(screen.getByText(ENROLMENT.secret)).toBeTruthy();
  });

  it("says a setup begun in another session cannot be finished here, and starts again on request", async () => {
    const user = userEvent.setup();
    routes[CONFIRM] = () => problem(404, "not-found", "No such enrolment");
    await toScanStep(user);

    await user.type(scanCodeField(), "482913");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await screen.findByText("This setup can't be finished from here.");
    expect(screen.queryByLabelText("6-digit code")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Start again" }));

    // Back at the password, which the new setup needs again.
    expect(await screen.findByLabelText("Password")).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Two-factor authentication setup code" })).toBeNull();
  });

  it("gives the server's sentence when the factor is already confirmed", async () => {
    const user = userEvent.setup();
    routes[CONFIRM] = () => problem(401, "unauthorized", "This factor is already confirmed.");
    // The shared client tries one silent refresh on a 401 before it gives up; there is none here.
    routes["/api/v1/auth/refresh"] = () => problem(401, "unauthorized", "No session");
    await toScanStep(user);

    await user.type(scanCodeField(), "482913");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await findAlert()).textContent).toContain("This factor is already confirmed.");
  });

  it("goes back to where things stand on Cancel", async () => {
    const user = userEvent.setup();
    await toScanStep(user);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Set up two-factor authentication" })).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Two-factor authentication setup code" })).toBeNull();
  });
});

describe("Security: adding another authenticator", () => {
  const WITH_ONE = () => me({ mfa_enabled: true });

  it("says it is on, and offers to add another rather than to set it up", () => {
    renderPage(WITH_ONE());

    expect(screen.getByText("Enabled for your account.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add another authenticator" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Set up two-factor authentication" })).toBeNull();
  });

  it("asks for the password and a code from the current authenticator, both required", async () => {
    const user = userEvent.setup();
    renderPage(WITH_ONE());

    await openStartForm(user, "Add another authenticator");

    expect(passwordField().hasAttribute("required")).toBe(true);
    expect(currentCodeField().hasAttribute("required")).toBe(true);
    expect(describedBy(currentCodeField())).toBe(
      "A 6-digit code from an authenticator you already use, or one of your recovery codes.",
    );
  });

  it("will not send until the current code is there, and says which field is missing", async () => {
    const user = userEvent.setup();
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");
    await user.type(passwordField(), PASSWORD);

    await submitStart(user);

    expect(describedBy(currentCodeField())).toContain("Enter a code from your current authenticator.");
    expect(document.activeElement).toBe(currentCodeField());
    expect(calls).toHaveLength(0);
  });

  it("sends the password and the current code, with the spaces a phone shows taken out", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => json(ENROLMENT, 201);
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");

    await user.type(passwordField(), PASSWORD);
    await user.type(currentCodeField(), "482 913");
    await submitStart(user);

    await screen.findByRole("img", { name: "Two-factor authentication setup code" });
    expect(callsTo(ENROL)[0]?.body).toEqual({ password: PASSWORD, current_code: "482913" });
  });

  it("accepts a recovery code in the same field", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => json(ENROLMENT, 201);
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");

    await user.type(passwordField(), PASSWORD);
    await user.type(currentCodeField(), "rc00-abcd-efgh");
    await submitStart(user);

    await screen.findByRole("img", { name: "Two-factor authentication setup code" });
    expect(callsTo(ENROL)[0]?.body).toEqual({ password: PASSWORD, current_code: "rc00-abcd-efgh" });
  });

  it("puts a wrong or already-used current code under its own field, and empties it", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    routes[ENROL] = () => {
      attempt += 1;
      return attempt === 1
        ? problem(422, "mfa-code-invalid", "That code is not valid.", "current_code")
        : problem(422, "mfa-code-replayed", "That code has already been used. Wait for the next one.", "current_code");
    };
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");
    await user.type(passwordField(), PASSWORD);
    await user.type(currentCodeField(), "000000");

    await submitStart(user);

    await waitFor(() => expect(describedBy(currentCodeField())).toContain("That code is not valid."));
    expect(currentCodeField()).toHaveProperty("value", "");
    expect(document.activeElement).toBe(currentCodeField());
    // The password was right and is still there; the message is not under it.
    expect(passwordField()).toHaveProperty("value", PASSWORD);
    expect(describedBy(passwordField())).toBe("");
    expect(pageAlerts()).toHaveLength(0);

    await user.type(currentCodeField(), "482913");
    await submitStart(user);

    await waitFor(() => expect(describedBy(currentCodeField())).toContain("That code has already been used."));
    expect(currentCodeField()).toHaveProperty("value", "");
  });

  it("puts a wrong password under the password field even when a code was sent", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => problem(422, "password-incorrect", "That password is not correct.", "password");
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");
    await user.type(passwordField(), "wrong-guess");
    await user.type(currentCodeField(), "482913");

    await submitStart(user);

    await waitFor(() => expect(describedBy(passwordField())).toBe("That password is not correct."));
    expect(passwordField()).toHaveProperty("value", "");
    // The code was not spent on it: the password is checked first, and the code is still typed.
    expect(currentCodeField()).toHaveProperty("value", "482913");
  });

  it("says a locked second factor above the button", async () => {
    const user = userEvent.setup();
    const detail = "This account is temporarily locked after several failed authentication attempts. Try again shortly.";
    routes[ENROL] = () => problem(403, "mfa-locked", detail);
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");
    await user.type(passwordField(), PASSWORD);
    await user.type(currentCodeField(), "482913");

    await submitStart(user);

    expect((await findAlert()).textContent).toContain(detail);
    expect(currentCodeField().getAttribute("aria-invalid")).toBeNull();
  });

  it("shows no recovery-code panel for an authenticator that was added, only that it was", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () => json(ENROLMENT, 201);
    routes[CONFIRM] = () => json({ recovery_codes: [] });
    renderPage(WITH_ONE());
    await openStartForm(user, "Add another authenticator");
    await user.type(passwordField(), PASSWORD);
    await user.type(currentCodeField(), "482913");
    await submitStart(user);

    await user.type(await screen.findByLabelText("6-digit code"), "739105");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await screen.findByText("Authenticator added.");
    expect(screen.queryByRole("heading", { name: "Recovery codes" })).toBeNull();
    expect(screen.queryByText(/Shown only once/)).toBeNull();
    expect(screen.queryByRole("button", { name: "I've saved these codes" })).toBeNull();
    expect(screen.getByText("Enabled for your account.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add another authenticator" })).toBeTruthy();
  });
});

describe("Security: a single sign-on session", () => {
  it("says an authenticator cannot be added, plainly, and offers no form", () => {
    renderPage(me({ amr: ["sso"] }));

    expect(screen.getByText(/Not available with single sign-on\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Set up two-factor authentication" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add another authenticator" })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("still says an authenticator is on the account, and that another cannot be added here", () => {
    renderPage(me({ amr: ["sso"], mfa_enabled: true }));

    expect(screen.getByText("Enabled for your account.")).toBeTruthy();
    expect(screen.getByText(/Adding another authenticator isn't available with single sign-on/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add another authenticator" })).toBeNull();
  });

  it("does not mistake a password session that also used a code for single sign-on", () => {
    renderPage(me({ amr: ["pwd", "otp"], mfa_enabled: true }));

    expect(screen.getByRole("button", { name: "Add another authenticator" })).toBeTruthy();
  });

  it("switches to the same statement when the server is the one to say so", async () => {
    const user = userEvent.setup();
    routes[ENROL] = () =>
      problem(403, "mfa-unavailable-for-sso", "Two-factor authentication is not available for single sign-on sessions.");
    renderPage(me());
    await openStartForm(user, "Set up two-factor authentication");
    await user.type(passwordField(), PASSWORD);

    await submitStart(user);

    await screen.findByText(/Not available with single sign-on\./);
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Set up two-factor authentication" })).toBeNull();
    expect(pageAlerts()).toHaveLength(0);
  });
});

describe("Security: switching organisation", () => {
  it("says the list is out of date when the token is already for that organisation", async () => {
    const user = userEvent.setup();
    routes[SWITCH] = () => problem(409, "already-in-organization", "You are already in that organization.");
    renderPage(me({ organizations: [ORG_A, ORG_B] }));

    await user.click(screen.getByRole("button", { name: "Switch" }));

    await screen.findByText("You're already in that organisation.");
    expect(screen.getByText(/This list is out of date/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
    expect(callsTo(SWITCH)[0]?.body).toEqual({ organization_id: ORG_B.organization_id });
  });

  it("gives any other refusal in the server's own words", async () => {
    const user = userEvent.setup();
    routes[SWITCH] = () => problem(403, "forbidden", "You no longer have access to that organization.");
    renderPage(me({ organizations: [ORG_A, ORG_B] }));

    await user.click(screen.getByRole("button", { name: "Switch" }));

    expect((await findAlert()).textContent).toContain("You no longer have access to that organization.");
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
  });
});

describe("Security: changing the password", () => {
  const CHANGE = "/api/v1/auth/password/change";
  const NEW = "a-brand-new-pw-9"; // pragma: allowlist secret
  const currentField = () => screen.getByLabelText("Current password");
  const newField = () => screen.getByLabelText("New password");
  const confirmField = () => screen.getByLabelText("Confirm new password");

  async function fill(user: User, current: string, next: string, confirm = next) {
    await user.type(currentField(), current);
    await user.type(newField(), next);
    await user.type(confirmField(), confirm);
  }

  const save = () => screen.getByRole("button", { name: "Change password" });

  it("sends both passwords, says it was changed, and empties the form", async () => {
    const user = userEvent.setup();
    routes[CHANGE] = () => new Response(null, { status: 204 });
    renderPage(me());

    await fill(user, PASSWORD, NEW);
    await user.click(save());

    expect(await screen.findByText("Password changed.")).toBeTruthy();
    expect(callsTo(CHANGE)[0]?.body).toEqual({ current_password: PASSWORD, new_password: NEW });
    expect(currentField()).toHaveProperty("value", "");
    expect(newField()).toHaveProperty("value", "");
  });

  it("refuses a new password shorter than the server minimum without calling the server", async () => {
    const user = userEvent.setup();
    routes[CHANGE] = () => new Response(null, { status: 204 });
    renderPage(me());
    expect(screen.getByText("At least 8 characters.")).toBeTruthy();
    await fill(user, PASSWORD, "short-7");

    await user.click(save());

    expect(describedBy(newField())).toContain("Use at least 8 characters.");
    expect(callsTo(CHANGE)).toHaveLength(0);
  });

  it("puts a wrong current password under its own field, empties it and focuses it, without signing anybody out", async () => {
    const user = userEvent.setup();
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    routes[CHANGE] = () =>
      problem(422, "password-incorrect", "That is not your current password.", "current_password");
    renderPage(me());
    await fill(user, "wrong-guess", NEW);

    await user.click(save());

    await waitFor(() => expect(describedBy(currentField())).toBe("That is not your current password."));
    expect(currentField().getAttribute("aria-invalid")).toBe("true");
    expect(currentField()).toHaveProperty("value", "");
    expect(document.activeElement).toBe(currentField());
    // The new password was fine and is still there; nothing is said about it.
    expect(newField()).toHaveProperty("value", NEW);
    expect(describedBy(newField())).not.toContain("current");
    expect(pageAlerts()).toHaveLength(0);
    // A 422, so the shared client neither refreshed nor re-sent it, and the session is intact.
    expect(callsTo(CHANGE)).toHaveLength(1);
    expect(callsTo("/api/v1/auth/refresh")).toHaveLength(0);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("clears the message as soon as the field is edited", async () => {
    const user = userEvent.setup();
    routes[CHANGE] = () =>
      problem(422, "password-incorrect", "That is not your current password.", "current_password");
    renderPage(me());
    await fill(user, "wrong-guess", NEW);
    await user.click(save());
    await waitFor(() => expect(describedBy(currentField())).not.toBe(""));

    await user.type(currentField(), "x");

    expect(describedBy(currentField())).toBe("");
    expect(currentField().getAttribute("aria-invalid")).toBeNull();
  });

  it("puts a new password that is the same as the current one under the new password field", async () => {
    const user = userEvent.setup();
    routes[CHANGE] = () =>
      problem(422, "business-rule", "The new password must be different from the current one.", "new_password");
    renderPage(me());
    await fill(user, NEW, NEW);

    await user.click(save());

    await waitFor(() =>
      expect(describedBy(newField())).toContain("The new password must be different from the current one."),
    );
    expect(newField().getAttribute("aria-invalid")).toBe("true");
    expect(currentField().getAttribute("aria-invalid")).toBeNull();
    // Nothing was wrong with the current password, so it is left where it is.
    expect(currentField()).toHaveProperty("value", NEW);
    expect(pageAlerts()).toHaveLength(0);
  });

  it("puts a weak new password under the new password field", async () => {
    const user = userEvent.setup();
    routes[CHANGE] = () => problem(422, "validation", "Mix letters with numbers or symbols", "new_password");
    renderPage(me());
    await fill(user, PASSWORD, "abcdefghijkl");

    await user.click(save());

    await waitFor(() => expect(describedBy(newField())).toContain("Mix letters with numbers or symbols"));
    expect(currentField()).toHaveProperty("value", PASSWORD);
    expect(pageAlerts()).toHaveLength(0);
  });

  it("says a lockout above the button, in the server's words, and marks no field", async () => {
    const user = userEvent.setup();
    const detail =
      "This account is temporarily locked after several failed attempts. Try again shortly, or reset the password.";
    routes[CHANGE] = () => problem(403, "password-locked", detail);
    renderPage(me());
    await fill(user, PASSWORD, NEW);

    await user.click(save());

    expect((await findAlert()).textContent).toContain(detail);
    expect(currentField().getAttribute("aria-invalid")).toBeNull();
    expect(newField().getAttribute("aria-invalid")).toBeNull();
    expect(callsTo(CHANGE)).toHaveLength(1);
  });

  it("says a confirmation that does not match before anything is sent", async () => {
    const user = userEvent.setup();
    renderPage(me());
    await fill(user, PASSWORD, NEW, "something-else-9");

    await user.click(save());

    expect(describedBy(confirmField())).toBe("Doesn't match the new password.");
    expect(callsTo(CHANGE)).toHaveLength(0);
  });
});
