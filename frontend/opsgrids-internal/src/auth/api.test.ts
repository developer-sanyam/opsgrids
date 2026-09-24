import { afterEach, describe, expect, it, vi } from "vitest";

import { login, logout } from "./api";

/**
 * The wire shape of the two calls the security review's fix round tightened
 * (`services/auth/app/api/routes.py`'s `_assert_not_cross_site`): `logout` is
 * cookie-authenticated, not bearer-authenticated, and every cookie-authenticated auth route now
 * refuses a request that lacks the CSRF header a forged cross-site form post cannot carry. Pinned
 * here as the exact header set, not just "it doesn't 403", so a future refactor that quietly drops
 * the header — or starts attaching a bearer token logout never needed — fails a test instead of
 * only a live 403.
 */

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: status === 204 ? {} : { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const first = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!first) throw new Error("fetch was never called");
  return first;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("logout", () => {
  it("sends the CSRF header and no Authorization header, cookie-authenticated like the tenant logout", async () => {
    const fetchMock = stubFetch(null, 204);
    await logout();
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/auth/operator/logout");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Requested-With"]).toBe("opsgrids");
    expect(headers.Authorization).toBeUndefined();
  });
});

describe("login", () => {
  it("carries the credential in the body and no Authorization header — there is no token yet", async () => {
    const fetchMock = stubFetch({ status: "mfa_required", pre_auth_token: "pat" });
    await login("owner@opsgrids.example", "hunter2");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/auth/operator/login");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(JSON.parse(init.body as string)).toEqual({
      email: "owner@opsgrids.example",
      password: "hunter2",
    });
  });
});
