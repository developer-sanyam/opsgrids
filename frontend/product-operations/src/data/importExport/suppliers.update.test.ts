/**
 * Updating existing suppliers from a sheet (ADR-0022 addendum). The supplier endpoint has no null
 * guard, so what these tests hold the spec to is what it is sent: changed keys only, `""` and never
 * `null` to empty a text field, nothing at all for `since`, and no `code` or `currency` ever.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem } from "@opsgrids/client";
import type { ImportContext, ParsedRow } from "@opsgrids/client";

import { pageAt, stubGateway, supplierOut } from "./entityTestKit";
import { supplierImport } from "./suppliers";
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

afterEach(() => {
  vi.unstubAllGlobals();
});

const ctx: ImportContext = { lookups: {}, signal: new AbortController().signal };

const SUPPLIER = supplierOut();
const PATH = "/api/v1/operations/suppliers/sup-1";

/** Every importable cell equal to what `SUPPLIER` stores. */
const SAME: ParsedRow = {
  code: "SUP-MER",
  name: "Meridian Metals",
  category: "raw_materials",
  status: "active",
  lead_time_days: 12,
  currency: "INR",
  contact_name: "Rajesh Pillai",
  contact_email: "rajesh@meridianmetals.example",
  location: "Jamshedpur",
  since: "2019-04-02",
};

const BLANK: ParsedRow = Object.fromEntries(Object.keys(SAME).map((key) => [key, null]));

const plan = (row: ParsedRow, cleared: readonly string[] = [], existing = SUPPLIER) =>
  planFor(supplierImport, existing, row, ctx, cleared);

describe("a row that changes nothing", () => {
  it("makes no request when every cell already matches the supplier", async () => {
    expect(plan(SAME)).toBeNull();
    expect(await requestsOf(plan(SAME))).toEqual([]);
  });

  it("recognises the supplier's currency in other capitals as the same", () => {
    expect(plan({ ...SAME, currency: "inr" })).toBeNull();
  });

  it("holds a row found by its Record ID to the code's exact spelling: other capitals is an attempt to change it", () => {
    // A row found by typing its code is handed the stored spelling by the engine (see updateFlow.test.tsx).
    expect(() => plan({ ...BLANK, code: "sup-mer" })).toThrow(
      "Supplier code cannot be changed once the record exists (it is 'SUP-MER')",
    );
  });

  it("leaves every field alone when every cell is blank", () => {
    expect(plan(BLANK)).toBeNull();
  });

  it("does not mistake an address for changed because its stored domain has capitals", () => {
    const stored = supplierOut({ contact_email: "Rajesh@MeridianMetals.example" });
    expect(plan({ ...BLANK, contact_email: "Rajesh@meridianmetals.example" }, [], stored)).toBeNull();
  });
});

describe("a row that changes one field", () => {
  const CHANGES: [what: string, cells: ParsedRow, changed: string[], body: Record<string, unknown>][] = [
    ["the name", { name: "Meridian Metals Pvt Ltd" }, ["Supplier name"], { name: "Meridian Metals Pvt Ltd" }],
    ["the category", { category: "components" }, ["Category"], { category: "components" }],
    ["the status", { status: "on_hold" }, ["Status"], { status: "on_hold" }],
    ["the lead time", { lead_time_days: 20 }, ["Lead time (days)"], { lead_time_days: 20 }],
    ["a lead time of zero, which is a value and not a blank", { lead_time_days: 0 }, ["Lead time (days)"], { lead_time_days: 0 }],
    ["the contact name", { contact_name: "Farhan Qureshi" }, ["Contact name"], { contact_name: "Farhan Qureshi" }],
    ["the contact email", { contact_email: "farhan@meridianmetals.example" }, ["Contact email"], { contact_email: "farhan@meridianmetals.example" }],
    ["the location", { location: "Mumbai" }, ["Location"], { location: "Mumbai" }],
    ["the date they became a supplier", { since: "2020-01-15" }, ["Supplier since"], { since: "2020-01-15" }],
  ];

  it.each(CHANGES)("sends only %s, in one PATCH to the supplier", async (_what, cells, changed, body) => {
    const p = plan({ ...SAME, ...cells });
    expect(p?.changed).toEqual(changed);
    const request = await soleRequest(p);
    expect(request).toEqual({ method: "PATCH", path: PATH, body });
    expect(containsNull(request.body)).toBe(false);
  });

  it("sends only the fields that differ when a row changes several and repeats the rest", async () => {
    const p = plan({ ...SAME, name: "Meridian Steel", status: "pending", location: "Mumbai" });
    expect(p?.changed).toEqual(["Supplier name", "Status", "Location"]);
    expect((await soleRequest(p)).body).toEqual({ name: "Meridian Steel", status: "pending", location: "Mumbai" });
  });

  it("treats a change of capitals in the local part of an email as a change, since the server keeps it as typed", async () => {
    const p = plan({ ...BLANK, contact_email: "Rajesh@meridianmetals.example" });
    expect((await soleRequest(p)).body).toEqual({ contact_email: "Rajesh@meridianmetals.example" });
  });

  it("is one step and one request however many fields change, so a rate-limited request is retried on its own", async () => {
    const p = plan({ ...SAME, name: "Meridian Steel", status: "pending", location: "Mumbai", since: "2020-01-15" });
    expect(p?.changed).toHaveLength(4);
    expect(await stepsAndRequests(p)).toEqual({ steps: 1, requests: 1 });
  });

  it("fills in a contact that was empty", async () => {
    const bare = supplierOut({ contact_name: "", contact_email: "", location: "" });
    const p = plan({ ...BLANK, contact_name: "Farhan Qureshi", contact_email: "farhan@apexpack.example", location: "Vapi" }, [], bare);
    expect((await soleRequest(p)).body).toEqual({
      contact_name: "Farhan Qureshi",
      contact_email: "farhan@apexpack.example",
      location: "Vapi",
    });
  });
});

