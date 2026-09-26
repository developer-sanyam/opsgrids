/**
 * What every CRM entity definition must hold to, so the sample sheet (built from these same
 * definitions) can never disagree with the importer: distinct keys and headers, no read-only
 * column in a sample, every required column filled, and sample rows that pass the entity's own
 * rules against the reference data a real organisation would have.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getMySubjectRef } from "@/lib/subject";
import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
  contextWith,
  pageOf,
  parseRow,
  stubGateway,
} from "./entityTestKit";
import { leadImport } from "./leads";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

/** What the fake gateway holds: the companies and contacts the sample rows name. */
function gateway() {
  return stubGateway(({ url }) => {
    if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
    if (url.pathname.endsWith("/customers")) {
      return pageOf([
        companyWire("comp-1", "Northwind Logistics"),
        companyWire("comp-2", "Cobalt Financial"),
      ]);
    }
    if (url.pathname.endsWith("/contacts")) {
      return pageOf([
        contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
        contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
      ]);
    }
    throw new Error(`unexpected request ${url.pathname}`);
  });
}

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ENTITIES = [
  { name: "companies", entity: companyImport, id: "crm.companies", noun: "companies" },
  { name: "contacts", entity: contactImport, id: "crm.contacts", noun: "contacts" },
  { name: "leads", entity: leadImport, id: "crm.leads", noun: "leads" },
  { name: "deals", entity: dealImport, id: "crm.deals", noun: "deals" },
] as const;

describe.each(ENTITIES)("$name entity definition", ({ entity, id, noun }) => {
  it("is named for the dialog and the files", () => {
    expect(entity.id).toBe(id);
    expect(entity.noun).toBe(noun);
    expect(entity.fileStem).toBe(noun);
  });

  it("gives every column its own key and its own header", () => {
    const keys = entity.columns.map((c) => c.key);
    const headers = entity.columns.map((c) => c.header.toLowerCase());
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(headers).size).toBe(headers.length);
  });

  it("describes every column for the Instructions sheet", () => {
    for (const column of entity.columns) {
      expect(column.help.trim(), column.header).not.toBe("");
    }
  });

  it("marks lookup columns as plain text and enum columns with their options", () => {
    for (const column of entity.columns) {
      if (column.lookup !== undefined) expect(column.kind, column.header).toBe("text");
      if (column.kind === "enum") expect(column.options?.length, column.header).toBeGreaterThan(0);
    }
  });

  it("loads every lookup its columns name", async () => {
    gateway();
    const lookups = await entity.loadLookups?.(new AbortController().signal);
    const named = entity.columns.flatMap((c) => (c.lookup === undefined ? [] : [c.lookup]));
    expect(Object.keys(lookups ?? {}).sort()).toEqual([...new Set(named)].sort());
  });

  it("keeps read-only columns out of the sample rows", () => {
    const readOnly = new Set(entity.columns.filter((c) => c.readOnly === true).map((c) => c.key));
    const known = new Set(entity.columns.map((c) => c.key));
    for (const sample of entity.sampleRows) {
      for (const key of Object.keys(sample)) {
        expect(known.has(key), `${key} is not a column`).toBe(true);
        expect(readOnly.has(key), `${key} is read-only`).toBe(false);
      }
    }
  });

  it("fills every required column in every sample row", () => {
    const required = entity.columns.filter((c) => c.required === true && c.readOnly !== true);
    expect(required.length).toBeGreaterThan(0);
    for (const sample of entity.sampleRows) {
      for (const column of required) {
        expect(sample[column.key], column.header).toBeTruthy();
      }
    }
  });

  it("has two or three realistic sample rows that pass the entity's own rules", async () => {
    expect(entity.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(entity.sampleRows.length).toBeLessThanOrEqual(3);
    gateway();
    const ctx = contextWith(await entity.loadLookups?.(new AbortController().signal));
    for (const sample of entity.sampleRows) {
      const row = parseRow(entity.columns, sample);
      expect(() => entity.toPayload(row, ctx)).not.toThrow();
    }
  });

  it("states the rules a user has to know", () => {
    expect(entity.notes?.length).toBeGreaterThan(0);
  });

  it("can be exported", () => {
    expect(entity.export).toBeDefined();
  });
});
