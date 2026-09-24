/**
 * The operator sign-in, as a person meets it: a password, then a second factor that is never
 * optional — a code for an account that has an authenticator, enrolment for one that has none.
 *
 * What is pinned is each place it can go wrong and what the screen says there. A wrong code stays
 * on the step and keeps the field; a lockout goes back to the start with the server's sentence; and
 * the 503 the server sends when it cannot yet read a factor is said plainly as *its* problem, with
 * the way through (a recovery code). Everything that can be typed in survives a failure, nothing
 * submits twice, and each step puts focus where the next move is.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { OperatorAuthProvider } from "@/auth/OperatorAuthProvider";
import { json, operatorToken, problem, stubFetch } from "@/testing";
import type { Handler } from "@/testing";
import { LoginPage } from "./Login";

const PRE_AUTH = "pre-auth-token-value";
const EMAIL = "owner@opsgrids.local";
const PASSWORD = "a-long-enough-password"; // pragma: allowlist secret

const ME = {
  staff_id: "8f21a0c4-5b1e-4d3a-9c77-2e6a4b0f1d10",
  email: EMAIL,
  display_name: "OpsGrids Owner (dev)",
  operator_roles: ["owner"],
  amr: ["pwd", "otp"],
};

const LOCKED =
  "This operator account is temporarily locked after several failed attempts. Try again shortly.";

function Destination() {
  const location = useLocation();
  return <p>{`Arrived at ${location.pathname}${location.search}`}</p>;
}

function renderLogin(from?: string) {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[{ pathname: "/login", ...(from ? { state: { from } } : {}) }]}>
      <OperatorAuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Destination />} />
        </Routes>
      </OperatorAuthProvider>
    </MemoryRouter>,
  );
  return user;
}

const complete = (token: string) => ({
  status: "complete",
  access_token: token,
  token_type: "Bearer",
  expires_in: 900,
  operator_roles: ["owner"],
});

/** Answers the routes every sign-in touches, with the second-factor step under test on top. */
function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
    "GET /auth/operator/me": () => json(ME),
    ...extra,
  });
}

async function signInWithPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Operator email"), EMAIL);
  await user.type(screen.getByLabelText("Password"), PASSWORD);
  await user.click(screen.getByRole("button", { name: "Sign in to console" }));
}

