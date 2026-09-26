/**
 * Test support for the import definitions. The import engine lives in `@opsgrids/client` and these
 * tests must not depend on it, so this applies each column's own rules (required, pattern, bounds,
 * enum, lookup) to a row the way the engine will, and coerces a sample row into the `ParsedRow`
 * `toPayload` receives. A sample row that breaks its own entity's rules fails a test here rather
 * than a customer's first import.
 */
import { vi } from "vitest";

import type { ImportColumn, LookupSet, ParsedRow, ParsedValue, SheetValue } from "@opsgrids/client";

interface SampleSource {
  columns: readonly ImportColumn[];
  sampleRows: readonly Record<string, SheetValue>[];
}

function isBlank(value: SheetValue | undefined): boolean {
  return value === undefined || value === null || (typeof value === "string" && value.trim() === "");
}

function matchesOption(column: ImportColumn, value: string): boolean {
  const wanted = value.trim().toLowerCase();
  return (column.options ?? []).some(
    (option) => option.value.toLowerCase() === wanted || option.label.toLowerCase() === wanted,
  );
}

/** Every rule a sample row breaks, as sentences. Empty means the row is valid. */
export function rowProblems(
  source: SampleSource,
  row: Record<string, SheetValue>,
  lookups: LookupSet,
): string[] {
  const problems: string[] = [];
  const byKey = new Map(source.columns.map((column) => [column.key, column]));

  for (const key of Object.keys(row)) {
    const column = byKey.get(key);
    if (column === undefined) problems.push(`${key}: is not a column`);
    else if (column.readOnly === true) problems.push(`${key}: is read-only and must not be in a sample`);
  }

  for (const column of source.columns) {
    if (column.readOnly === true) continue;
    const value = row[column.key];
    if (isBlank(value)) {
      if (column.required === true) problems.push(`${column.key}: is required`);
      continue;
    }
    const text = String(value).trim();
    if (column.maxLength !== undefined && text.length > column.maxLength) {
      problems.push(`${column.key}: is longer than ${column.maxLength}`);
    }
    if (column.pattern !== undefined && !column.pattern.test(text)) {
      problems.push(`${column.key}: "${text}" does not match ${String(column.pattern)}`);
    }
    if (column.kind === "integer") {
      const n = Number(value);
      if (!Number.isInteger(n)) problems.push(`${column.key}: is not a whole number`);
      if (column.min !== undefined && n < column.min) problems.push(`${column.key}: is below ${column.min}`);
      if (column.max !== undefined && n > column.max) problems.push(`${column.key}: is above ${column.max}`);
    }
    if (column.kind === "money" && !/^\d+(\.\d+)?$/.test(text)) {
      problems.push(`${column.key}: "${text}" is not a decimal amount`);
    }
    if (column.kind === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(text)) {
      problems.push(`${column.key}: "${text}" is not a YYYY-MM-DD date`);
    }
    if (column.kind === "email" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(text)) {
      problems.push(`${column.key}: "${text}" is not an email address`);
    }
    if (column.kind === "boolean" && typeof value !== "boolean") {
      problems.push(`${column.key}: is not a yes/no value`);
    }
    if (column.kind === "enum" && !matchesOption(column, text)) {
      problems.push(`${column.key}: "${text}" is not one of the options`);
    }
    if (column.lookup !== undefined) {
      const lookup = lookups[column.lookup];
      if (lookup === undefined || lookup.resolve(text) === undefined) {
        problems.push(`${column.key}: "${text}" is not in the ${column.lookup} lookup`);
      }
    }
  }
  return problems;
}

/** Minor-unit digits for a money cell in a currency with two decimals (the sample currency). */
function toMinor(text: string): number {
  const [whole = "0", fraction = ""] = text.split(".");
  return Number(`${whole}${fraction.padEnd(2, "0")}`);
}

/** What the engine hands `toPayload` for a sheet row: enums as the option's `value`, money as
 *  integer minor units, blanks as null. Only sample-shaped values are supported. */
export function parseRow(source: SampleSource, row: Record<string, SheetValue>): ParsedRow {
  const parsed: Record<string, ParsedValue> = {};
  for (const column of source.columns) {
    if (column.readOnly === true) continue;
    const value = row[column.key];
    if (isBlank(value)) {
      parsed[column.key] = null;
      continue;
    }
    const text = String(value).trim();
    if (column.kind === "enum") {
      const wanted = text.toLowerCase();
      const option = (column.options ?? []).find(
        (o) => o.value.toLowerCase() === wanted || o.label.toLowerCase() === wanted,
      );
      parsed[column.key] = option?.value ?? text;
    } else if (column.kind === "money") {
      parsed[column.key] = toMinor(text);
    } else if (column.kind === "integer" || column.kind === "decimal") {
      parsed[column.key] = Number(value);
    } else if (column.kind === "boolean") {
      parsed[column.key] = value === true;
    } else {
      parsed[column.key] = text;
    }
  }
  return parsed;
}

/* ------------------------------ a stubbed gateway ------------------------------ */

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
