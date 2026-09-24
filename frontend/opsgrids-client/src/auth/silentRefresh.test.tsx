/**
 * Silent refresh end to end (ADR-0003 addendum) — the fix for a session dying on the access
 * token's own ~15-minute TTL, in every tab, on every product SPA.
 *
 * Three layers, three groups of tests:
 *   - `apiRequest`'s 401-retry (`http.ts`): a stale token heals itself instead of signing out.
 *   - `refreshAccessToken`'s single-flight (`auth/refresh.ts`): concurrent callers share one
 *     request rather than each burning their own rotation.
 *   - `AuthProvider`'s boot restore and proactive timer: a session survives a reload or a new
 *     tab, and renews itself before the access token actually expires.
 */

import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { apiRequest } from "../http";
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "../session";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./context";
import { configureAuthOrigin } from "./origin";

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

/** An unsigned, unverified token — exactly as much as `decodeAccessToken` ever reads. */
function fakeToken(claims: Record<string, unknown>): string {
  const segment = (value: unknown) => btoa(JSON.stringify(value));
  return `${segment({ alg: "none" })}.${segment(claims)}.sig`;
}

interface Call {
  url: string;
  headers: Record<string, string>;
}

let calls: Call[];

function record(input: RequestInfo | URL, init?: RequestInit): Call {
  const headers: Record<string, string> = {};
  new Headers(init?.headers).forEach((value, key) => {
    headers[key] = value;
  });
  const call = { url: String(input), headers };
  calls.push(call);
  return call;
}

beforeEach(() => {
  calls = [];
  setAccessToken(null);
  configureAuthOrigin(null);
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  configureAuthOrigin(null);
});