describe("the password step", () => {
  it("is one page with one h1, two labelled fields and one way forward", async () => {
    backend();
    renderLogin();
    expect(await screen.findByRole("heading", { level: 1, name: "Operator sign-in" })).toBeDefined();
    expect(screen.getByLabelText("Operator email")).toBeDefined();
    expect(screen.getByLabelText("Password")).toBeDefined();
    expect(screen.getByRole("button", { name: "Sign in to console" })).toBeDefined();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByText("OpsGrids staff console · access is logged and audited")).toBeDefined();
  });

  it("puts focus in the first field, so a keyboard user starts typing", async () => {
    backend();
    renderLogin();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Operator email")));
  });

  it("says which field is empty, on that field, and sends nothing", async () => {
    const { calls } = backend();
    const user = renderLogin();

    await user.click(await screen.findByRole("button", { name: "Sign in to console" }));
    expect(screen.getByText("Enter your operator email.")).toBeDefined();
    expect(document.activeElement).toBe(screen.getByLabelText("Operator email"));

    await user.type(screen.getByLabelText("Operator email"), EMAIL);
    await user.click(screen.getByRole("button", { name: "Sign in to console" }));
    expect(screen.getByText("Enter your password.")).toBeDefined();
    expect(document.activeElement).toBe(screen.getByLabelText("Password"));

    expect(calls.filter((c) => c.path === "/auth/operator/login")).toHaveLength(0);
  });

  it("reveals the password on request, and hides it again", async () => {
    backend();
    const user = renderLogin();
    const field = await screen.findByLabelText("Password");
    expect(field.getAttribute("type")).toBe("password");
    await user.click(screen.getByRole("switch", { name: "Show password" }));
    expect(field.getAttribute("type")).toBe("text");
    await user.click(screen.getByRole("switch", { name: "Show password" }));
    expect(field.getAttribute("type")).toBe("password");
  });

  it("says a wrong email or password is wrong, without saying which half, and keeps what was typed", async () => {
    backend({
      "POST /auth/operator/login": () => problem(401, "Those credentials are not valid", { type: "unauthorized" }),
    });
    const user = renderLogin();

    await signInWithPassword(user);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That email or password is incorrect.");
    expect((screen.getByLabelText("Operator email") as HTMLInputElement).value).toBe(EMAIL);
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe(PASSWORD);
    expect(screen.getByRole("button", { name: "Sign in to console" }).hasAttribute("disabled")).toBe(false);
  });

  it("lands the server's field detail on the field it names", async () => {
    backend({
      "POST /auth/operator/login": () =>
        problem(422, "Validation failed", {
          type: "validation",
          errors: [
            { field: "email", code: "value_error", message: "value is not a valid email address" },
            { field: "password", code: "string_too_short", message: "String should have at least 8 characters" },
          ],
        }),
    });
    const user = renderLogin();

    await signInWithPassword(user);

    expect(await screen.findByText("value is not a valid email address")).toBeDefined();
    expect(screen.getByText("String should have at least 8 characters")).toBeDefined();
    // Field-level detail is on the fields; there is no banner repeating it.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the server's own sentence for a refusal that is not a wrong password — a rate limit is not one", async () => {
    backend({
      "POST /auth/operator/login": () => problem(429, "Too many attempts. Try again in a minute.", { type: "rate-limited" }),
    });
    const user = renderLogin();

    await signInWithPassword(user);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Too many attempts. Try again in a minute.");
    expect(alert.textContent).not.toContain("incorrect");
  });

  it("says a real fault is one, with its reference, and offers the same form to try again", async () => {
    backend({
      "POST /auth/operator/login": () =>
        problem(500, "auth-service could not reach its database", { type: "internal", request_id: "7f3c1a2e" }),
    });
    const user = renderLogin();

    await signInWithPassword(user);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't sign in. auth-service could not reach its database");
    expect(alert.textContent).toContain("Reference: 7f3c1a2e");
  });

  it("says a dropped connection is a failure to sign in, not bad credentials", async () => {
    stubFetch({});
    const user = renderLogin();

    await signInWithPassword(user);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't sign in.");
    expect(alert.textContent).not.toContain("incorrect");
  });

  it("submits once however many times the button is pressed, and says it is working", async () => {
    let release: (response: Response) => void = () => undefined;
    const { calls } = backend({
      "POST /auth/operator/login": () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    const user = renderLogin();
    await user.type(await screen.findByLabelText("Operator email"), EMAIL);
    await user.type(screen.getByLabelText("Password"), PASSWORD);

    await user.click(screen.getByRole("button", { name: "Sign in to console" }));
    const working = await screen.findByRole("button", { name: "Signing in…" });
    expect(working.hasAttribute("disabled")).toBe(true);
    expect(working.getAttribute("aria-busy")).toBe("true");
    await user.click(working);
    await user.keyboard("{Enter}");

    release(json({ status: "mfa_required", pre_auth_token: PRE_AUTH }));
    await screen.findByRole("heading", { name: "Enter your code" });
    expect(calls.filter((c) => c.path === "/auth/operator/login")).toHaveLength(1);
  });
});

describe("the second factor", () => {
  it("asks for the code, focused, and says where it can come from", async () => {
    backend();
    const user = renderLogin();
    await signInWithPassword(user);

    expect(await screen.findByRole("heading", { level: 1, name: "Enter your code" })).toBeDefined();
    expect(screen.getByText("From your authenticator app, or one of your recovery codes.")).toBeDefined();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Code")));
  });

  it("signs in with a code and goes where the operator was headed, query and all", async () => {
    const { calls } = backend({ "POST /auth/operator/mfa/verify": () => json(complete(operatorToken())) });
    const user = renderLogin("/accounts?q=acme");
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Arrived at /accounts?q=acme")).toBeDefined();
    const verify = calls.find((c) => c.path === "/auth/operator/mfa/verify");
    expect(verify?.body).toEqual({ pre_auth_token: PRE_AUTH, code: "123456" });
  });

  it("says a wrong code is wrong beside the field, in the server's words, keeps it, and lets the next one through", async () => {
    let attempts = 0;
    backend({
      "POST /auth/operator/mfa/verify": () => {
        attempts += 1;
        return attempts === 1
          ? problem(401, "That code is not valid", { type: "unauthorized" })
          : json(complete(operatorToken()));
      },
    });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    const field = screen.getByLabelText("Code") as HTMLInputElement;
    expect(await screen.findByText("That code is not valid")).toBeDefined();
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(field.value).toBe("000000");
    // Still the same step, and the field is where focus goes for the next attempt.
    expect(screen.getByRole("heading", { name: "Enter your code" })).toBeDefined();
    await waitFor(() => expect(document.activeElement).toBe(field));

    await user.clear(field);
    await user.type(field, "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Arrived at /")).toBeDefined();
  });

  it("says an empty code is empty, without asking the server", async () => {
    const { calls } = backend();
    const user = renderLogin();
    await signInWithPassword(user);

    await user.click(await screen.findByRole("button", { name: "Verify" }));

    expect(screen.getByText("Enter the code from your authenticator app, or a recovery code.")).toBeDefined();
    expect(calls.some((c) => c.path === "/auth/operator/mfa/verify")).toBe(false);
  });

  it("takes a recovery code in the same field, letters and hyphens and all", async () => {
    const { calls } = backend({ "POST /auth/operator/mfa/verify": () => json(complete(operatorToken())) });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Code"), "abcd-efgh-ijkl");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByText("Arrived at /");
    expect(calls.find((c) => c.path === "/auth/operator/mfa/verify")?.body?.code).toBe("abcd-efgh-ijkl");
  });

  it("goes back to the start on a lockout, with the server's sentence, because the session behind it is gone", async () => {
    backend({
      "POST /auth/operator/mfa/verify": () => problem(403, LOCKED, { type: "forbidden" }),
    });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Operator sign-in" })).toBeDefined();
    expect((await screen.findByRole("alert")).textContent).toContain(LOCKED);
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  describe("when the platform cannot yet read the authenticator (503, mfa-reseal-pending)", () => {
    const RESEAL = "Multi-factor re-seal pending — use a recovery code to sign in.";

    function resealThenAccept() {
      let attempts = 0;
      return backend({
        "POST /auth/operator/mfa/verify": () => {
          attempts += 1;
          return attempts === 1
            ? problem(503, RESEAL, {
                type: "mfa-reseal-pending",
                title: "Multi-factor re-seal pending",
                request_id: "reseal-42",
              })
            : json(complete(operatorToken()));
        },
      });
    }

    it("says so plainly — it is on our side, it did not count, and a recovery code works — and does not call the code wrong", async () => {
      resealThenAccept();
      const user = renderLogin();
      await signInWithPassword(user);

      await user.type(await screen.findByLabelText("Code"), "123456");
      await user.click(screen.getByRole("button", { name: "Verify" }));

      const alert = await screen.findByRole("status");
      expect(alert.textContent).toContain(RESEAL);
      expect(alert.textContent).toContain("on our side, not your code");
      expect(alert.textContent).toContain("hasn't counted against you");
      expect(alert.textContent).toContain("recovery code");
      expect(alert.textContent).toContain("Reference: reseal-42");
      // It is not a wrong-code error: nothing under the field, and the field is not marked invalid.
      expect(screen.queryByText("That code is not valid")).toBeNull();
      expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBeNull();
    });

    it("keeps the field and the sign-in, so a recovery code goes through the same form", async () => {
      resealThenAccept();
      const user = renderLogin();
      await signInWithPassword(user);

      await user.type(await screen.findByLabelText("Code"), "123456");
      await user.click(screen.getByRole("button", { name: "Verify" }));
      await screen.findByRole("status");
      expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("123456");

      const field = screen.getByLabelText("Code");
      await user.clear(field);
      await user.type(field, "abcd-efgh-ijkl");
      await user.click(screen.getByRole("button", { name: "Verify" }));

      expect(await screen.findByText("Arrived at /")).toBeDefined();
    });
  });

  it("says a failure to check is not a wrong code", async () => {
    backend({
      "POST /auth/operator/mfa/verify": () => problem(500, "auth-service is restarting", { type: "internal" }),
    });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't check the code. auth-service is restarting");
    expect(screen.queryByText("That code is not valid")).toBeNull();
  });

  it("goes back to the password step on request, and asks again from the start", async () => {
    const { calls } = backend();
    const user = renderLogin();
    await signInWithPassword(user);

    await user.click(await screen.findByRole("button", { name: "Back to sign in" }));

    expect(await screen.findByRole("heading", { name: "Operator sign-in" })).toBeDefined();
    // The half-finished sign-in is gone: there is no code step to reach without a password.
    expect(screen.queryByLabelText("Code")).toBeNull();
    expect(calls.filter((c) => c.path === "/auth/operator/login")).toHaveLength(1);
  });

  it("submits once however many times it is pressed", async () => {
    let release: (response: Response) => void = () => undefined;
    const { calls } = backend({
      "POST /auth/operator/mfa/verify": () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    const user = renderLogin();
    await signInWithPassword(user);
    await user.type(await screen.findByLabelText("Code"), "123456");

    await user.click(screen.getByRole("button", { name: "Verify" }));
    const working = await screen.findByRole("button", { name: "Verifying…" });
    await user.click(working);
    await user.keyboard("{Enter}");
    release(json(complete(operatorToken())));

    await screen.findByText("Arrived at /");
    expect(calls.filter((c) => c.path === "/auth/operator/mfa/verify")).toHaveLength(1);
  });
});

describe("enrolment, for an account with no authenticator yet", () => {
  const SECRET = "JBSWY3DPEHPK3PXP"; // pragma: allowlist secret
  const RECOVERY = ["k3f9-2mxq-7d1a", "p8w4-zr6c-n0vb", "h2jt-5qle-y9us"];

  function enrolBackend(extra: Record<string, Handler> = {}) {
    return backend({
      "POST /auth/operator/login": () => json({ status: "mfa_enrollment_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/enrol": () =>
        json({ factor_id: "factor-1", secret: SECRET, otpauth_uri: `otpauth://totp/OpsGrids:${EMAIL}?secret=${SECRET}` }),
      "POST /auth/operator/mfa/enrol/confirm": () =>
        json({ ...complete(operatorToken()), recovery_codes: RECOVERY }),
      ...extra,
    });
  }

  async function toScanStep(user: ReturnType<typeof userEvent.setup>) {
    await signInWithPassword(user);
    await user.type(await screen.findByLabelText("Enrolment code"), "abcd-efgh-ijkl");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Scan this with your authenticator" });
  }

  it("asks for the out-of-band code an owner issued, and warns that a typo spends it", async () => {
    enrolBackend();
    const user = renderLogin();
    await signInWithPassword(user);

    expect(await screen.findByRole("heading", { level: 1, name: "Set up your authenticator" })).toBeDefined();
    const field = screen.getByLabelText("Enrolment code");
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(screen.getByText(/works once/)).toBeDefined();
    expect(screen.getByText(/ask an owner for a new one/)).toBeDefined();
  });

  it("says a rejected enrolment code is rejected, beside the field, clears the spent value and stays on the step", async () => {
    enrolBackend({
      "POST /auth/operator/mfa/enrol": () =>
        problem(401, "That enrolment code is not valid or has expired.", { type: "unauthorized" }),
    });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Enrolment code"), "wrong-code-1");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("That enrolment code is not valid or has expired.")).toBeDefined();
    expect((screen.getByLabelText("Enrolment code") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("heading", { name: "Set up your authenticator" })).toBeDefined();
  });

  it("says an empty enrolment code is empty, without asking the server", async () => {
    const { calls } = enrolBackend();
    const user = renderLogin();
    await signInWithPassword(user);

    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(screen.getByText("Enter the enrolment code an owner gave you.")).toBeDefined();
    expect(calls.some((c) => c.path === "/auth/operator/mfa/enrol")).toBe(false);
  });

  it("goes back to the start on a lockout during enrolment", async () => {
    enrolBackend({
      "POST /auth/operator/mfa/enrol": () => problem(403, LOCKED, { type: "forbidden" }),
    });
    const user = renderLogin();
    await signInWithPassword(user);

    await user.type(await screen.findByLabelText("Enrolment code"), "abcd-efgh-ijkl");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Operator sign-in" })).toBeDefined();
    expect((await screen.findByRole("alert")).textContent).toContain(LOCKED);
  });

  it("shows a scannable code and the key to type by hand, and asks for the first code", async () => {
    enrolBackend();
    const user = renderLogin();
    await toScanStep(user);

    expect(screen.getByRole("img", { name: "Authenticator setup code" })).toBeDefined();
    expect(screen.getByText(SECRET)).toBeDefined();
    expect(screen.getByText("Or enter this key")).toBeDefined();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("6-digit code")));
  });

  it("says a wrong first code is wrong beside the field, keeps it, and lets the operator try again", async () => {
    let attempts = 0;
    enrolBackend({
      "POST /auth/operator/mfa/enrol/confirm": () => {
        attempts += 1;
        return attempts === 1
          ? problem(401, "That code is not valid. Check your device's clock.", { type: "unauthorized" })
          : json({ ...complete(operatorToken()), recovery_codes: RECOVERY });
      },
    });
    const user = renderLogin();
    await toScanStep(user);

    await user.type(screen.getByLabelText("6-digit code"), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));

    expect(await screen.findByText("That code is not valid. Check your device's clock.")).toBeDefined();
    expect((screen.getByLabelText("6-digit code") as HTMLInputElement).value).toBe("000000");

    await user.clear(screen.getByLabelText("6-digit code"));
    await user.type(screen.getByLabelText("6-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeDefined();
  });

  it("goes back to the start on a lockout while confirming", async () => {
    enrolBackend({
      "POST /auth/operator/mfa/enrol/confirm": () => problem(403, LOCKED, { type: "forbidden" }),
    });
    const user = renderLogin();
    await toScanStep(user);

    await user.type(screen.getByLabelText("6-digit code"), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));

    expect(await screen.findByRole("heading", { name: "Operator sign-in" })).toBeDefined();
    expect((await screen.findByRole("alert")).textContent).toContain(LOCKED);
  });

  it("shows the recovery codes once, holds the operator on them until they say they are saved, then goes in", async () => {
    enrolBackend();
    const user = renderLogin("/plans");
    await toScanStep(user);

    await user.type(screen.getByLabelText("6-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));

    const heading = await screen.findByRole("heading", { level: 1, name: "Save your recovery codes" });
    // Focus lands on what says what this is; the codes are plain, selectable text.
    await waitFor(() => expect(document.activeElement).toBe(heading));
    const list = screen.getByRole("list");
    for (const code of RECOVERY) expect(within(list).getByText(code)).toBeDefined();
    expect(screen.getByText("Shown only once — save these somewhere safe.")).toBeDefined();

    // The session exists, but the operator is not let through until they have said they saved the codes.
    const proceed = screen.getByRole("button", { name: "Continue to the console" });
    expect(proceed.hasAttribute("disabled")).toBe(true);
    expect(screen.queryByText(/Arrived at/)).toBeNull();

    await user.click(screen.getByRole("switch", { name: "I've saved these codes" }));
    expect(proceed.hasAttribute("disabled")).toBe(false);
    await user.click(proceed);

    expect(await screen.findByText("Arrived at /plans")).toBeDefined();
    // Gone from the page the moment they are through — nothing keeps the codes on screen.
    expect(screen.queryByText(RECOVERY[0] ?? "")).toBeNull();
  });

  it("lets the operator try again if the console cannot load who they are after they saved the codes", async () => {
    let meCalls = 0;
    enrolBackend({
      "GET /auth/operator/me": () => {
        meCalls += 1;
        return meCalls === 1 ? problem(503, "auth-service is restarting", { type: "upstream-unavailable" }) : json(ME);
      },
    });
    const user = renderLogin();
    await toScanStep(user);
    await user.type(screen.getByLabelText("6-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });

    await user.click(screen.getByRole("switch", { name: "I've saved these codes" }));
    await user.click(screen.getByRole("button", { name: "Continue to the console" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't finish signing in. auth-service is restarting");
    // The codes are still on screen — a failure here must not take away what is shown once.
    expect(screen.getByText(RECOVERY[0] ?? "")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Continue to the console" }));
    expect(await screen.findByText("Arrived at /")).toBeDefined();
  });
});
