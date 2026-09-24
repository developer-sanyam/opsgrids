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
import { act, render, screen, waitFor } from "@testing-library/react";

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

const ME_RESPONSE_BODY = {
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
};

/** Advance fake time in small steps, each wrapped in `act`, rather than one big jump — a
 *  `setTimeout` callback that itself schedules another `setTimeout` (exactly what the proactive
 *  loop does on every success) only runs the second one if the advancement is still in
 *  progress when it is scheduled; small steps make that reliable regardless of how many
 *  reschedules happen inside the window. */
async function advance(totalMs: number, stepMs = 50): Promise<void> {
  let remaining = totalMs;
  do {
    const step = Math.min(stepMs, remaining);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(step);
    });
    remaining -= step;
  } while (remaining > 0);
}

describe("AuthProvider refreshes proactively before the access token expires", () => {
  it("renews itself ~60s ahead of exp, scheduling from expires_in rather than exp", async () => {
    vi.useFakeTimers();
    const ttlSeconds = 900;
    setAccessToken(fakeToken({ exp: Math.floor(Date.now() / 1000) + ttlSeconds }));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/me") return Promise.resolve(respond(200, ME_RESPONSE_BODY));
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
    await advance(0);
    expect(screen.getByTestId("status").textContent).toBe("authenticated");
    const beforeSchedule = getAccessToken();

    // Comfortably inside the schedule (900s ttl - 60s lead = 840s out): nothing yet.
    await advance(800_000, 10_000);
    expect(getAccessToken()).toBe(beforeSchedule);

    // Past it: the proactive refresh has fired, with no 401 anywhere in the log.
    await advance(60_000, 10_000);
    expect(getAccessToken()).not.toBe(beforeSchedule);
    expect(calls.filter((c) => c.url === "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("does not spin when the client clock is far ahead of the server's", async () => {
    // Adopted from the security review's clock-skew probe: a client clock 20 minutes ahead of
    // the server's made `decodeAccessToken().exp * 1000 - Date.now()` come out deeply negative
    // — "already expired" — every single time the proactive timer recomputed it, which fired it
    // again immediately, forever. `PROACTIVE_REFRESH_MIN_DELAY_MS` is what a token already held
    // at boot (this test's setup — nothing here ever calls `/auth/refresh` to get a fresh
    // `expires_in`) still has to fall back to decoding `exp` for, so this is exactly the path
    // the floor has to hold on its own.
    vi.useFakeTimers();
    const serverNow = () => Date.now() - 20 * 60 * 1000;
    setAccessToken(fakeToken({ exp: Math.floor(serverNow() / 1000) + 900 }));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/me") return Promise.resolve(respond(200, ME_RESPONSE_BODY));
        if (call.url === "/api/v1/auth/refresh") {
          return Promise.resolve(
            respond(200, {
              access_token: fakeToken({ exp: Math.floor(serverNow() / 1000) + 900 }),
              expires_in: 900,
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
    await advance(0);
    expect(screen.getByTestId("status").textContent).toBe("authenticated");

    await advance(500, 10);

    const refreshes = calls.filter((c) => c.url === "/api/v1/auth/refresh").length;
    expect(refreshes).toBeLessThanOrEqual(1);
  });

  it("backs off exponentially on repeated failures, and resets on the next success", async () => {
    vi.useFakeTimers();
    const ttlSeconds = 100; // schedule floors to 30s (100 - 60 lead < 30 min) either way
    setAccessToken(fakeToken({ exp: Math.floor(Date.now() / 1000) + ttlSeconds }));
    let refreshAttempts = 0;
    const FAILURES_BEFORE_SUCCESS = 3;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/me") return Promise.resolve(respond(200, ME_RESPONSE_BODY));
        if (call.url === "/api/v1/auth/refresh") {
          refreshAttempts += 1;
          if (refreshAttempts <= FAILURES_BEFORE_SUCCESS) return Promise.reject(new Error("network down"));
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
    await advance(0);
    expect(screen.getByTestId("status").textContent).toBe("authenticated");

    // First attempt at 100s ttl - 60s lead = 40s, then backing off 5s, 10s, 20s between failures.
    await advance(40_000, 5_000);
    expect(refreshAttempts).toBe(1);
    await advance(5_000);
    expect(refreshAttempts).toBe(2);
    await advance(10_000);
    expect(refreshAttempts).toBe(3);
    await advance(20_000);
    expect(refreshAttempts).toBe(4); // the success

    expect(getAccessToken()).not.toBeNull();
    // Signed in throughout — a network blip never signs anyone out.
    expect(screen.getByTestId("status").textContent).toBe("authenticated");
  });

  it("stops rescheduling once a refresh reports the session is over", async () => {
    vi.useFakeTimers();
    const ttlSeconds = 100;
    setAccessToken(fakeToken({ exp: Math.floor(Date.now() / 1000) + ttlSeconds }));
    let refreshAttempts = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const call = record(input, init);
        if (call.url === "/api/v1/auth/me") return Promise.resolve(respond(200, ME_RESPONSE_BODY));
        if (call.url === "/api/v1/auth/refresh") {
          refreshAttempts += 1;
          return Promise.resolve(respond(401, {}));
        }
        throw new Error(`unexpected call to ${call.url}`);
      }),
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await advance(0);
    expect(screen.getByTestId("status").textContent).toBe("authenticated");

    await advance(40_000, 5_000);
    expect(refreshAttempts).toBe(1);
    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");

    await advance(600_000, 30_000);
    expect(refreshAttempts).toBe(1);
  });

  it(
    "keeps the session alive through many access-token lifetimes, then signs out cleanly " +
      "once the server's own 7-day session cap is reached",
    async () => {
      // Owner decision, 2026-09-24: a session is capped at 7 days server-side regardless of
      // activity. The client has no notion of "7 days" at all — it just keeps renewing on
      // whatever `expires_in` each response carries, forever, until a refresh call itself
      // reports the session is over. This is that contract from the client's side: many
      // 15-minute-token renewals in a row never sign anyone out, and the eventual 401 (the
      // session's own absolute cap, not a network blip) ends it once, cleanly — never a loop.
      vi.useFakeTimers();
      const accessTtlSeconds = 900; // 15 minutes, this platform's real access_token_ttl_seconds
      setAccessToken(fakeToken({ exp: Math.floor(Date.now() / 1000) + accessTtlSeconds }));
      let refreshCount = 0;
      const REFRESHES_BEFORE_THE_SESSION_CAP = 100; // comfortably more than one day's worth

      vi.stubGlobal(
        "fetch",
        vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
          const call = record(input, init);
          if (call.url === "/api/v1/auth/me") {
            return Promise.resolve(respond(200, ME_RESPONSE_BODY));
          }
          if (call.url === "/api/v1/auth/refresh") {
            refreshCount += 1;
            if (refreshCount > REFRESHES_BEFORE_THE_SESSION_CAP) {
              // The server clamps a refresh token's own lifetime to what is left of the
              // session (`_refresh_ttl_seconds`, services/auth) — this is what that clamp
              // running out looks like from here: an ordinary 401, not a network failure.
              return Promise.resolve(respond(401, {}));
            }
            return Promise.resolve(
              respond(200, {
                access_token: fakeToken({
                  exp: Math.floor(Date.now() / 1000) + accessTtlSeconds,
                }),
                expires_in: accessTtlSeconds,
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
      await advance(0);
      expect(screen.getByTestId("status").textContent).toBe("authenticated");

      // Every renewal in between: still signed in, never a loop, never an error screen.
      for (let i = 0; i < REFRESHES_BEFORE_THE_SESSION_CAP; i += 1) {
        await advance(accessTtlSeconds * 1000 - 60_000, 60_000);
        expect(screen.getByTestId("status").textContent).toBe("authenticated");
      }
      expect(refreshCount).toBe(REFRESHES_BEFORE_THE_SESSION_CAP);

      // The next one is the session's own absolute cap — a clean, one-time sign-out.
      await advance(accessTtlSeconds * 1000 - 60_000, 60_000);
      expect(refreshCount).toBe(REFRESHES_BEFORE_THE_SESSION_CAP + 1);
      expect(screen.getByTestId("status").textContent).toBe("unauthenticated");

      // And it stays that way — no further refresh attempts, no loop.
      await advance(accessTtlSeconds * 1000, 60_000);
      expect(refreshCount).toBe(REFRESHES_BEFORE_THE_SESSION_CAP + 1);
    },
    20_000,
  );
});

describe("logout", () => {
  it("posts to the auth origin with the CSRF header, and never Authorization", async () => {
    // Security review, fix round 1, B2: the scoped CORS preflight for /auth/logout allows
    // only Content-Type and X-Requested-With. Sending Authorization alongside them made the
    // browser refuse the preflight outright — the request never reached the server, and the
    // best-effort `catch` around it in `AuthProvider.logout` swallowed that silently.
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
    expect(calls[0]?.headers.authorization).toBeUndefined();
  });
});
