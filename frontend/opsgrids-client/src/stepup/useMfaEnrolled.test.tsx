/**
 * `useMfaEnrolled`: prefers a mounted `AuthContext` (fixture mode's own identity, no network
 * call — the case that matters most, since a fetch that hangs against fixture mode's "no
 * gateway" would leave a reveal control permanently hidden); falls back to its own `GET
 * /auth/me` only when no context is mounted at all, which is what lets a screen using this stay
 * testable without the full session lifecycle wrapped around it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { MeOut } from "../auth/types";
import { useMfaEnrolled } from "./useMfaEnrolled";

function Harness() {
  const enrolled = useMfaEnrolled();
  return <p data-testid="enrolled">{String(enrolled)}</p>;
}

const noop = () => {
  throw new Error("not used by these tests");
};

function contextValue(user: MeOut | null): AuthContextValue {
  return {
    status: user ? "authenticated" : "loading",
    user,
    login: noop,
    verifyMfa: noop,
    selectOrg: noop,
    logout: noop,
  };
}

const USER: MeOut = {
  user_id: "user-1",
  email: "priya@acme.example.com",
  full_name: "Priya Nair",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: true,
  organizations: [],
};

describe("useMfaEnrolled: a mounted AuthContext", () => {
  it("reads the context's user directly and makes no network call", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <AuthContext.Provider value={contextValue(USER)}>
        <Harness />
      </AuthContext.Provider>,
    );

    expect(await screen.findByText("true")).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("is false for a signed-in user with no second factor", async () => {
    render(
      <AuthContext.Provider value={contextValue({ ...USER, mfa_enabled: false })}>
        <Harness />
      </AuthContext.Provider>,
    );

    expect(await screen.findByText("false")).toBeTruthy();
  });

  it("is false before sign-in completes (user still null)", async () => {
    render(
      <AuthContext.Provider value={contextValue(null)}>
        <Harness />
      </AuthContext.Provider>,
    );

    expect(await screen.findByText("false")).toBeTruthy();
  });
});

describe("useMfaEnrolled: no AuthContext mounted", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (String(input) !== "/api/v1/auth/me") throw new Error(`No stub for ${String(input)}`);
        return Promise.resolve(
          new Response(JSON.stringify({ ...USER, mfa_enabled: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("falls back to its own GET /auth/me and reflects the answer", async () => {
    render(<Harness />);

    expect(await screen.findByText("true")).toBeTruthy();
  });
});
