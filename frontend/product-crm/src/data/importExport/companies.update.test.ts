import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, indexExisting, makeLookup, matchRow } from "@opsgrids/client";
import type { LookupSet, ParsedValue } from "@opsgrids/client";

import type { Company } from "@/api/types";
import { companyImport } from "./companies";
import { companyWire, contextWith, pageOf, stubGateway } from "./entityTestKit";
import { planFor, requestLines, runPlan, updateSpecOf, valuesOf } from "./updateTestKit";

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
    // Two active reps under one address: the name cannot say which.
    { label: "twin@example.com", value: "own-7" },
    { label: "Twin@example.com", value: "own-8" },
  ]),
};
const ctx = contextWith(LOOKUPS);
const spec = updateSpecOf(companyImport);

afterEach(() => {
  vi.unstubAllGlobals();
});

const STORED: Company = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: "own-2",
  annual_revenue_minor: 8400000050,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

/** Every importable cell as an export of STORED writes it, once coerced. */
const SAME_AS_STORED: Record<string, ParsedValue> = {
  name: "Northwind Logistics",
  owner: "Grace@Example.com",
  domain: "northwind.co",
  industry: "Logistics",
  sizeBand: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  annualRevenue: 8400000050,
  currency: "INR",
};

const plan = (cells: Record<string, ParsedValue>, cleared: string[] = [], existing: Company = STORED) =>
  planFor(companyImport, existing, cells, ctx, cleared);

const PATCH = "PATCH /api/v1/customers/comp-1";

describe("companies: which record a row names", () => {
  const other: Company = { ...STORED, id: "comp-2", name: "Cobalt Financial" };
  const index = indexExisting(spec, [STORED, other]);

  it("takes the record's own id, and the name as the unique key", () => {
    expect(spec.idOf(STORED)).toBe("comp-1");
    expect(spec.keyColumn).toBe("name");
    expect(spec.keyOf?.(STORED)).toBe("Northwind Logistics");
  });

  it("matches by Company ID first, and refuses an ID no company has", () => {
    expect(matchRow(index, "comp-2", "Northwind Logistics")).toMatchObject({ kind: "found", by: "id" });
    expect(matchRow(index, "comp-404", null)).toEqual({ kind: "unknown-id" });
  });

  it("matches by name, ignoring capitals and spacing, and treats an unknown name as a new company", () => {
    expect(matchRow(index, null, "  NORTHWIND   logistics ")).toMatchObject({ kind: "found", by: "key" });
    expect(matchRow(index, null, "Solstice Energy")).toEqual({ kind: "new" });
  });

  it("refuses a name two stored companies share (they differ only in capitals) instead of choosing one", () => {
    const twins = indexExisting(spec, [STORED, { ...other, name: "NORTHWIND LOGISTICS" }]);
    expect(matchRow(twins, null, "Northwind Logistics")).toEqual({ kind: "ambiguous" });
  });
});

