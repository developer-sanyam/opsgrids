import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/api/http";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./context";
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "./session";
import type { OperatorMe } from "./types";

/**
 * The operator session must survive what a browser does to `sessionStorage`: a new browser run
 * (closing and reopening it), a new tab, and the access token simply expiring after 15 minutes.
 * The access token is mirrored only to `sessionStorage`; what carries the session across all
 * three is the httpOnly refresh cookie auth-service sets on operator sign-in, and
 * `POST /api/v1/auth/operator/refresh` is what turns it back into an access token. Before this,
 * the console never called that route: an empty `sessionStorage` landed on the sign-in screen and
 * the first 401 after expiry signed the operator out, with a perfectly good cookie unused.
 */

const OPERATOR: OperatorMe = {
  staff_id: "op_1",
  email: "b@opsgrids.com",
  display_name: "B Test",
  operator_roles: ["owner"],
  amr: ["pwd", "otp"],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function refreshed(token: string): Response {
  return json({ status: "complete", access_token: token, token_type: "Bearer", expires_in: 900, operator_roles: ["owner"] });
}

type Route = (init: RequestInit) => Response | Promise<Response>;

/** Stub `fetch` by path; records every call so a test can assert what was sent. */
function stubFetch(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const route = routes[url];
    if (!route) throw new Error(`unexpected fetch ${url}`);
    return route(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: ReturnType<typeof stubFetch>, url: string): RequestInit[] {
  return fetchMock.mock.calls.filter(([u]) => u === url).map(([, init]) => init ?? {});
}

function header(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function StatusProbe() {
  const { status, operator } = useAuth();
  return (
    <p>
      {status}
      {operator ? ` as ${operator.email}` : ""}
    </p>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  setAccessToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

describe("boot after a new browser run (empty sessionStorage)", () => {
  it("restores the session from the refresh cookie instead of showing sign-in", async () => {
    const fetchMock = stubFetch({
      "/api/v1/auth/operator/refresh": () => refreshed("fresh"),
      "/api/v1/auth/operator/me": () => json(OPERATOR),
    });

    render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("authenticated as b@opsgrids.com")).toBeTruthy();
    const [refresh] = callsTo(fetchMock, "/api/v1/auth/operator/refresh");
    expect(refresh?.method).toBe("POST");
    expect(refresh?.credentials).toBe("same-origin");
    // The CSRF header auth-service's `_assert_not_cross_site` requires, and no bearer token.
    expect(header(refresh ?? {}, "X-Requested-With")).toBe("opsgrids");
    expect(header(refresh ?? {}, "Authorization")).toBeUndefined();
    const [me] = callsTo(fetchMock, "/api/v1/auth/operator/me");
    expect(header(me ?? {}, "Authorization")).toBe("Bearer fresh");
    expect(getAccessToken()).toBe("fresh");
  });

  it("lands signed out, without asking who the operator is, when there is no live cookie", async () => {
    const fetchMock = stubFetch({
      "/api/v1/auth/operator/refresh": () => json({ title: "No session to refresh" }, 401),
      "/api/v1/auth/operator/me": () => json(OPERATOR),
    });

    render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("unauthenticated")).toBeTruthy();
    expect(callsTo(fetchMock, "/api/v1/auth/operator/me")).toHaveLength(0);
    expect(getAccessToken()).toBeNull();
  });

  it("lands signed out when the refresh cannot be reached at all", async () => {
    stubFetch({
      "/api/v1/auth/operator/refresh": () => Promise.reject(new TypeError("Failed to fetch")),
    });

    render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("unauthenticated")).toBeTruthy();
  });
});

describe("a 401 from an expired access token", () => {
  it("refreshes once and retries the request with the new token", async () => {
    setAccessToken("expired");
    let pricingCalls = 0;
    const fetchMock = stubFetch({
      "/api/v1/auth/operator/refresh": () => refreshed("renewed"),
      "/api/v1/admin/things": (init) => {
        pricingCalls += 1;
        return header(init, "Authorization") === "Bearer renewed"
          ? json({ ok: true })
          : json({ title: "expired" }, 401);
      },
    });
    const signedOut = vi.fn();
    setUnauthorizedHandler(signedOut);

    await expect(
      apiRequest("/admin/things", { method: "POST", body: { a: 1 }, headers: { "Idempotency-Key": "k1" } }),
    ).resolves.toEqual({ ok: true });

    expect(pricingCalls).toBe(2);
    const retry = callsTo(fetchMock, "/api/v1/admin/things")[1] ?? {};
    // The retry is the same request: same body, same idempotency key.
    expect(retry.body).toBe(JSON.stringify({ a: 1 }));
    expect(header(retry, "Idempotency-Key")).toBe("k1");
    expect(signedOut).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe("renewed");
  });

  it("signs out when the refresh itself is refused", async () => {
    setAccessToken("expired");
    stubFetch({
      "/api/v1/auth/operator/refresh": () => json({ title: "expired" }, 401),
      "/api/v1/admin/things": () => json({ title: "expired" }, 401),
    });
    const signedOut = vi.fn();
    setUnauthorizedHandler(signedOut);

    await expect(apiRequest("/admin/things")).rejects.toMatchObject({ status: 401 });
    expect(signedOut).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it("shares one refresh between requests that fail together", async () => {
    setAccessToken("expired");
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = stubFetch({
      "/api/v1/auth/operator/refresh": async () => {
        await gate;
        return refreshed("renewed");
      },
      "/api/v1/admin/things": (init) =>
        header(init, "Authorization") === "Bearer renewed" ? json({ ok: true }) : json({}, 401),
    });

    const both = Promise.all([apiRequest("/admin/things"), apiRequest("/admin/things")]);
    await vi.waitFor(() => expect(callsTo(fetchMock, "/api/v1/auth/operator/refresh")).toHaveLength(1));
    release!();
    await expect(both).resolves.toEqual([{ ok: true }, { ok: true }]);
    // A second rotation of the same cookie would read as reuse; one in-flight refresh serves both.
    expect(callsTo(fetchMock, "/api/v1/auth/operator/refresh")).toHaveLength(1);
  });

  it("does not refresh for a pre-auth call whose 401 means a wrong credential", async () => {
    const fetchMock = stubFetch({
      "/api/v1/auth/operator/login": () => json({ title: "Wrong email or password" }, 401),
    });

    await expect(
      apiRequest("/auth/operator/login", {
        method: "POST",
        body: {},
        auth: false,
        signalUnauthorized: false,
      }),
    ).rejects.toMatchObject({ status: 401 });
    expect(callsTo(fetchMock, "/api/v1/auth/operator/refresh")).toHaveLength(0);
  });
});
