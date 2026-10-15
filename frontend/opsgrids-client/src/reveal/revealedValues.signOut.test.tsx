/**
 * A revealed value is sticky for a session and ends with it. `AuthProvider` owns every place a
 * session ends — the logout button, a 401 that survives its silent refresh, a failed boot restore —
 * and each one empties the in-memory reveal cache, so the next person on the tab starts masked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthProvider } from "../auth/AuthProvider";
import { useAuth } from "../auth/context";
import { getAccessToken, notifyUnauthorized, setAccessToken } from "../session";
import { clearRevealedValues, getRevealedValue, setRevealedValue } from "./revealedValues";

const KEY = "lead:lead-1:phone number";

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
  clearRevealedValues();
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
  clearRevealedValues();
  sessionStorage.clear();
});

describe("revealed values and the end of a session", () => {
  it("are dropped the moment the person signs out, before the sign-out request has even answered", async () => {
    const user = userEvent.setup();
    await signedIn();
    setRevealedValue(KEY, "+91 98765 04821");

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(getRevealedValue(KEY)).toBeNull();
    await waitFor(() => expect(logoutAnswer).not.toBeNull());
    logoutAnswer?.(new Response(null, { status: 204 }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(getAccessToken()).toBeNull();
  });

  it("are dropped when a 401 ends the session", async () => {
    await signedIn();
    setRevealedValue(KEY, "+91 98765 04821");

    notifyUnauthorized();

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(getRevealedValue(KEY)).toBeNull();
  });

  it("are kept while the session lives", async () => {
    await signedIn();
    setRevealedValue(KEY, "+91 98765 04821");
    expect(getRevealedValue(KEY)).toBe("+91 98765 04821");
  });
});
