import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import {
  getStats,
  listAutomations,
  listEmailTemplateOptions,
  listRuns,
  listTriggers,
  listWhatsAppTemplateOptions,
  problemsFrom,
  retryRun,
} from "./automations";

/**
 * The client's own decisions: which query a filter becomes, how the two template shapes the
 * service can return are read, and how a refusal's problems are recovered. The paths are the A1
 * contract's, so a rename on either side fails here rather than at a customer.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calledUrl(fetchMock: ReturnType<typeof stubFetch>): URL {
  const first = fetchMock.mock.calls[0] as [string, RequestInit];
  return new URL(first[0], "https://example.test");
}

describe("requests", () => {
  it("sends only the filters that were set", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listAutomations({ status: "paused", module: "", q: "  invoice ", page: 2 });
    const url = calledUrl(fetchMock);
    expect(url.pathname).toBe("/api/v1/engage/automations");
    expect(Object.fromEntries(url.searchParams)).toEqual({ status: "paused", q: "invoice", page: "2", size: "25" });
  });

  it("reads the trigger catalogue's items", async () => {
    const fetchMock = stubFetch({ items: [{ event_type: "a", label: "A", module: "M", variables: [] }] });
    expect(await listTriggers()).toHaveLength(1);
    expect(calledUrl(fetchMock).pathname).toBe("/api/v1/engage/automations/triggers");
  });

  it("filters runs by state and stats by range", async () => {
    let fetchMock = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listRuns("abc", { state: "failed" });
    expect(calledUrl(fetchMock).pathname).toBe("/api/v1/engage/automations/abc/runs");
    expect(calledUrl(fetchMock).searchParams.get("state")).toBe("failed");
    fetchMock = stubFetch({});
    await getStats({ from: "2026-09-01", to: "2026-09-30" });
    expect(calledUrl(fetchMock).searchParams.get("from")).toBe("2026-09-01");
    expect(calledUrl(fetchMock).searchParams.get("to")).toBe("2026-09-30");
  });

  it("retries a run with a POST to its own path", async () => {
    const fetchMock = stubFetch({ ok: true });
    await retryRun("run 1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/engage/automations/runs/run%201/retry");
    expect(init.method).toBe("POST");
  });
});

describe("template options", () => {
  it("reads the W2 WhatsApp shape and offers only approved templates", async () => {
    stubFetch({
      items: [
        { name: "order_shipped", language: "en", status: "approved", body_preview: "Hi {{1}}", variables: [{ index: 1, name: "name", kind: "text", example: "Rahul" }] },
        { name: "still_pending", language: "en", status: "pending", body_preview: "", variables: [] },
      ],
      total: 2, page: 1, size: 200, pages: 1,
    });
    expect(await listWhatsAppTemplateOptions()).toEqual([
      { ref: "order_shipped", label: "order_shipped", language: "en", preview: "Hi {{1}}", variables: ["1"] },
    ]);
  });

  it("reads the earlier WhatsApp shape too", async () => {
    stubFetch({
      items: [
        { provider_name: "legacy", language: "en", status: "approved", preview_text: "Hi", variables: [{ key: "name", position: 1 }] },
      ],
      total: 1, page: 1, size: 200, pages: 1,
    });
    expect(await listWhatsAppTemplateOptions()).toEqual([
      { ref: "legacy", label: "legacy", language: "en", preview: "Hi", variables: ["1"] },
    ]);
  });

  it("reads email templates with their placeholders as variables", async () => {
    stubFetch({
      items: [{ id: "order_shipped", subject: "Your order shipped", description: "", placeholders: ["order_number"] }],
      total: 1, page: 1, size: 200, pages: 1,
    });
    expect(await listEmailTemplateOptions()).toEqual([
      { ref: "order_shipped", label: "order_shipped", language: null, preview: "Your order shipped", variables: ["order_number"] },
    ]);
  });
});

describe("problemsFrom", () => {
  it("recovers the contract's problems from a 422", () => {
    const problem = { problems: [{ node_id: "wait_1", code: "x", message: "Fix it." }, { node_id: null, code: "y", message: "Flow-wide." }, { nope: true }] } as unknown as ProblemJson;
    expect(problemsFrom(new ApiError(422, "Invalid", problem))).toEqual([
      { node_id: "wait_1", code: "x", message: "Fix it." },
      { node_id: null, code: "y", message: "Flow-wide." },
    ]);
  });

  it("reads plain field errors as problems that belong to no step", () => {
    const problem: ProblemJson = { errors: [{ field: "name", code: "required", message: "Name is required." }] };
    expect(problemsFrom(new ApiError(422, "Invalid", problem))).toEqual([
      { node_id: null, code: "required", message: "Name is required." },
    ]);
  });

  it("is empty for anything that carries none", () => {
    expect(problemsFrom(new ApiError(500, "Boom", null))).toEqual([]);
    expect(problemsFrom(new Error("network"))).toEqual([]);
  });
});
