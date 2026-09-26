import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportContext, ParsedRow } from "@opsgrids/client";

import type { WarehouseOut } from "@/api/types";
import { pageOf, stubGateway } from "./entityTestKit";
import { warehouseImport } from "./warehouses";

afterEach(() => {
  vi.unstubAllGlobals();
});

const CONTEXT: ImportContext = { lookups: {}, signal: new AbortController().signal };

function exportSource() {
  if (warehouseImport.export === undefined) throw new Error("warehouses have no export");
  return warehouseImport.export;
}

const ROW: ParsedRow = { code: "WH-MAIN", name: "Main Warehouse", location: "Bhiwandi, Maharashtra" };

describe("warehouse columns", () => {
  it("names them in plain business English", () => {
    expect(warehouseImport.columns.map((c) => c.header)).toEqual([
      "Warehouse code",
      "Warehouse name",
      "Location",
      "ID",
    ]);
  });

  it("requires the code and the name, and keeps the id read-only", () => {
    const required = warehouseImport.columns.filter((c) => c.required === true).map((c) => c.key);
    const readOnly = warehouseImport.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(required).toEqual(["code", "name"]);
    expect(readOnly).toEqual(["id"]);
  });

  it("warns that only people who can create warehouses may import them, and that the name is the key", () => {
    const notes = (warehouseImport.notes ?? []).join(" ");
    expect(notes).toMatch(/allowed to create warehouses/i);
    expect(notes).toMatch(/import warehouses before importing the items/i);
  });
});

describe("warehouse sample rows", () => {
  // Whether they pass the column rules and turn into a payload is `checkEntities.test.ts`'s job.
  it("has two or three of them", () => {
    expect(warehouseImport.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(warehouseImport.sampleRows.length).toBeLessThanOrEqual(3);
  });
});

describe("warehouse toPayload", () => {
  it("leaves the location out when the cell is blank", () => {
    expect(warehouseImport.toPayload({ code: "WH-COLD", name: "Cold Store", location: null }, CONTEXT)).toStrictEqual({
      code: "WH-COLD",
      name: "Cold Store",
    });
  });

  it("trims the cells", () => {
    expect(
      warehouseImport.toPayload({ code: " WH-COLD ", name: " Cold Store ", location: " Nashik " }, CONTEXT),
    ).toStrictEqual({ code: "WH-COLD", name: "Cold Store", location: "Nashik" });
  });

  it("refuses a missing name", () => {
    expect(() => warehouseImport.toPayload({ code: "WH-COLD", name: null }, CONTEXT)).toThrow(
      /Warehouse name is required/,
    );
  });
});

describe("warehouse create", () => {
  it("posts the payload to the warehouses endpoint and reports the new id", async () => {
    const recorded = stubGateway(() => ({ body: { id: "wh-9" }, status: 201 }));

    const result = await warehouseImport.create(warehouseImport.toPayload(ROW, CONTEXT), CONTEXT);

    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/warehouses");
    expect(recorded[0]?.init.method).toBe("POST");
    expect(JSON.parse(String(recorded[0]?.init.body))).toEqual({
      code: "WH-MAIN",
      name: "Main Warehouse",
      location: "Bhiwandi, Maharashtra",
    });
    expect(result).toEqual({ id: "wh-9" });
  });

  it("lets a duplicate name or code refusal reach the engine so the row is reported as skipped", async () => {
    stubGateway(() => ({
      body: {
        title: "Conflict",
        status: 409,
        detail: "A warehouse named 'Main Warehouse' already exists",
        errors: [{ field: "name", code: "duplicate", message: "A warehouse named 'Main Warehouse' already exists" }],
      },
      status: 409,
    }));
    await expect(warehouseImport.create(warehouseImport.toPayload(ROW, CONTEXT), CONTEXT)).rejects.toMatchObject({
      status: 409,
    });
  });
});

describe("warehouse export", () => {
  const WAREHOUSE: WarehouseOut = {
    id: "wh-1",
    code: "WH-MAIN",
    name: "Main Warehouse",
    location: "Bhiwandi, Maharashtra",
  };

  it("pages the whole list, passing the abort signal on", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageOf([WAREHOUSE], 2, 2) }));

    const result = await exportSource().fetchPage({
      page: 2,
      size: 200,
      signal: controller.signal,
      filters: undefined,
    });

    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/warehouses");
    expect(recorded[0]?.url.searchParams.get("page")).toBe("2");
    expect(recorded[0]?.url.searchParams.get("size")).toBe("200");
    expect(recorded[0]?.init.signal).toBe(controller.signal);
    expect(result.items).toEqual([WAREHOUSE]);
    expect(result.pages).toBe(2);
  });

  it("writes one sheet row per warehouse, a cell for every column", () => {
    const row = exportSource().toRow(WAREHOUSE, {});
    expect(row).toStrictEqual({
      code: "WH-MAIN",
      name: "Main Warehouse",
      location: "Bhiwandi, Maharashtra",
      id: "wh-1",
    });
    expect(warehouseImport.columns.map((c) => c.key).filter((key) => !(key in row))).toEqual([]);
  });

  it("leaves an empty location blank", () => {
    expect(exportSource().toRow({ ...WAREHOUSE, location: "" }, {}).location).toBeNull();
  });
});
