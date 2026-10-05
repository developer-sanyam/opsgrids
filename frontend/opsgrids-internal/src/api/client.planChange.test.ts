import { afterEach, describe, expect, it, vi } from "vitest";

import { changePlan, getPlanOptions, previewPlanChange } from "./client";
import { ApiError } from "./http";

/**
 * The plan-switching client in live mode (`vitest.config.ts` pins `VITE_USE_FIXTURES=false`, so
 * this takes the same fetch branch a production build does): the admin-gateway paths the contract
 * names, the colon on the preview route, that the idempotency key is the header the request
 * carries — and that a coded refusal reaches the screen with its status, code, sentence and
 * reference intact.
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

const REQUEST = {
  plan_version_id: "pv_1",
  timing: "auto" as const,
  reason: "agreed",
  expected: { state: "active", plan_version_id: "pv_0", pending_plan_version_id: null },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getPlanOptions", () => {
  it("reads the plan options from the documented route with the product as a query value", async () => {
    const fetchMock = stubFetch({ organization_id: "org_1", product_key: "hrms", subscription: {}, options: [] });
    await getPlanOptions("org_1", "hrms");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/plan-options?product_key=hrms");
    expect(init.method).toBe("GET");
  });

  it("encodes an organization id and a product key that need it", async () => {
    const fetchMock = stubFetch({ options: [] });
    await getPlanOptions("org/weird", "a b");
    expect(call(fetchMock)[0]).toBe("/api/v1/admin/organizations/org%2Fweird/plan-options?product_key=a%20b");
  });
});

describe("previewPlanChange", () => {
  it("POSTs to the colon route, with a body and no idempotency key", async () => {
    const fetchMock = stubFetch({ refusal_code: null });
    await previewPlanChange("org_1", "hrms", { plan_version_id: "pv_1", timing: "auto" });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/subscriptions/hrms/plan-change:preview");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ plan_version_id: "pv_1", timing: "auto" });
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
  });

  it("carries no code when the server gave none", async () => {
    stubFetch({ status: 502, detail: "Bad gateway" }, 502);
    const error = (await previewPlanChange("org_1", "hrms", { plan_version_id: "pv_1", timing: "auto" }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect([error.status, error.code, error.requestId]).toEqual([502, null, null]);
  });
});

describe("changePlan", () => {
  it("POSTs the confirm with the idempotency key as a header", async () => {
    const fetchMock = stubFetch({ effective: "next_period", replayed: false });
    await changePlan("org_1", "hrms", REQUEST, "5b1e6b0a-1111-4a2b-9c3d-000000000003");
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/admin/organizations/org_1/subscriptions/hrms/plan-change");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("5b1e6b0a-1111-4a2b-9c3d-000000000003");
    expect(JSON.parse(init.body as string)).toEqual(REQUEST);
  });

  it("surfaces a coded refusal with its status, code, sentence and reference", async () => {
    stubFetch(
      {
        status: 409,
        detail: "this subscription changed since you looked at it; refresh and review it again",
        request_id: "req-9",
        errors: [{ code: "state_changed", message: "changed" }],
      },
      409,
    );
    const error = await changePlan("org_1", "hrms", REQUEST, "key-0001-0001").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const refusal = error as ApiError;
    expect([refusal.status, refusal.code, refusal.requestId]).toEqual([409, "state_changed", "req-9"]);
    expect(refusal.message).toMatch(/changed since you looked at it/);
  });
});
