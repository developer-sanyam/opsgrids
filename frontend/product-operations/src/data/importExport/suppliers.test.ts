import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportContext, ParsedRow } from "@opsgrids/client";

import type { SupplierOut } from "@/api/types";
import { pageOf, stubGateway } from "./entityTestKit";
import { supplierImport } from "./suppliers";

afterEach(() => {
  vi.unstubAllGlobals();
});

const CONTEXT: ImportContext = { lookups: {}, signal: new AbortController().signal };

function exportSource() {
  if (supplierImport.export === undefined) throw new Error("suppliers have no export");
  return supplierImport.export;
}

const MINIMAL_ROW: ParsedRow = {
  code: "SUP-MER",
  name: "Meridian Metals",
  category: "raw_materials",
};

describe("supplier columns", () => {
  it("names them in plain business English, importable ones first and read-only ones last", () => {
    expect(supplierImport.columns.map((c) => c.header)).toEqual([
      "Supplier code",
      "Supplier name",
      "Category",
      "Status",
      "Lead time (days)",
      "Currency",
      "Contact name",
      "Contact email",
      "Location",
      "Supplier since",
      "Lifetime spend",
      "Open purchase orders",
      "On-time delivery (%)",
      "ID",
    ]);
  });

  it("requires exactly what the API requires", () => {
    const required = supplierImport.columns.filter((c) => c.required === true).map((c) => c.key);
    expect(required).toEqual(["code", "name", "category"]);
  });

  it("keeps the rollups the service maintains read-only, so they can never be imported", () => {
    const readOnly = supplierImport.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(readOnly).toEqual(["spend", "open_pos", "on_time_pct", "id"]);
  });

  it("offers the supplier statuses under the labels the screen uses", () => {
    const status = supplierImport.columns.find((c) => c.key === "status");
    expect(status?.options).toEqual([
      { value: "active", label: "Active" },
      { value: "on_hold", label: "On hold" },
      { value: "pending", label: "Pending" },
    ]);
  });

  it("names its id column as the record id, so a row that came out of an export is not added again", () => {
    expect(supplierImport.recordIdKey).toBe("id");
    expect(supplierImport.columns.find((c) => c.key === supplierImport.recordIdKey)?.readOnly).toBe(true);
  });

  it("holds the lead time to the API's 0 to 365 days", () => {
    const leadTime = supplierImport.columns.find((c) => c.key === "lead_time_days");
    expect(leadTime).toMatchObject({ kind: "integer", min: 0, max: 365 });
  });
});

describe("supplier sample rows", () => {
  // Whether they pass the column rules and turn into a payload is `checkEntities.test.ts`'s job.
  it("has two or three of them", () => {
    expect(supplierImport.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(supplierImport.sampleRows.length).toBeLessThanOrEqual(3);
  });
});

describe("supplier toPayload", () => {
  it("sends only what the row says, so the API's own defaults still apply", () => {
    expect(supplierImport.toPayload(MINIMAL_ROW, CONTEXT)).toStrictEqual({
      code: "SUP-MER",
      name: "Meridian Metals",
      category: "raw_materials",
    });
  });

  it("maps every column to the field the API takes", () => {
    const row: ParsedRow = {
      ...MINIMAL_ROW,
      status: "on_hold",
      lead_time_days: 0,
      currency: "usd",
      contact_name: "Rajesh Pillai",
      contact_email: "rajesh@meridianmetals.example",
      location: "Jamshedpur",
      since: "2024-04-02",
    };
    expect(supplierImport.toPayload(row, CONTEXT)).toStrictEqual({
      code: "SUP-MER",
      name: "Meridian Metals",
      category: "raw_materials",
      status: "on_hold",
      lead_time_days: 0,
      currency: "USD",
      contact_name: "Rajesh Pillai",
      contact_email: "rajesh@meridianmetals.example",
      location: "Jamshedpur",
      since: "2024-04-02",
    });
  });

  it("never sends the rollups, even when a sheet carries them", () => {
    const row: ParsedRow = { ...MINIMAL_ROW, spend: 9_900_000, open_pos: 4, on_time_pct: 96.4, id: "sup-1" };
    const payload = supplierImport.toPayload(row, CONTEXT);
    expect(Object.keys(payload)).toEqual(["code", "name", "category"]);
  });

  it("refuses a missing required cell", () => {
    expect(() => supplierImport.toPayload({ ...MINIMAL_ROW, name: null }, CONTEXT)).toThrow(
      /Supplier name is required/,
    );
  });
});

describe("supplier create", () => {
  it("posts the payload to the suppliers endpoint and reports the new id", async () => {
    const recorded = stubGateway(() => ({ body: { id: "sup-9" }, status: 201 }));

    const result = await supplierImport.create(supplierImport.toPayload(MINIMAL_ROW, CONTEXT), CONTEXT);

    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/suppliers");
    expect(recorded[0]?.init.method).toBe("POST");
    expect(JSON.parse(String(recorded[0]?.init.body))).toEqual({
      code: "SUP-MER",
      name: "Meridian Metals",
      category: "raw_materials",
    });
    expect(result).toEqual({ id: "sup-9" });
  });
});

describe("supplier export", () => {
  const SUPPLIER: SupplierOut = {
    id: "sup-1",
    code: "SUP-MER",
    name: "Meridian Metals",
    category: "raw_materials",
    status: "on_hold",
    lead_time_days: 12,
    on_time_pct: 96.4,
    spend_minor: 412_000_000,
    currency: "INR",
    open_pos: 3,
    contact_name: "Rajesh Pillai",
    contact_email: "rajesh@meridianmetals.example",
    location: "Jamshedpur",
    since: "2019-04-02",
  };

  it("asks for the page under the screen's current filters, not just the visible page", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageOf([], 3, 4) }));

    await exportSource().fetchPage({
      page: 3,
      size: 200,
      signal: controller.signal,
      filters: { q: "meridian", category: "raw_materials", status: "all", sort: "spend" },
    });

    const params = recorded[0]?.url.searchParams;
    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/suppliers");
    expect(params?.get("page")).toBe("3");
    expect(params?.get("size")).toBe("200");
    expect(params?.get("q")).toBe("meridian");
    expect(params?.get("category")).toBe("raw_materials");
    expect(params?.get("sort")).toBe("spend");
    expect(params?.has("status")).toBe(false);
    expect(recorded[0]?.init.signal).toBe(controller.signal);
  });

  it("writes one sheet row per supplier, with labels for enums and money in major units", () => {
    expect(exportSource().toRow(SUPPLIER, {})).toStrictEqual({
      code: "SUP-MER",
      name: "Meridian Metals",
      category: "Raw materials",
      status: "On hold",
      lead_time_days: 12,
      currency: "INR",
      contact_name: "Rajesh Pillai",
      contact_email: "rajesh@meridianmetals.example",
      location: "Jamshedpur",
      since: "2019-04-02",
      spend: "4120000.00",
      open_pos: 3,
      on_time_pct: 96.4,
      id: "sup-1",
    });
  });

  it("leaves empty optional cells blank and writes a cell for every column", () => {
    const row = exportSource().toRow(
      { ...SUPPLIER, contact_name: "", contact_email: "", location: "", currency: "JPY", spend_minor: 5000 },
      {},
    );
    expect(row.contact_name).toBeNull();
    expect(row.contact_email).toBeNull();
    expect(row.location).toBeNull();
    expect(row.spend).toBe("5000");
    const missing = supplierImport.columns.map((c) => c.key).filter((key) => !(key in row));
    expect(missing).toEqual([]);
  });
});