describe("[clear]", () => {
  it.each([
    ["contact_name", "Contact name"],
    ["contact_email", "Contact email"],
    ["location", "Location"],
  ])("empties %s with an empty string, because this endpoint has no null guard and a null is a 409", async (column, header) => {
    const p = plan({ ...BLANK }, [column]);
    expect(p?.changed).toEqual([header]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { [column]: "" } });
  });

  it("does nothing to a field that is already empty", () => {
    const bare = supplierOut({ contact_name: "", contact_email: "", location: "" });
    expect(plan({ ...BLANK }, ["contact_name", "contact_email", "location"], bare)).toBeNull();
  });

  it("empties all three in one request", async () => {
    const p = plan({ ...BLANK }, ["contact_name", "contact_email", "location"]);
    expect((await soleRequest(p)).body).toEqual({ contact_name: "", contact_email: "", location: "" });
  });

  it.each([
    ["name", "Supplier name"],
    ["category", "Category"],
    ["status", "Status"],
    ["lead_time_days", "Lead time (days)"],
    ["since", "Supplier since"],
  ])("refuses to clear %s: a null there is a conflict, or for since a server error", (column, header) => {
    expect(() => plan({ ...BLANK }, [column])).toThrow(RowProblem);
    expect(() => plan({ ...BLANK }, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("what can never change", () => {
  it("refuses a code that names another supplier, saying what is stored, and sends nothing", () => {
    const attempt = () => plan({ ...SAME, code: "SUP-APX", name: "Renamed" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("Supplier code cannot be changed once the record exists (it is 'SUP-MER')");
  });

  it("refuses a currency other than the supplier's, even when nothing else in the row is priced", () => {
    expect(() => plan({ ...BLANK, currency: "USD" })).toThrow(
      "Currency cannot be changed once the record exists (it is 'INR')",
    );
  });

  it("refuses the whole row, not just the currency, when the currency disagrees", () => {
    expect(() => plan({ ...SAME, name: "Renamed", currency: "EUR" })).toThrow(RowProblem);
  });

  it("accepts the supplier's own currency, which a blank Currency cell must not turn into rupees", () => {
    const dirham = supplierOut({ currency: "AED" });
    expect(plan({ ...BLANK, currency: null, name: "Renamed" }, [], dirham)?.changed).toEqual(["Supplier name"]);
    expect(plan({ ...BLANK, currency: "AED" }, [], dirham)).toBeNull();
    expect(updateSpec(supplierImport).currencyOf?.(dirham)).toBe("AED");
  });

  it.each([
    ["code", "Supplier code"],
    ["currency", "Currency"],
  ])("refuses to clear %s", (column, header) => {
    expect(() => plan({ ...BLANK }, [column])).toThrow(`${header} cannot be cleared: it cannot be changed once the record exists`);
  });

  it("never puts code or currency in a body, whatever the row says", async () => {
    const p = plan({ ...SAME, currency: "inr", name: "Renamed" });
    expect(Object.keys((await soleRequest(p)).body as object)).toEqual(["name"]);
  });

  it("does not offer the rollups as fields: they are worked out by the app", () => {
    const columns = updateSpec(supplierImport).fields.map((field) => field.column);
    expect(columns).not.toContain("spend");
    expect(columns).not.toContain("open_pos");
    expect(columns).not.toContain("on_time_pct");
  });
});

describe("finding the supplier a row names", () => {
  const meridian = supplierOut();
  const apex = supplierOut({ id: "sup-2", code: "SUP-APX", name: "Apex Packaging" });

  it("matches by Record ID first, and by the code, surrounding spaces ignored", () => {
    expect(matchAmong(supplierImport, [meridian, apex], "sup-2", null)).toEqual({ kind: "found", item: apex, by: "id" });
    expect(matchAmong(supplierImport, [meridian, apex], null, " SUP-APX ")).toEqual({ kind: "found", item: apex, by: "key" });
  });

  it("names the supplier a row updates by its code and name, never by its contact", () => {
    const label = updateSpec(supplierImport).recordLabel?.(meridian);
    expect(label).toBe("SUP-MER · Meridian Metals");
    expect(label).not.toContain("Rajesh");
    expect(label).not.toContain("@");
  });

  it("treats a code nobody has as a new supplier, and a Record ID nobody has as an error", () => {
    expect(matchAmong(supplierImport, [meridian, apex], null, "SUP-NEW")).toEqual({ kind: "new" });
    expect(matchAmong(supplierImport, [meridian, apex], "sup-404", "SUP-MER")).toEqual({ kind: "unknown-id" });
  });

  it("refuses a code that two suppliers share, instead of picking one", () => {
    const twin = supplierOut({ id: "sup-3", code: "SUP-MER" });
    expect(matchAmong(supplierImport, [meridian, twin], null, "SUP-MER")).toEqual({ kind: "ambiguous" });
  });
});

describe("supplier loadExisting", () => {
  const spec = updateSpec(supplierImport);

  it("reads every page of the whole list, with no filter, and keeps each supplier once", async () => {
    const [a, b, c] = [
      supplierOut({ id: "sup-1", code: "A" }),
      supplierOut({ id: "sup-2", code: "B" }),
      supplierOut({ id: "sup-3", code: "C" }),
    ];
    const served = [[a, b], [b, c]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const items = await spec.loadExisting(ctx);

    expect(items.map((item) => item.id)).toEqual(["sup-1", "sup-2", "sup-3"]);
    expect(recorded.map((request) => request.url.pathname)).toEqual([
      "/api/v1/operations/suppliers",
      "/api/v1/operations/suppliers",
    ]);
    expect(recorded.map((request) => Object.fromEntries(request.url.searchParams))).toEqual([
      { page: "1", size: "200" },
      { page: "2", size: "200" },
    ]);
  });

  it("reads the list afresh on every call", async () => {
    let name = "Meridian Metals";
    stubGateway(() => ({ body: pageAt([[supplierOut({ name })]], 1) }));

    const before = await spec.loadExisting(ctx);
    name = "Meridian Steel";
    const after = await spec.loadExisting(ctx);

    expect([before[0]?.name, after[0]?.name]).toEqual(["Meridian Metals", "Meridian Steel"]);
  });
});

describe("supplier update failures", () => {
  it("surfaces the server's own sentence as the row's failure, keeping its status", async () => {
    stubGateway(() => ({
      status: 422,
      body: {
        type: "https://errors.opsgrids.dev/validation",
        title: "Validation failed",
        status: 422,
        detail: "contact_email: must be a valid email address, or empty",
      },
    }));
    const p = expectPlan(plan({ ...BLANK, contact_email: "not-an-address@x.example" }));
    await expect(p.run()).rejects.toBeInstanceOf(ApiError);
    await expect(p.run()).rejects.toMatchObject({ status: 422 });
  });
});

describe("supplier update notes", () => {
  it("says what cannot change, what [clear] can empty, and that a rename reaches purchase orders", () => {
    const notes = (supplierImport.notes ?? []).filter((note) => note.startsWith("Updating:")).join(" ");
    expect(notes).toMatch(/Supplier code and Currency cannot be changed/);
    expect(notes).toMatch(/Contact name, Contact email and Location can be emptied with \[clear\]. Supplier since and every other column cannot/);
    expect(notes).toMatch(/renames it, without notice, on every purchase order/);
  });

  it("says that a code must be spelled as stored, and that other capitals need that spelling or the Record ID", () => {
    const notes = (supplierImport.notes ?? []).join(" ");
    expect(notes).toMatch(/by its code spelled exactly as it is stored/);
    expect(notes).toMatch(/differs only in capitals is refused, naming the stored spelling: use that spelling, or the Record ID/);
    expect(notes).not.toMatch(/still finds its supplier/);
  });

  it("says the same in the help for the name and the currency", () => {
    const help = (key: string) => supplierImport.columns.find((column) => column.key === key)?.help ?? "";
    expect(help("name")).toMatch(/renames it on every purchase order raised to it/);
    expect(help("currency")).toMatch(/existing supplier keeps its own currency/);
  });
});