describe("companies: what a row changes", () => {
  it("changes nothing, and makes no request, when every cell matches the stored company", () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    expect(plan(SAME_AS_STORED)).toBeNull();
    // A currency typed in lower case and an owner in other capitals are the same value.
    expect(plan({ ...SAME_AS_STORED, currency: "inr", owner: "grace@example.COM" })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("leaves every field alone when its cell is blank", () => {
    expect(plan({})).toBeNull();
  });

  const SINGLE: readonly {
    what: string;
    cells: Record<string, ParsedValue>;
    header: string;
    body: Record<string, unknown>;
  }[] = [
    { what: "a rename", cells: { name: "Northwind Logistics Ltd" }, header: "Company name", body: { name: "Northwind Logistics Ltd" } },
    { what: "a change of capitals alone", cells: { name: "NORTHWIND LOGISTICS" }, header: "Company name", body: { name: "NORTHWIND LOGISTICS" } },
    { what: "another owner", cells: { owner: "sofia@example.com" }, header: "Owner email", body: { owner_id: "own-1" } },
    { what: "the domain", cells: { domain: "northwind.example" }, header: "Domain", body: { domain: "northwind.example" } },
    { what: "the industry", cells: { industry: "Freight" }, header: "Industry", body: { industry: "Freight" } },
    { what: "the headcount band", cells: { sizeBand: "1000+" }, header: "Headcount", body: { size_band: "1000+" } },
    { what: "the city", cells: { city: "Pune" }, header: "City", body: { city: "Pune" } },
    { what: "the country", cells: { country: "Nepal" }, header: "Country", body: { country: "Nepal" } },
    { what: "the status", cells: { status: "churned" }, header: "Status", body: { status: "churned" } },
    { what: "the revenue, in minor units", cells: { annualRevenue: 1250000 }, header: "Annual revenue", body: { annual_revenue_minor: 1250000 } },
  ];

  it.each(SINGLE)("sends one PATCH holding only $what, under the key the endpoint names", async ({ cells, header, body }) => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    const result = plan({ ...SAME_AS_STORED, ...cells });
    expect(result?.changed).toEqual([header]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual(body);
  });

  it("sends only the fields that differ when several cells change, in one request", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    const result = plan({ ...SAME_AS_STORED, city: "Pune", status: "prospect", industry: "Logistics" });
    expect(result?.changed).toEqual(["City", "Status"]);
    await runPlan(result);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toStrictEqual({ city: "Pune", status: "prospect" });
  });

  it("names the changed columns for the preview in the order of the sheet's columns", () => {
    expect(plan({ status: "prospect", name: "Renamed", city: "Pune" })?.changed).toEqual([
      "Company name",
      "City",
      "Status",
    ]);
  });
});

describe("companies: [clear]", () => {
  const CLEARABLE = [
    { column: "domain", header: "Domain", key: "domain" },
    { column: "industry", header: "Industry", key: "industry" },
    { column: "sizeBand", header: "Headcount", key: "size_band" },
    { column: "city", header: "City", key: "city" },
    { column: "country", header: "Country", key: "country" },
  ] as const;

  it.each(CLEARABLE)("empties $header with an empty string, which the endpoint accepts, never null", async ({ column, key }) => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    await runPlan(plan({}, [column]));
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual({ [key]: "" });
    expect(valuesOf(calls[0]?.body)).not.toContain(null);
  });

  it("does nothing to a field that is already empty", () => {
    expect(plan({}, ["domain", "city"], { ...STORED, domain: "", city: "" })).toBeNull();
  });

  it("empties several fields in one request", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    await runPlan(plan({ status: "prospect" }, ["domain", "industry"]));
    expect(calls[0]?.body).toStrictEqual({ domain: "", industry: "", status: "prospect" });
  });

  it.each([
    ["name", "Company name"],
    ["owner", "Owner email"],
    ["status", "Status"],
    ["annualRevenue", "Annual revenue"],
    ["currency", "Currency"],
  ])("refuses [clear] in %s: the endpoint has no way to empty it", (column, header) => {
    expect(() => plan({}, [column])).toThrow(RowProblem);
    expect(() => plan({}, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("companies: the owner", () => {
  it("resolves the owner's email to the rep's id, ignoring capitals", () => {
    expect(plan({ owner: "SOFIA@example.com" })?.changed).toEqual(["Owner email"]);
  });

  it("refuses an owner who is not an active rep, with a sentence naming the column", () => {
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(RowProblem);
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(
      "'nobody@example.com' is not an active sales rep. Use one from the Lists sheet, or leave Owner email blank to keep it as it is.",
    );
  });

  it("refuses an owner that two reps share, instead of choosing one", () => {
    expect(() => plan({ owner: "twin@example.com" })).toThrow(
      "'twin@example.com' matches more than one entry for Owner email, so it cannot be chosen.",
    );
  });

  it("does not pre-empt the server for a caller who may only assign to themselves", async () => {
    // Sent as written; the server answers 403 and the sentence is what the row reports.
    const calls = stubGateway(
      () => ({ title: "Forbidden", detail: "You can only assign companies you own to yourself", status: 403 }),
      403,
    );
    await expect(runPlan(plan({ owner: "sofia@example.com" }))).rejects.toMatchObject({
      status: 403,
      message: "You can only assign companies you own to yourself",
    });
    expect(requestLines(calls)).toEqual([PATCH]);
  });
});

describe("companies: money and currency", () => {
  const YEN: Company = { ...STORED, currency: "JPY", annual_revenue_minor: 500 };

  it("names the stored currency, so a blank Currency cell is read in it and not in INR", () => {
    expect(spec.currencyOf?.(YEN)).toBe("JPY");
    expect(spec.currencyOf?.(STORED)).toBe("INR");
  });

  it("leaves a blank Currency cell alone: a yen company is not re-priced in rupees", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    // The engine has read 800 in the stored currency (a yen has no minor unit): only the amount differs.
    await runPlan(plan({ annualRevenue: 800 }, [], YEN));
    expect(calls[0]?.body).toStrictEqual({ annual_revenue_minor: 800 });
  });

  it("changes nothing when the amount and the currency both repeat what is stored", () => {
    expect(plan({ annualRevenue: 500, currency: "jpy" }, [], YEN)).toBeNull();
    expect(plan({ annualRevenue: 500 }, [], YEN)).toBeNull();
  });

  it("sends the currency and the amount together when the row restates both", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    const result = plan({ annualRevenue: 84000000, currency: "usd" }, [], STORED);
    expect(result?.changed).toEqual(["Annual revenue", "Currency"]);
    await runPlan(result);
    expect(calls[0]?.body).toStrictEqual({ annual_revenue_minor: 84000000, currency: "USD" });
  });

  it("sends only the currency when the row restates the same minor units in it", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    await runPlan(plan({ annualRevenue: 8400000050, currency: "USD" }));
    expect(calls[0]?.body).toStrictEqual({ currency: "USD" });
  });

  it("refuses a currency change with no amount: the server would re-label the amount on file", () => {
    expect(() => plan({ currency: "USD" })).toThrow(RowProblem);
    expect(() => plan({ currency: "usd" })).toThrow(
      "Currency cannot change on its own: Annual revenue is not converted, so the amount on file would be read in the new currency. Fill in Annual revenue in USD too.",
    );
  });

  it("allows a currency change on a company with no revenue: there is nothing to re-label", async () => {
    const calls = stubGateway(() => companyWire("comp-1", "x"));
    await runPlan(plan({ currency: "USD" }, [], { ...STORED, annual_revenue_minor: 0 }));
    expect(calls[0]?.body).toStrictEqual({ currency: "USD" });
  });
});

describe("companies: what the server says", () => {
  it("reports a rename onto another company's name as a failure with the server's sentence, never as skipped", async () => {
    stubGateway(
      () => ({ title: "Conflict", detail: "A company with that name already exists", status: 409 }),
      409,
    );
    const failure = await runPlan(plan({ name: "Cobalt Financial" })).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, message: "A company with that name already exists" });
  });

  it("reports a company that has gone (404) as the server does", async () => {
    stubGateway(() => ({ title: "Not found", detail: "That company is not part of this organization", status: 404 }), 404);
    await expect(runPlan(plan({ city: "Pune" }))).rejects.toMatchObject({ status: 404 });
  });
});

