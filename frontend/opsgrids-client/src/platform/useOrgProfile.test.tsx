/**
 * The organization's timezone, currency and locale, read once per session: two screens asking
 * share one request, and a failed read still answers, with the platform defaults marked
 * `fallback` so a caller can say so rather than pass them off as the organization's own.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Module = typeof import("./useOrgProfile");

let useOrgProfile: Module["useOrgProfile"];

beforeEach(async () => {
  vi.resetModules();
  ({ useOrgProfile } = await import("./useOrgProfile"));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("useOrgProfile", () => {
  it("reads GET /organization once for every caller in the session", async () => {
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(() =>
      Promise.resolve(respond({ id: "org-1", timezone: "Asia/Dubai", currency: "AED", locale: "en-AE" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = renderHook(() => useOrgProfile());
    const second = renderHook(() => useOrgProfile());

    expect(first.result.current.status).toBe("loading");
    await waitFor(() => expect(first.result.current.status).toBe("success"));
    await waitFor(() => expect(second.result.current.status).toBe("success"));
    expect(first.result.current).toEqual({ timezone: "Asia/Dubai", currency: "AED", locale: "en-AE", status: "success" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/organization");

    const later = renderHook(() => useOrgProfile());
    expect(later.result.current.status).toBe("success");
  });

  it("answers the platform defaults, marked fallback, when the read fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(respond({ title: "Server error" }, 500))));

    const { result } = renderHook(() => useOrgProfile());

    await waitFor(() => expect(result.current.status).toBe("fallback"));
    expect(result.current).toEqual({ timezone: "Asia/Kolkata", currency: "INR", locale: "en-IN", status: "fallback" });
  });
});
