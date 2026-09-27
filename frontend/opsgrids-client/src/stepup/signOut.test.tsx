/**
 * A step-up token is proof of a code typed in one particular session, so it must not outlive that
 * session by a single request. `AuthProvider` owns every place a session ends — the logout button,
 * a 401 that survives its silent refresh, a failed boot restore — and each one drops the token.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthProvider } from "../auth/AuthProvider";
import { useAuth } from "../auth/context";
import { getAccessToken, notifyUnauthorized, setAccessToken } from "../session";
import { clearStepUpToken, getStepUpToken, setStepUpToken } from "./holder";

const STEP_UP = "step-up-access-token";

const ME = {
  user_id: "u-1",
  email: "priya@acme.example.com",
  full_name: "Priya Nair",
  organization_id: "org-a",
  membership_id: "mem-1",
  roles: [],
  amr: ["pwd", "otp"],
  must_change_password: false,
  mfa_enabled: true,
  organizations: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

let logoutAnswer: ((response: Response) => void) | null;

function Screen() {
  const { status, logout } = useAuth();
  return (
    <div>
      <p data-testid="status">{status}</p>
      <button type="button" onClick={() => void logout()}>
        Sign out
      </button>
    </div>
  );
}

async function signedIn() {
  render(
    <AuthProvider>
      <Screen />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
}

beforeEach(() => {
  logoutAnswer = null;
  setAccessToken("session-access-token");
  clearStepUpToken();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/me") return Promise.resolve(json(ME));
      if (url === "/api/v1/auth/logout") {
        return new Promise<Response>((resolve) => {
          logoutAnswer = resolve;
        });
      }
      throw new Error(`No stub for ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  clearStepUpToken();
  sessionStorage.clear();
});

describe("the step-up token and the end of a session", () => {
  it("is dropped the moment the person signs out, before the sign-out request has even answered", async () => {
    const user = userEvent.setup();
    await signedIn();
    setStepUpToken(STEP_UP, 180);

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    // The server has not answered yet: the token must already be gone.
    expect(getStepUpToken()).toBeNull();
    await waitFor(() => expect(logoutAnswer).not.toBeNull());
    logoutAnswer?.(new Response(null, { status: 204 }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(getAccessToken()).toBeNull();
  });

  it("is dropped when a 401 ends the session", async () => {
    await signedIn();
    setStepUpToken(STEP_UP, 180);

    notifyUnauthorized();

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(getStepUpToken()).toBeNull();
  });

  it("is not held by a session that never asked for one", async () => {
    await signedIn();
    expect(getStepUpToken()).toBeNull();
  });
});