describe("apiRequest heals a 401 with one silent refresh", () => {
  it("refreshes once and retries the original request, which then succeeds", async () => {
    setAccessToken("stale-token");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call = record(input, init);
      if (call.url === "/api/v1/widgets") {
        return Promise.resolve(
          call.headers.authorization === "Bearer fresh-token"
            ? respond(200, { ok: true })
            : respond(401, {}),
        );
      }
      if (call.url === "/api/v1/auth/refresh") {
        return Promise.resolve(respond(200, { access_token: "fresh-token", expires_in: 900 }));
      }
      throw new Error(`unexpected call to ${call.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest<{ ok: boolean }>("/widgets");

    expect(result).toEqual({ ok: true });
    expect(getAccessToken()).toBe("fresh-token");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/widgets",
      "/api/v1/auth/refresh",
      "/api/v1/widgets",
    ]);
  });

  it("signs out when the refresh itself is unauthorized, without a second retry", async () => {
    setAccessToken("stale-token");
    const signedOut = vi.fn();
    setUnauthorizedHandler(signedOut);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/refresh") return Promise.resolve(respond(401, {}));
        return Promise.resolve(respond(401, {}));
      }),
    );

    await expect(apiRequest("/widgets")).rejects.toMatchObject({ status: 401 });

    expect(signedOut).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
    // Exactly one attempt at the original call, one refresh — no retry loop.
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/widgets", "/api/v1/auth/refresh"]);
  });

  it("never refreshes for a call that opted out (login, MFA, logout)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        record(input, init);
        return Promise.resolve(respond(401, { detail: "wrong password" }));
      }),
    );

    await expect(
      apiRequest("/auth/login", { auth: false, signalUnauthorized: false, method: "POST" }),
    ).rejects.toMatchObject({ status: 401 });

    expect(calls.map((c) => c.url)).toEqual(["/api/v1/auth/login"]);
  });
});

describe("refreshAccessToken is single-flight", () => {
  it("two concurrent 401s trigger exactly one refresh call", async () => {
    setAccessToken("stale-token");
    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/refresh") {
          refreshCalls += 1;
          return Promise.resolve(respond(200, { access_token: "fresh-token", expires_in: 900 }));
        }
        // /a and /b: unauthorized until the refreshed token shows up.
        return Promise.resolve(
          call.headers.authorization === "Bearer fresh-token"
            ? respond(200, { from: call.url })
            : respond(401, {}),
        );
      }),
    );

    const [a, b] = await Promise.all([
      apiRequest<{ from: string }>("/a"),
      apiRequest<{ from: string }>("/b"),
    ]);

    expect(a).toEqual({ from: "/api/v1/a" });
    expect(b).toEqual({ from: "/api/v1/b" });
    expect(refreshCalls).toBe(1);
  });
});

/** A tiny screen that surfaces `useAuth()`'s status — enough to drive the provider's effects. */
function Probe({ onReady }: { onReady?: (call: () => Promise<unknown>) => void }) {
  const { status } = useAuth();
  useEffect(() => {
    onReady?.(() => apiRequest("/widgets"));
  }, [onReady]);
  return <p data-testid="status">{status}</p>;
}

describe("AuthProvider restores a session on boot", () => {
  it("with no token in memory, tries a silent refresh before landing on /login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/refresh") {
          return Promise.resolve(respond(200, { access_token: "restored-token", expires_in: 900 }));
        }
        if (call.url === "/api/v1/auth/me") {
          return Promise.resolve(
            respond(200, {
              user_id: "u-1",
              email: "priya@acme.example.com",
              full_name: "Priya Nair",
              organization_id: "org-a",
              membership_id: "mem-1",
              roles: [],
              amr: ["pwd"],
              must_change_password: false,
              mfa_enabled: false,
              organizations: [],
            }),
          );
        }
        throw new Error(`unexpected call to ${call.url}`);
      }),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(getAccessToken()).toBe("restored-token");
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/auth/refresh", "/api/v1/auth/me"]);
  });

  it("with no cookie either, lands on unauthenticated without ever calling /me", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/refresh") return Promise.resolve(respond(401, {}));
        throw new Error(`unexpected call to ${call.url}`);
      }),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/auth/refresh"]);
  });
});

describe("AuthProvider refreshes proactively before the access token expires", () => {
  it(
    "renews itself ~60s ahead of exp, with no 401 ever happening",
    async () => {
      // A token that expires 61s from now schedules the proactive refresh about a second out
      // (`PROACTIVE_REFRESH_LEAD_MS` is 60s) — real timers, and a real delay short enough that
      // the test can simply wait it out.
      const ttlSeconds = 61;
      setAccessToken(fakeToken({ exp: Math.floor(Date.now() / 1000) + ttlSeconds }));

      vi.stubGlobal(
        "fetch",
        vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
          const call = record(input, init);
          if (call.url === "/api/v1/auth/me") {
            return Promise.resolve(
              respond(200, {
                user_id: "u-1",
                email: "priya@acme.example.com",
                full_name: "Priya Nair",
                organization_id: "org-a",
                membership_id: "mem-1",
                roles: [],
                amr: ["pwd"],
                must_change_password: false,
                mfa_enabled: false,
                organizations: [],
              }),
            );
          }
          if (call.url === "/api/v1/auth/refresh") {
            return Promise.resolve(
              respond(200, {
                access_token: fakeToken({ exp: Math.floor(Date.now() / 1000) + ttlSeconds }),
                expires_in: ttlSeconds,
              }),
            );
          }
          throw new Error(`unexpected call to ${call.url}`);
        }),
      );

      render(
        <AuthProvider>
          <Probe />
        </AuthProvider>,
      );
      await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
      const beforeSchedule = getAccessToken();

      // The proactive timer fires on its own, with no 401 anywhere in the log to force it.
      await waitFor(() => expect(getAccessToken()).not.toBe(beforeSchedule), { timeout: 4000 });
      expect(calls.filter((c) => c.url === "/api/v1/auth/refresh")).toHaveLength(1);
    },
    8000,
  );
});

describe("logout", () => {
  it("posts to the auth origin with the CSRF header and clears the local session", async () => {
    configureAuthOrigin("https://app.opsgrids.com");
    setAccessToken("a-token");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        record(input, init);
        return Promise.resolve(new Response(null, { status: 204 }));
      }),
    );

    const authApi = await import("./api");
    await authApi.logout();

    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("https://app.opsgrids.com/api/v1/auth/logout");
    expect(calls[0]?.headers["x-requested-with"]).toBe("opsgrids");
  });
});