describe("companies: loadExisting", () => {
  const companies = (from: number, to: number) =>
    Array.from({ length: to - from + 1 }, (_, i) => companyWire(`comp-${from + i}`, `Company ${from + i}`));

  it("reads every page of the whole list, unfiltered, with the dialog's signal", async () => {
    const calls = stubGateway(({ url }) =>
      url.searchParams.get("page") === "1"
        ? pageOf(companies(1, 200), 1, 200, 201)
        : pageOf(companies(201, 201), 2, 200, 201),
    );
    const existing = await spec.loadExisting(ctx);
    expect(existing).toHaveLength(201);
    expect(calls.map((c) => [c.url.pathname, c.url.searchParams.get("page"), c.url.searchParams.get("size")])).toEqual([
      ["/api/v1/customers", "1", "200"],
      ["/api/v1/customers", "2", "200"],
    ]);
    expect(calls.every((c) => !c.url.searchParams.has("q") && !c.url.searchParams.has("status"))).toBe(true);
    expect(calls.every((c) => c.signal === ctx.signal)).toBe(true);
  });

  it("keeps a company the list serves twice once, so a match is never ambiguous with itself", async () => {
    stubGateway(({ url }) =>
      url.searchParams.get("page") === "1"
        ? pageOf(companies(1, 200), 1, 200, 201)
        : pageOf(companies(200, 201), 2, 200, 201),
    );
    const existing = await spec.loadExisting(ctx);
    expect(existing).toHaveLength(201);
    expect(new Set(existing.map((c) => c.id)).size).toBe(201);
  });
});
