/**
 * Test-only helpers for the entity definitions. The spreadsheet engine lives in `@opsgrids/client`
 * and is not under test here: whether a definition holds together is `checkImportEntity`'s call
 * (`checkEntities.test.ts`), and how cells are coerced is the engine's own. `parsedRow` builds the
 * row `toPayload` receives, `stubGateway` answers the API calls an entity makes, so `loadLookups`,
 * `create` and the export are exercised through the app's real api client.
 */
import { vi } from "vitest";

import type { ImportColumn, ImportContext, LookupSet, ParsedRow, ParsedValue } from "@opsgrids/client";

export function contextWith(lookups: LookupSet = {}): ImportContext {
  return { lookups, signal: new AbortController().signal };
}

/** An optional part of an entity (its lookups, its export) that a test needs to be there. */
export function present<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new Error(`${what} is not defined`);
  return value;
}

/**
 * A row as `toPayload` receives it from the engine. The engine has already trimmed, checked and
 * converted every cell, so `values` holds what a coerced row holds (an enum's `value`, money in
 * integer minor units, an email in lower case, a phone as `+` and digits), and a cell the sheet
 * left blank is `null`. A key that is not an importable column of `columns` fails the test.
 */
export function parsedRow(
  columns: readonly ImportColumn[],
  values: Readonly<Record<string, ParsedValue>>,
): ParsedRow {
  const importable = columns.filter((column) => column.readOnly !== true);
  for (const key of Object.keys(values)) {
    if (!importable.some((column) => column.key === key)) throw new Error(`"${key}" is not an importable column`);
  }
  return Object.fromEntries(importable.map((column) => [column.key, values[column.key] ?? null]));
}

/* ------------------------------- a fake gateway ------------------------------- */

export interface Recorded {
  url: URL;
  method: string;
  body: unknown;
  signal: AbortSignal | null | undefined;
}

/** What the fake gateway answers: a body, and the status it comes with (200 unless said). */
export interface Reply {
  body: unknown;
  status?: number;
}

/** Stubs `fetch` with a handler that answers each request, and records every request. Pair it
 *  with `vi.unstubAllGlobals()` in `afterEach`. */
export function stubApi(handler: (request: Recorded) => Reply): Recorded[] {
  const recorded: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const request: Recorded = {
        url: new URL(input, "http://localhost"),
        method: init.method ?? "GET",
        body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
        signal: init.signal,
      };
      recorded.push(request);
      const { body, status = 200 } = handler(request);
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return recorded;
}

/** `stubApi` for the common case of one status for every answer. */
export function stubGateway(handler: (request: Recorded) => unknown, status = 200): Recorded[] {
  return stubApi((request) => ({ body: handler(request), status }));
}

/** A page envelope over `items`, as every list endpoint returns it. */
export function pageOf<T>(items: readonly T[], page = 1, size = 25, total = items.length) {
  return { items, total, page, size, pages: Math.ceil(total / size) };
}

export const OWNERS_WIRE = [
  { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true },
  { id: "own-2", full_name: "Grace Adeyemi", email: "Grace@Example.com", role: "AE", is_active: true },
  { id: "own-3", full_name: "Former Rep", email: "former@example.com", role: "AE", is_active: false },
];

export function companyWire(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    name,
    domain: "",
    industry: "",
    size_band: "",
    city: "",
    country: "",
    status: "prospect",
    owner_id: "own-1",
    annual_revenue_minor: 0,
    currency: "INR",
    created_at: "2026-01-05T09:00:00Z",
    ...extra,
  };
}

export function contactWire(id: string, email: string, companyId: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    full_name: "Someone",
    title: "",
    email,
    phone: "+•• •••• ••210",
    company_id: companyId,
    status: "lead",
    owner_id: "own-1",
    last_activity_at: null,
    created_at: "2026-01-06T09:00:00Z",
    ...extra,
  };
}

export function dealWire(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    name,
    company_id: "comp-1",
    primary_contact_id: null,
    value_minor: 320000000,
    currency: "INR",
    stage: "lead",
    owner_id: "own-1",
    probability: 20,
    close_date: "2026-12-15",
    created_at: "2026-09-27T09:00:00Z",
    last_activity_at: null,
    source: "",
    ...extra,
  };
}

export function leadWire(id: string, fullName: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    full_name: fullName,
    company: null,
    email: "m•••@h•••.in",
    phone: "+•• ••••• ••345",
    source: "import",
    status: "new",
    owner_id: null,
    notes: "",
    converted_company_id: null,
    converted_contact_id: null,
    converted_deal_id: null,
    converted_at: null,
    created_at: "2026-09-20T09:00:00Z",
    ...extra,
  };
}
