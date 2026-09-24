import { afterEach, describe, expect, it, vi } from "vitest";

import { getOrganizationPricing, setOrganizationPrice } from "./client";

/**
 * This app defaults to fixtures mode in dev (`USE_FIXTURES = import.meta.env.DEV &&
 * VITE_USE_FIXTURES !== "false"`, opt-*out*, unlike the product apps' opt-in) so the whole console
 * renders with no admin-gateway while its backend is built. `USE_FIXTURES` is read once at module
 * load, so it cannot be flipped per test with `vi.stubEnv` — `vitest.config.ts` pins
 * `VITE_USE_FIXTURES=false` for the whole suite instead, which is also what makes this file
 * exercise the real live-fetch branch: the same one a production build takes.
 *
 * What is pinned: the admin-gateway path the contract names, the HTTP method, and — for the write —
 * that the idempotency key the caller passes is the header the request actually carries, not just
 * a value threaded through and dropped.
 */

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
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

describe("getOrganizationPricing", () => {
  it("reads the org's pricing from the documented admin route", async () => {
    const fetchMock = stubFetch({ organization_id: "org_1", products: [] });
    await getOrganizationPricing("org_1");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/pricing");
    expect(init.method).toBe("GET");
  });

  it("encodes an organization id that needs it", async () => {
    const fetchMock = stubFetch({ organization_id: "org/weird", products: [] });
    await getOrganizationPricing("org/weird");
    expect(call(fetchMock)[0]).toBe("/api/v1/admin/organizations/org%2Fweird/pricing");
  });
});

describe("setOrganizationPrice", () => {
  const request = {
    product_key: "crm",
    amount_minor: 249_950,
    currency: "INR" as const,
    billing_interval: "month" as const,
    reason: "Negotiated during the renewal call",
    apply_to_subscription: true,
  };

  it("POSTs to the org's pricing route with the idempotency key as a header", async () => {
    const fetchMock = stubFetch({ plan_version_id: "pv_1", applied_to_subscription_id: "sub_1", effective: "now" });
    await setOrganizationPrice("org_1", request, "5b1e6b0a-1111-4a2b-9c3d-000000000001");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/pricing");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe(
      "5b1e6b0a-1111-4a2b-9c3d-000000000001",
    );
    expect(JSON.parse(init.body as string)).toEqual(request);
  });

  it("sends a different idempotency key as a different header value, unchanged by the client", async () => {
    const fetchMock = stubFetch({ plan_version_id: "pv_2", applied_to_subscription_id: null, effective: "not_subscribed" });
    await setOrganizationPrice("org_1", request, "aaaaaaaa-1111-4a2b-9c3d-000000000002");
    expect((call(fetchMock)[1].headers as Record<string, string>)["Idempotency-Key"]).toBe(
      "aaaaaaaa-1111-4a2b-9c3d-000000000002",
    );
  });
});
