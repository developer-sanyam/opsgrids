/**
 * The bootstrap client: what it sends, what it caches, and — the half that matters — which
 * failures are "there is no shop here" and which are "we could not look".
 *
 * Every assertion below about status codes is the same rule stated once per code path: **only a
 * 404 is an absence.** A 422, a 429, a 503, a dead socket, a timeout and a 200 carrying a body
 * this build cannot read are all "unavailable", because rendering any of them as "no shop here"
 * puts a lie on a working merchant's own domain.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE } from "@opsgrids/client";

import {
  currentHost,
  loadStoreContext,
  readCachedStoreContext,
  StoreContextError,
  STOREFRONT_API_BASE,
  type StoreContextOut,
} from "./storeContext";

const HOST = "merchant.com";
const PATH = "/api/v1/storefront/public/store-context";

const CONTEXT: StoreContextOut = {
  store: "greenleaf",
  name: "Greenleaf Organics",
  tagline: "Fresh from the farm, daily",
  logo_text: "GLO",
  currency: "INR",
  timezone: "Asia/Kolkata",
  canonical_host: "merchant.com",
  is_canonical: true,
  theme: null,
  checked_at: "2026-09-19T11:14:22Z",
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function unreadableResponse(status: number): Response {
  return {
    ok: true,
    status,
    json: () => Promise.reject(new SyntaxError("Unexpected token <")),
  } as unknown as Response;
}

function mockFetch(...responses: (Response | Error)[]) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    if (response instanceof Error) fetchMock.mockRejectedValueOnce(response);
    else fetchMock.mockResolvedValueOnce(response);
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

async function failureOf(promise: Promise<unknown>): Promise<StoreContextError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof StoreContextError) return error;
    throw error;
  }
  throw new Error("Expected the bootstrap to fail, and it resolved.");
}

describe("what the bundle sends", () => {
  it("posts the host and nothing else, to the exact public path", async () => {
    const fetchMock = mockFetch(jsonResponse(200, CONTEXT));

    await loadStoreContext(HOST);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PATH);
    expect(init.method).toBe("POST");
    // `additionalProperties: false` on the schema — a spare key is a 422, and a 422 is a buyer
    // looking at "we can't load this shop" because of something this bundle did.
    expect(JSON.parse(String(init.body))).toEqual({ host: HOST });
  });

  it("carries no Authorization header, because there is no session to carry", async () => {
    const fetchMock = mockFetch(jsonResponse(200, CONTEXT));

    await loadStoreContext(HOST);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(Object.keys(init.headers as Record<string, string>).sort()).toEqual([
      "Accept",
      "Content-Type",
    ]);
  });

  it("sends window.location.hostname — no port, no scheme, no constructed value", () => {
    // jsdom's default location is http://localhost/. The point is that `currentHost` reads
    // `hostname` and not `host`, which would carry the port on a dev server.
    expect(currentHost()).toBe(window.location.hostname);
    expect(currentHost()).not.toContain(":");
  });

  it("uses the same API base as the SDK, without importing its HTTP layer", () => {
    /* `@opsgrids/client`'s `API_BASE` lives in a module that imports the session, and the buyer
       bundle must contain no token machinery. This assertion is what keeps the duplicated
       constant honest — it is a test-time import, not a bundle-time one. */
    expect(STOREFRONT_API_BASE).toBe(API_BASE);
  });
});

describe("a 404 is an absence, and nothing else is", () => {
  it("reads a 404 as not_found", async () => {
    mockFetch(jsonResponse(404, { detail: "No published store at this address." }));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("not_found");
    expect(error.status).toBe(404);
  });

  it.each([422, 429, 500, 502, 503, 504])("reads a %i as unavailable", async (status) => {
    mockFetch(jsonResponse(status, { detail: "nope" }));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("unavailable");
  });

  it("reads a dead network as unavailable, never as an absence", async () => {
    mockFetch(new TypeError("Failed to fetch"));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("unavailable");
    expect(error.status).toBe(0);
  });

  it("reads a 200 whose body will not parse as unavailable", async () => {
    // An HTML error page from a proxy, served with a 200. We could not look.
    mockFetch(unreadableResponse(200));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("unavailable");
  });

  it("reads a 200 missing a required field as unavailable", async () => {
    const { canonical_host, ...short } = CONTEXT;
    void canonical_host;
    mockFetch(jsonResponse(200, short));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("unavailable");
  });
});

