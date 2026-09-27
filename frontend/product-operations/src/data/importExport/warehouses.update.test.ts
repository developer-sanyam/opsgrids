/**
 * Updating existing warehouses from a sheet (ADR-0022 addendum). A rename is the risky change: the
 * server rewrites the name on every item and purchase order that carries it, and checks a new name
 * only for an exact match. These tests hold the spec to the body it sends and to the rename rules
 * it applies before anything is sent.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import { pageAt, stubGateway, warehouseOut } from "./entityTestKit";
import {
  containsNull,
  expectPlan,
  matchAmong,
  planFor,
  requestsOf,
  soleRequest,
  stepsAndRequests,
  updateSpec,
} from "./updateTestKit";
import { warehouseImport } from "./warehouses";

afterEach(() => {
  vi.unstubAllGlobals();
});

const MAIN = { ...warehouseOut("Main Warehouse", "WH-MAIN"), location: "Bhiwandi, Maharashtra" };
const COLD = warehouseOut("Cold Store", "WH-COLD");

const lookupsOf = (...names: string[]): LookupSet => ({
  warehouses: makeLookup(names.map((name) => ({ label: name, value: name }))),
});

const ctx: ImportContext = { lookups: lookupsOf("Main Warehouse", "Cold Store"), signal: new AbortController().signal };

const PATH = "/api/v1/operations/warehouses/wh-WH-MAIN";

const SAME: ParsedRow = { code: "WH-MAIN", name: "Main Warehouse", location: "Bhiwandi, Maharashtra" };
const BLANK: ParsedRow = { code: null, name: null, location: null };

const plan = (row: ParsedRow, cleared: readonly string[] = [], existing = MAIN, context = ctx) =>
  planFor(warehouseImport, existing, row, context, cleared);

describe("a row that changes nothing", () => {
  it("makes no request when every cell already matches the warehouse", async () => {
    expect(plan(SAME)).toBeNull();
    expect(await requestsOf(plan(SAME))).toEqual([]);
  });

  it("holds a row found by its Record ID to the code's exact spelling: other capitals is an attempt to change it", () => {
    // A row found by typing its code is handed the stored spelling by the engine (see updateFlow.test.tsx).
    expect(() => plan({ ...BLANK, code: "wh-main" })).toThrow(
      "Warehouse code cannot be changed once the record exists (it is 'WH-MAIN')",
    );
  });

  it("leaves every field alone when every cell is blank", () => {
    expect(plan(BLANK)).toBeNull();
  });
});

describe("a row that changes a field", () => {
  it("renames the warehouse with only the name in the body", async () => {
    const p = plan({ ...SAME, name: "Central Warehouse" });
    expect(p?.changed).toEqual(["Warehouse name"]);
    const request = await soleRequest(p);
    expect(request).toEqual({ method: "PATCH", path: PATH, body: { name: "Central Warehouse" } });
    expect(containsNull(request.body)).toBe(false);
  });

  it("moves the location with only the location in the body", async () => {
    const p = plan({ ...SAME, location: "Nashik, Maharashtra" });
    expect(p?.changed).toEqual(["Location"]);
    expect((await soleRequest(p)).body).toEqual({ location: "Nashik, Maharashtra" });
  });

  it("changes both in one request, and only the columns the sheet has", async () => {
    const p = plan({ ...BLANK, name: "Central Warehouse", location: "Pune" });
    expect((await soleRequest(p)).body).toEqual({ name: "Central Warehouse", location: "Pune" });
  });

  it("is one step and one request however many fields change, so a rate-limited request is retried on its own", async () => {
    const p = plan({ ...BLANK, name: "Central Warehouse", location: "Pune" });
    expect(p?.changed).toHaveLength(2);
    expect(await stepsAndRequests(p)).toEqual({ steps: 1, requests: 1 });
  });

  it("fills in a location that was empty", async () => {
    const bare = { ...MAIN, location: "" };
    expect((await soleRequest(plan({ ...BLANK, location: "Pune" }, [], bare))).body).toEqual({ location: "Pune" });
  });
});

describe("[clear]", () => {
  it("empties the location with an empty string, because this endpoint has no null guard and a null is a 409", async () => {
    const p = plan({ ...BLANK }, ["location"]);
    expect(p?.changed).toEqual(["Location"]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { location: "" } });
  });

  it("does nothing to a location that is already empty", () => {
    expect(plan({ ...BLANK }, ["location"], { ...MAIN, location: "" })).toBeNull();
  });

  it("refuses to clear the name", () => {
    expect(() => plan({ ...BLANK }, ["name"])).toThrow(RowProblem);
    expect(() => plan({ ...BLANK }, ["name"])).toThrow("Warehouse name cannot be cleared");
  });

  it("refuses to clear the code", () => {
    expect(() => plan({ ...BLANK }, ["code"])).toThrow(
      "Warehouse code cannot be cleared: it cannot be changed once the record exists",
    );
  });
});

describe("what can never change", () => {
  it("refuses a code that names another warehouse, saying what is stored, and sends nothing", () => {
    const attempt = () => plan({ ...SAME, code: "WH-COLD", name: "Renamed" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("Warehouse code cannot be changed once the record exists (it is 'WH-MAIN')");
  });

  it("never puts the code in a body, whatever the row says", async () => {
    const p = plan({ ...SAME, name: "Central Warehouse" });
    expect(Object.keys((await soleRequest(p)).body as object)).toEqual(["name"]);
  });
});

describe("renaming a warehouse", () => {
  it("refuses a name that another warehouse has in other capitals, naming the one that has it", () => {
    const attempt = () => plan({ ...BLANK, name: "cold store" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(
      "A warehouse called 'Cold Store' already exists; names that differ only in capitals or spacing are not accepted.",
    );
  });

  it("refuses a name that another warehouse has in other spacing", () => {
    expect(() => plan({ ...BLANK, name: "Cold   Store" })).toThrow(RowProblem);
  });

  it("refuses a name that several warehouses already have in other capitals", () => {
    const twins = { ...ctx, lookups: lookupsOf("Main Warehouse", "Cold Store", "COLD STORE") };
    expect(() => plan({ ...BLANK, name: "cold store" }, [], MAIN, twins)).toThrow(
      "More than one warehouse already has a name that differs from 'cold store' only in capitals or spacing",
    );
  });

  it("leaves a name that another warehouse has exactly to the server, which answers it with a 409 naming the field", async () => {
    const p = expectPlan(plan({ ...BLANK, name: "Cold Store" }));
    stubGateway(() => ({
      status: 409,
      body: {
        type: "https://errors.opsgrids.dev/conflict",
        title: "Conflict",
        status: 409,
        detail: "A warehouse named 'Cold Store' already exists",
        errors: [{ field: "name", code: "duplicate", message: "A warehouse named 'Cold Store' already exists" }],
      },
    }));
    await expect(p.run()).rejects.toBeInstanceOf(ApiError);
    await expect(p.run()).rejects.toMatchObject({ status: 409, message: "A warehouse named 'Cold Store' already exists" });
  });

  it("allows a change of capitals or spacing in the warehouse's own name", async () => {
    expect((await soleRequest(plan({ ...BLANK, name: "MAIN WAREHOUSE" }))).body).toEqual({ name: "MAIN WAREHOUSE" });
    expect((await soleRequest(plan({ ...BLANK, name: "Main  Warehouse" }))).body).toEqual({ name: "Main  Warehouse" });
  });

  it("allows a name nobody has", () => {
    expect(plan({ ...BLANK, name: "Central Warehouse" })?.changed).toEqual(["Warehouse name"]);
  });

  it("does not look at other warehouses when the name is not changing", () => {
    expect(plan({ ...SAME, location: "Pune" })?.changed).toEqual(["Location"]);
  });

  it("leaves the check to the server when the names were not loaded", () => {
    expect(plan({ ...BLANK, name: "cold store" }, [], MAIN, { ...ctx, lookups: {} })?.changed).toEqual(["Warehouse name"]);
  });

  it("says in the notes and the column help that the rename reaches every item and purchase order", () => {
    const notes = (warehouseImport.notes ?? []).filter((note) => note.startsWith("Updating:")).join(" ");
    expect(notes).toMatch(/renames it, without notice, on every inventory item and purchase order/);
    expect(notes).toMatch(/Run the rename first, then any inventory sheet that names the new warehouse/);
    expect(warehouseImport.columns.find((column) => column.key === "name")?.help).toMatch(
      /renames it on every inventory item and purchase order that uses it/,
    );
  });
});

describe("finding the warehouse a row names", () => {
  it("matches by Record ID first, and by code ignoring capitals and spacing", () => {
    expect(matchAmong(warehouseImport, [MAIN, COLD], "wh-WH-COLD", null)).toEqual({ kind: "found", item: COLD, by: "id" });
    expect(matchAmong(warehouseImport, [MAIN, COLD], null, " wh-cold ")).toEqual({ kind: "found", item: COLD, by: "key" });
  });

  it("keys a warehouse by its code alone, so that a rename is a change and not a new warehouse", () => {
    const spec = updateSpec(warehouseImport);
    expect(spec.keyColumn).toBe("code");
    expect(spec.keyOf?.(MAIN)).toBe("WH-MAIN");
  });

  it("treats a code nobody has as a new warehouse, and a Record ID nobody has as an error", () => {
    expect(matchAmong(warehouseImport, [MAIN, COLD], null, "WH-NEW")).toEqual({ kind: "new" });
    expect(matchAmong(warehouseImport, [MAIN, COLD], "wh-404", "WH-MAIN")).toEqual({ kind: "unknown-id" });
  });

  it("refuses a code that two warehouses share, instead of picking one", () => {
    const twin = warehouseOut("Main Two", "wh-main");
    expect(matchAmong(warehouseImport, [MAIN, twin], null, "WH-MAIN")).toEqual({ kind: "ambiguous" });
  });
});

describe("warehouse loadExisting", () => {
  const spec = updateSpec(warehouseImport);

  it("reads every page of the whole list and keeps each warehouse once", async () => {
    const served = [[MAIN], [MAIN, COLD]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 2, page, size: 200, pages: 2 } };
    });

    const items = await spec.loadExisting(ctx);

    expect(items.map((item) => item.code)).toEqual(["WH-MAIN", "WH-COLD"]);
    expect(recorded.map((request) => Object.fromEntries(request.url.searchParams))).toEqual([
      { page: "1", size: "200" },
      { page: "2", size: "200" },
    ]);
  });

  it("reads the list afresh on every call, so an inventory run after a rename sees the new names", async () => {
    let name = "Main Warehouse";
    stubGateway(() => ({ body: pageAt([[warehouseOut(name, "WH-MAIN")]], 1) }));

    const before = await spec.loadExisting(ctx);
    name = "Central Warehouse";
    const after = await spec.loadExisting(ctx);

    expect([before[0]?.name, after[0]?.name]).toEqual(["Main Warehouse", "Central Warehouse"]);
  });
});

describe("warehouse update failures", () => {
  it("surfaces a refusal for a caller who may not write warehouses as the row's failure, leaving the decision to the server", async () => {
    stubGateway(() => ({
      status: 403,
      body: {
        type: "https://errors.opsgrids.dev/forbidden",
        title: "Forbidden",
        status: 403,
        detail: "Missing permission: operations.inventory.warehouse.write",
      },
    }));
    const p = expectPlan(plan({ ...BLANK, location: "Pune" }));
    await expect(p.run()).rejects.toMatchObject({ status: 403, message: "Missing permission: operations.inventory.warehouse.write" });
  });
});
