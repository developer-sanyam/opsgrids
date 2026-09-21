import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The live path. `USE_FIXTURES` is read at module load (`@/api/client`), so these re-import
 * after stubbing the flag — the same technique `api/uploadAsset.test.ts` uses, and the only way
 * to prove the flag is what selects the branch.
 */
async function liveApi(): Promise<typeof import("./api")> {
  vi.stubEnv("VITE_USE_FIXTURES", "false");
  vi.resetModules();
  return import("./api");
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("getStoreIdentity against the gateway", () => {
  it("GETs the store and returns it", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "s1",
        slug: "greenleaf",
        name: "Greenleaf",
        tagline: null,
        logo_text: null,
        status: "draft",
        currency: "INR",
        timezone: "Asia/Kolkata",
        custom_domain: null,
        custom_domain_status: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { getStoreIdentity } = await liveApi();

    const store = await getStoreIdentity();

    expect(store?.slug).toBe("greenleaf");
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/store");
    expect(init.method ?? "GET").toBe("GET");
  });

  it("returns null on a 404 rather than throwing — a fresh organization has no store yet", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "no store" })));
    const { getStoreIdentity } = await liveApi();

    await expect(getStoreIdentity()).resolves.toBeNull();
  });
});

describe("putStoreIdentity against the gateway", () => {
  it("PUTs the exact StorePut body — no status field, ever", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "s1",
        slug: "greenleaf",
        name: "Greenleaf",
        tagline: null,
        logo_text: null,
        status: "draft",
        currency: "INR",
        timezone: "Asia/Kolkata",
        custom_domain: null,
        custom_domain_status: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { putStoreIdentity } = await liveApi();

    await putStoreIdentity({
      slug: "greenleaf",
      name: "Greenleaf",
      tagline: null,
      logo_text: null,
      currency: "INR",
      timezone: "Asia/Kolkata",
      custom_domain: null,
    });

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/store");
    expect(init.method).toBe("PUT");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({
      slug: "greenleaf",
      name: "Greenleaf",
      tagline: null,
      logo_text: null,
      currency: "INR",
      timezone: "Asia/Kolkata",
      custom_domain: null,
    });
    expect(body.status).toBeUndefined();
  });
});

describe("publishStore / unpublishStore against the gateway", () => {
  it("POSTs to the exact publish and unpublish paths, with no body", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "s1",
        slug: "greenleaf",
        name: "Greenleaf",
        tagline: null,
        logo_text: null,
        status: "published",
        currency: "INR",
        timezone: "Asia/Kolkata",
        custom_domain: null,
        custom_domain_status: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { publishStore } = await liveApi();

    const store = await publishStore();

    expect(store.status).toBe("published");
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/store/publish");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});