describe("required-but-nullable fields", () => {
  it("accepts a null tagline and a null logo_text", async () => {
    mockFetch(jsonResponse(200, { ...CONTEXT, tagline: null, logo_text: null }));
    const context = await loadStoreContext(HOST);
    expect(context.tagline).toBeNull();
    expect(context.logo_text).toBeNull();
  });

  it("refuses a body that omits them rather than treating absent as null", async () => {
    // Absent is not a state the wire has. A build that accepted it would stop noticing the day
    // the service started omitting them.
    const { tagline, logo_text, ...without } = CONTEXT;
    void tagline;
    void logo_text;
    mockFetch(jsonResponse(200, without));
    const error = await failureOf(loadStoreContext(HOST));
    expect(error.reason).toBe("unavailable");
  });
});

describe("the session cache", () => {
  it("asks once per session, not once per page view", async () => {
    const fetchMock = mockFetch(jsonResponse(200, CONTEXT));

    await loadStoreContext(HOST);
    const second = await loadStoreContext(HOST);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(second).toEqual(CONTEXT);
  });

  it("uses sessionStorage and never localStorage", async () => {
    mockFetch(jsonResponse(200, CONTEXT));
    await loadStoreContext(HOST);

    expect(window.sessionStorage.length).toBe(1);
    // It is not the buyer's data and it goes stale the moment a merchant renames their shop.
    expect(window.localStorage.length).toBe(0);
  });

  it("treats a different host as a miss rather than re-using the answer", async () => {
    const fetchMock = mockFetch(jsonResponse(200, CONTEXT), jsonResponse(200, CONTEXT));

    await loadStoreContext(HOST);
    await loadStoreContext("www.merchant.com");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(readCachedStoreContext(HOST)).toBeNull();
    expect(readCachedStoreContext("www.merchant.com")).toEqual(CONTEXT);
  });

  it("never caches a failure", async () => {
    mockFetch(jsonResponse(503, {}));
    await failureOf(loadStoreContext(HOST));
    expect(window.sessionStorage.length).toBe(0);
  });

  it("treats a short cached entry from an older build as a miss", async () => {
    window.sessionStorage.setItem(
      "opsgrids.storefront.buyer.store_context",
      JSON.stringify({ host: HOST, context: { store: "greenleaf", name: "Greenleaf" } }),
    );
    expect(readCachedStoreContext(HOST)).toBeNull();

    const fetchMock = mockFetch(jsonResponse(200, CONTEXT));
    await loadStoreContext(HOST);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("treats unreadable storage as a miss instead of failing the shop", async () => {
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("denied", "SecurityError");
      });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    mockFetch(jsonResponse(200, CONTEXT));

    await expect(loadStoreContext(HOST)).resolves.toEqual(CONTEXT);

    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("collapses two simultaneous calls into one request", async () => {
    // StrictMode mounts every effect twice in development; a remount must not spend a second
    // request out of a 60-per-minute-per-IP budget shared by a whole office.
    const fetchMock = mockFetch(jsonResponse(200, CONTEXT));

    const [a, b] = await Promise.all([loadStoreContext(HOST), loadStoreContext(HOST)]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toEqual(b);
  });

  it("lets a retry through after a failure", async () => {
    const fetchMock = mockFetch(jsonResponse(503, {}), jsonResponse(200, CONTEXT));

    await failureOf(loadStoreContext(HOST));
    await expect(loadStoreContext(HOST)).resolves.toEqual(CONTEXT);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
