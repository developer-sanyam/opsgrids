/**
 * Test-only helpers for the entity definitions. The spreadsheet engine lives in `@opsgrids/client`
 * and is not under test here; `parseRow` stands in for its per-column coercion just far enough to
 * turn a sample row (what the sheet shows) into the `ParsedRow` `toPayload` receives, applying the
 * checks a column declares (required, length, pattern, bounds, allowed values). `stubGateway`
 * answers the API calls an entity makes, so `loadLookups`, `create` and the export are exercised
 * through the app's real api client.
 */
import { vi } from "vitest";

import type {
  ImportColumn,
  ImportContext,
  LookupSet,
  ParsedRow,
  ParsedValue,
  SheetValue,
} from "@opsgrids/client";

export function contextWith(lookups: LookupSet = {}): ImportContext {
  return { lookups, signal: new AbortController().signal };
}

/** An optional part of an entity (its lookups, its export) that a test needs to be there. */
export function present<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new Error(`${what} is not defined`);
  return value;
}

function coerce(column: ImportColumn, cell: SheetValue): ParsedValue {
  const fail = (why: string): never => {
    throw new Error(`${column.header}: ${why} (${String(cell)})`);
  };
  const text = typeof cell === "string" ? cell.trim() : String(cell);
  switch (column.kind) {
    case "text":
    case "email":
    case "phone":
    case "date": {
      if (column.maxLength !== undefined && text.length > column.maxLength) fail("too long");
      if (column.pattern !== undefined && !column.pattern.test(text)) fail("does not match");
      if (column.kind === "email") return text.toLowerCase();
      if (column.kind === "phone") return text.replace(/[^+\d]/g, "");
      if (column.kind === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(text)) fail("not an ISO date");
      return text;
    }
    case "enum": {
      const option = column.options?.find(
        (o) => o.value.toLowerCase() === text.toLowerCase() || o.label.toLowerCase() === text.toLowerCase(),
      );
      return option?.value ?? fail("not an allowed value");
    }
    case "integer": {
      const n = Number(text);
      if (!Number.isInteger(n)) fail("not a whole number");
      if (column.min !== undefined && n < column.min) fail("below the minimum");
      if (column.max !== undefined && n > column.max) fail("above the maximum");
      return n;
    }
    case "money": {
      // Two-decimal currencies only: enough for the sample rows, which are all INR.
      const [whole = "", fraction = ""] = text.split(".");
      if (!/^\d+$/.test(whole) || !/^\d{0,2}$/.test(fraction)) fail("not an amount");
      const minor = Number(`${whole}${fraction.padEnd(2, "0")}`);
      if (column.min !== undefined && minor < column.min * 100) fail("below the minimum");
      return minor;
    }
    default:
      return fail("kind not supported by the test kit");
  }
}

/** A sheet row (by column key) as `toPayload` receives it. Read-only columns are dropped, blank
 *  optional cells are null, and a blank required cell fails the test. */
export function parseRow(
  columns: readonly ImportColumn[],
  cells: Readonly<Record<string, SheetValue>>,
): ParsedRow {
  const row: Record<string, ParsedValue> = {};
  for (const column of columns) {
    if (column.readOnly === true) continue;
    const cell = cells[column.key];
    const blank = cell === undefined || cell === null || (typeof cell === "string" && cell.trim() === "");
    if (blank) {
      if (column.required === true) throw new Error(`${column.header} is required`);
      row[column.key] = null;
      continue;
    }
    row[column.key] = coerce(column, cell);
  }
  return row;
}

/* ------------------------------- a fake gateway ------------------------------- */

export interface Recorded {
  url: URL;
  method: string;
  body: unknown;
  signal: AbortSignal | null | undefined;
}

/** Stubs `fetch` with a handler that answers by request, and records every request. Pair it with
 *  `vi.unstubAllGlobals()` in `afterEach`. */
export function stubGateway(handler: (request: Recorded) => unknown, status = 200): Recorded[] {
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
      return new Response(JSON.stringify(handler(request)), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return recorded;
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
