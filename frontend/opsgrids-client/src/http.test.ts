import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "./http";

function respond(status: number, body: string | null, headers: Record<string, string> = {}) {
  return new Response(body, { status, headers });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("decodes a JSON body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(200, '{"ok":true}', { "content-type": "application/json" })));
    await expect(apiRequest<{ ok: boolean }>("/x", { auth: false })).resolves.toEqual({ ok: true });
  });

  it("treats an empty 2xx as no content — a 202 from password/forgot is not a JSON error", async () => {
    // The bug that reached a person: `password/forgot` answers 202 with nothing in it, on
    // purpose, and the screen showed "Unexpected end of JSON input" after a request that
    // had succeeded.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(202, null)));
    await expect(apiRequest<void>("/auth/password/forgot", { auth: false })).resolves.toBeUndefined();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(204, null)));
    await expect(apiRequest<void>("/x", { auth: false })).resolves.toBeUndefined();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(200, "   ")));
    await expect(apiRequest<void>("/x", { auth: false })).resolves.toBeUndefined();
  });

  it("raises ApiError with the problem document on a failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(409, '{"title":"Conflict","status":409,"detail":"taken"}', { "content-type": "application/problem+json" })),
    );
    await expect(apiRequest("/x", { auth: false, signalUnauthorized: false })).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "taken",
    });
    const err = await apiRequest("/x", { auth: false, signalUnauthorized: false }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});
