/**
 * Test support for the import definitions: a stubbed gateway that answers the api client's
 * requests by URL and records them, the paged-list body every list endpoint returns, and the few
 * reference records the lookups are built from. The rules of a definition (its columns, sample
 * rows and payloads) are checked by the engine's own `checkImportEntity` in
 * `checkEntities.test.ts`.
 */
import { vi } from "vitest";

import type { ItemCategoryOut, UomOut, WarehouseOut } from "@/api/types";

export interface RecordedRequest {
  url: URL;
  init: RequestInit;
}

/**
 * Stubs `fetch` with a handler that answers by URL and records every request, so a test drives the
 * real api client end to end and asserts the method, query and JSON body the gateway would receive.
 */
export function stubGateway(
  respond: (request: RecordedRequest) => { body: unknown; status?: number },
): RecordedRequest[] {
  const recorded: RecordedRequest[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init: RequestInit) => {
      const request = { url: new URL(input, "http://gateway.test"), init };
      recorded.push(request);
      const { body, status = 200 } = respond(request);
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        }),
      );
    }),
  );
  return recorded;
}

export function pageOf<T>(items: T[], page = 1, pages = 1): {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
} {
  return { items, total: items.length, page, size: 200, pages };
}

/** One page of a list held as pages: the body a paged endpoint answers with, `total` counting every page. */
export function pageAt<T>(pages: readonly (readonly T[])[], page: number) {
  return {
    items: pages[page - 1] ?? [],
    total: pages.reduce((sum, items) => sum + items.length, 0),
    page,
    size: 200,
    pages: Math.max(pages.length, 1),
  };
}

/** The requests a stub recorded for one method and path, in the order they were made. */
export function requestsTo(recorded: readonly RecordedRequest[], method: string, path: string): RecordedRequest[] {
  return recorded.filter((request) => (request.init.method ?? "GET") === method && request.url.pathname === path);
}

export function warehouseOut(name: string, code: string): WarehouseOut {
  return { id: `wh-${code}`, code, name, location: "" };
}

export function uomOut(code: string, name: string, isSystem: boolean): UomOut {
  return { id: `uom-${code}`, code, name, dimension: "count", is_discrete: false, is_system: isSystem };
}

export function itemCategoryOut(code: string, id: string, parentId: string | null = null): ItemCategoryOut {
  return { id, code, name: code, parent_id: parentId };
}
