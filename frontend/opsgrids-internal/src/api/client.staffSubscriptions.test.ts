import { afterEach, describe, expect, it, vi } from "vitest";

import { addProduct, extendTrial, getAddProductOptions } from "./client";
import { ApiError } from "./http";

/**
 * The Add product and Extend trial client in live mode (`vitest.config.ts` pins
 * `VITE_USE_FIXTURES=false`): the admin-gateway paths ADR-0030 names, the idempotency key as the
 * header, the body as typed — and a coded refusal reaching the screen intact.
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

function header(init: RequestInit, name: string): string | null {
  return new Headers(init.headers).get(name);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getAddProductOptions", () => {
  it("reads the documented route", async () => {
    const fetchMock = stubFetch({ organization_id: "org_1", currency: null, existing: [], products: [] });
    await getAddProductOptions("org/1");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org%2F1/add-product-options");
    expect(init.method).toBe("GET");
  });
});

describe("addProduct", () => {
  const REQUEST = {
    product_key: "crm",
    plan_version_id: "pv_1",
    trial: true,
    acknowledge_immediate_charge: false,
    reason: "onboarding",
  };

  it("POSTs the body to the subscriptions collection with the key as a header", async () => {
    const fetchMock = stubFetch({ subscription_id: "s" }, 201);
    await addProduct("org_1", REQUEST, "key-123456");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/subscriptions");
    expect(init.method).toBe("POST");
    expect(header(init, "Idempotency-Key")).toBe("key-123456");
    expect(JSON.parse(String(init.body))).toEqual(REQUEST);
  });

  it("a coded refusal arrives with its status, code, sentence and reference", async () => {
    stubFetch(
      {
        status: 409,
        detail: "this organization already has a subscription for 'crm' (cancelled)",
        request_id: "req-9",
        errors: [{ code: "already_subscribed", message: "x" }],
      },
      409,
    );
    const error = await addProduct("org_1", REQUEST, "key-123456").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const api = error as ApiError;
    expect([api.status, api.code, api.requestId]).toEqual([409, "already_subscribed", "req-9"]);
    expect(api.message).toMatch(/already has a subscription/);
  });
});

describe("extendTrial", () => {
  it("POSTs absolute instants to the product's extend-trial route with the key", async () => {
    const fetchMock = stubFetch({ trial_ends_at: "2026-11-03T18:29:59Z" });
    const body = {
      new_trial_ends_at: "2026-11-03T23:59:59+05:30",
      expected_trial_ends_at: "2026-10-20T09:00:00.123456Z",
      reason: "migration slipped",
    };
    await extendTrial("org_1", "hrms", body, "key-abcdef");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/subscriptions/hrms/extend-trial");
    expect(init.method).toBe("POST");
    expect(header(init, "Idempotency-Key")).toBe("key-abcdef");
    // The end the operator saw travels exactly as it was read — microseconds and all.
    expect(JSON.parse(String(init.body))).toEqual(body);
  });
});
