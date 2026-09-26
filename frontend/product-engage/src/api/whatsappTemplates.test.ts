import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createTemplate,
  getTemplate,
  listTemplates,
  previewTemplate,
  syncTemplates,
} from "./whatsappTemplates";

/**
 * The W2 wire: path, method and body, because a path off by a segment fails as a 404 the screen can
 * only report as "couldn't load", and a body the service refuses is a 422 on every submit.
 */

function stubFetch(body: unknown = {}, status = 200) {
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

describe("listTemplates", () => {
  it("reads the plain list with no query when nothing is filtered", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 1, size: 20, pages: 0 });
    await listTemplates();
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/whatsapp/templates");
    expect(init.method).toBe("GET");
  });

  it("sends category, status, q and page, and leaves blanks and page 1 off", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 2, size: 20, pages: 0 });
    await listTemplates({ category: "marketing", status: "approved", q: "  ship ", page: 2, size: 20 });
    const [url] = call(fetchMock);
    const params = new URL(url, "http://x").searchParams;
    expect(params.get("category")).toBe("marketing");
    expect(params.get("status")).toBe("approved");
    expect(params.get("q")).toBe("ship");
    expect(params.get("page")).toBe("2");
    expect(params.get("size")).toBe("20");

    const second = stubFetch({ items: [], total: 0, page: 1, size: 20, pages: 0 });
    await listTemplates({ q: "   ", page: 1 });
    expect(call(second)[0]).toBe("/api/v1/engage/whatsapp/templates");
  });
});

describe("per-template calls", () => {
  it("reads one template by its id, encoded", async () => {
    const fetchMock = stubFetch({});
    await getTemplate("weird name/1");
    expect(call(fetchMock)[0]).toBe("/api/v1/engage/whatsapp/templates/weird%20name%2F1");
  });

  it("posts the values to the preview route", async () => {
    const fetchMock = stubFetch({ text: "Hi Rahul", buttons: [] });
    await previewTemplate("order_shipped", { "1": "Rahul" });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/whatsapp/templates/order_shipped/preview");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ values: { "1": "Rahul" } });
  });
});

describe("writes", () => {
  it("syncs with a bodyless POST", async () => {
    const fetchMock = stubFetch({ synced: 1, created: 0, updated: 1, removed: 0 });
    await syncTemplates();
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/whatsapp/templates/sync");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("creates with the contract's body, verbatim", async () => {
    const fetchMock = stubFetch({}, 201);
    const input = {
      name: "order_shipped",
      category: "utility" as const,
      language: "en_US",
      components: { body: { text: "Hi {{1}}", examples: ["Rahul"] } },
    };
    await createTemplate(input);
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/whatsapp/templates");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(input);
  });
});
