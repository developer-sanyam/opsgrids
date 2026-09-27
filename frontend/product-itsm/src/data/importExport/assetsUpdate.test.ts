/**
 * Updating existing assets from a sheet, tested directly: the pure planner from `@opsgrids/client`
 * is given a parsed sheet row and a stored asset, and the requests it would make go to a stubbed
 * `fetch`. The engine and the dialog are not involved.
 *
 * `PATCH /itsm/assets/{tag}` refuses an empty body and the tag in the body, answers a null on a
 * required column with a 409 and on `cost_currency` with a 500, and relabels a cost when its
 * currency changes instead of converting it. So the body must hold only what changed, and never a
 * null the survey did not find nullable.
 */
import { RowProblem, indexExisting, matchRow, planUpdate } from "@opsgrids/client";
import type { ImportContext, ParsedRow, UpdateSpec } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "@/api/types";

import { assetsEntity } from "./assets";

const signal = new AbortController().signal;
const context: ImportContext = { lookups: {}, signal };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
  });
}

type Handler = (url: URL, init: RequestInit | undefined) => Response;

/** A stubbed gateway: one handler per `METHOD /path`; any other request fails the test. */
function stubApi(handlers: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://gateway.test");
    const handler = handlers[`${init?.method ?? "GET"} ${url.pathname}`];
    if (handler === undefined) throw new Error(`Unexpected request: ${input}`);
    return handler(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function asset(over: Partial<Asset> = {}): Asset {
  return {
    tag: "LT-2041",
    name: "Dell Latitude 7440",
    type: "laptop",
    status: "in_use",
    owner: "Anita Desai",
    location: "Bengaluru HQ",
    serial: "8H2K3Z3",
    purchased_at: "2025-08-14",
    warranty_expires_at: "2028-08-13",
    cost_minor: 8450000,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

const STORED = asset();

function spec(): UpdateSpec<Asset> {
  const update = assetsEntity.update;
  if (update === undefined) throw new Error("The assets entity has no update spec");
  return update;
}

function plan(row: ParsedRow, cleared: readonly string[] = [], existing: Asset = STORED) {
  return planUpdate(assetsEntity.columns, spec(), row, new Set(cleared), existing, context);
}

/** Runs the plan against a gateway that accepts the PATCH, and returns the request it made. */
async function sent(row: ParsedRow, cleared: readonly string[] = [], existing: Asset = STORED) {
  const fetchMock = stubApi({ [`PATCH /api/v1/itsm/assets/${existing.tag}`]: () => json(200, existing) });
  const p = plan(row, cleared, existing);
  expect(p, "the row should change something").not.toBeNull();
  await p?.run();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, method: init.method, body: JSON.parse(init.body as string) as Record<string, unknown> };
}

/** Every column of the stored asset, as a sheet that has not been touched would carry it. The
 *  currency cell is blank: on an update a blank currency stays blank. */
const UNTOUCHED: ParsedRow = {
  tag: "LT-2041",
  name: "Dell Latitude 7440",
  type: "laptop",
  status: "in_use",
  owner: "Anita Desai",
  location: "Bengaluru HQ",
  serial: "8H2K3Z3",
  purchased_at: "2025-08-14",
  warranty_expires_at: "2028-08-13",
  cost: 8450000,
  cost_currency: null,
};

describe("the update spec", () => {
  it("names an asset by its tag alone: there is no Record ID to match on", () => {
    expect(assetsEntity.recordIdKey).toBeUndefined();
    expect(spec().idOf(STORED)).toBe("LT-2041");
    expect(spec().keyColumn).toBe("tag");
    expect(spec().keyOf?.(STORED)).toBe("LT-2041");
  });

  it("reads a cost in the asset's own currency when the row names none", () => {
    expect(spec().currencyOf?.(asset({ cost_currency: "JPY" }))).toBe("JPY");
  });

  it("names only importable columns, never the read-only count", () => {
    const importable = new Set(assetsEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key));
    const named = spec().fields.map((f) => f.column);
    for (const column of named) expect(importable.has(column), column).toBe(true);
    expect(named).not.toContain("open_incidents");
  });

  it("keeps the tag immutable and nothing else", () => {
    expect(spec().fields.filter((f) => f.immutable === true).map((f) => f.column)).toEqual(["tag"]);
  });

  it("lets exactly the columns the endpoint accepts a clear for be cleared, each with the value it accepts", () => {
    const clearable = Object.fromEntries(
      spec()
        .fields.filter((f) => f.clear !== undefined)
        .map((f) => [f.column, f.clear]),
    );
    expect(clearable).toEqual({
      owner: null,
      location: "",
      serial: "",
      purchased_at: null,
      warranty_expires_at: null,
    });
  });

  it("sends each column under the key the endpoint reads it from", () => {
    const keys = Object.fromEntries(spec().fields.map((f) => [f.column, f.field ?? f.column]));
    expect(keys).toMatchObject({ cost: "cost_minor", cost_currency: "cost_currency", owner: "owner" });
    expect(keys).not.toHaveProperty("open_incidents");
  });
});

describe("matching a row to a stored asset", () => {
  const index = indexExisting(spec(), [STORED, asset({ tag: "SRV-0042" })]);

  it("finds the asset by tag, ignoring case and spacing", () => {
    expect(matchRow(index, null, "lt-2041")).toMatchObject({ kind: "found", by: "key", item: STORED });
    expect(matchRow(index, null, " LT-2041 ")).toMatchObject({ kind: "found", by: "key" });
  });

  it("treats a tag nobody has as a new asset", () => {
    expect(matchRow(index, null, "NEW-0001")).toEqual({ kind: "new" });
  });

  it("refuses a tag two assets share instead of guessing", () => {
    const twins = indexExisting(spec(), [STORED, asset({ tag: "lt-2041" })]);
    expect(matchRow(twins, null, "LT-2041")).toEqual({ kind: "ambiguous" });
  });
});

describe("loading the assets to compare with", () => {
  it("reads every page of the whole list, once each asset, keyed by tag", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/itsm/assets": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, { items: [asset({ tag: "A-1" }), asset({ tag: "A-2" })], total: 3, page: 1, size: 200, pages: 2 })
          : json(200, { items: [asset({ tag: "A-2" }), asset({ tag: "A-3" })], total: 3, page: 2, size: 200, pages: 2 }),
    });
    const items = await spec().loadExisting(context);
    expect(items.map((a) => a.tag)).toEqual(["A-1", "A-2", "A-3"]);
    const params = fetchMock.mock.calls.map(([url]) => new URL(url, "http://gateway.test").searchParams);
    expect(params.map((p) => `${p.get("page")}/${p.get("size")}`)).toEqual(["1/200", "2/200"]);
    expect(params.every((p) => !p.has("q") && !p.has("type") && !p.has("status"))).toBe(true);
  });

  it("refuses a list that stays short of the server's own total rather than diff against part of it", async () => {
    stubApi({
      "GET /api/v1/itsm/assets": () => json(200, { items: [asset()], total: 4, page: 1, size: 200, pages: 1 }),
    });
    await expect(spec().loadExisting(context)).rejects.toThrow(/changed while it was being read/);
  });
});

describe("what a row changes", () => {
  it("changes nothing, and makes no request, when every cell agrees with the stored asset", () => {
    const fetchMock = stubApi({});
    expect(plan(UNTOUCHED)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never asks for an empty body, which the server refuses: a row with nothing to change is no request", () => {
    const blank: ParsedRow = Object.fromEntries(Object.keys(UNTOUCHED).map((key) => [key, null]));
    expect(plan(blank)).toBeNull();
  });

  it("ignores the read-only count, so an exported file can be edited and sent back", () => {
    expect(plan({ ...UNTOUCHED, open_incidents: 3 })).toBeNull();
  });

  it.each([
    ["Name", { name: "Dell Latitude 7450" }, { name: "Dell Latitude 7450" }],
    ["Type", { type: "desktop" }, { type: "desktop" }],
    ["Status", { status: "retired" }, { status: "retired" }],
    ["Owner", { owner: "Ravi Iyer" }, { owner: "Ravi Iyer" }],
    ["Location", { location: "Pune office" }, { location: "Pune office" }],
    ["Serial number", { serial: "00123ABC" }, { serial: "00123ABC" }],
    ["Purchase date", { purchased_at: "2025-09-01" }, { purchased_at: "2025-09-01" }],
    ["Warranty expires", { warranty_expires_at: "2029-08-13" }, { warranty_expires_at: "2029-08-13" }],
    ["Cost", { cost: 9000050 }, { cost_minor: 9000050 }],
  ] satisfies [string, ParsedRow, Record<string, string | number>][])(
    "sends only %s, under the endpoint's own key, when only that cell differs",
    async (header, cells, wire) => {
      expect(plan({ ...UNTOUCHED, ...cells })?.changed).toEqual([header]);
      const request = await sent({ ...UNTOUCHED, ...cells });
      expect(request).toEqual({ url: "/api/v1/itsm/assets/LT-2041", method: "PATCH", body: wire });
    },
  );

  it("sends several changes in one request, and nothing that did not change", async () => {
    const { body } = await sent({ ...UNTOUCHED, status: "in_repair", owner: "Data centre team", serial: "8H2K3Z3" });
    expect(body).toEqual({ status: "in_repair", owner: "Data centre team" });
    expect(Object.values(body).every((value) => value !== null)).toBe(true);
  });

  it("updates by the tag in the URL and never puts the tag in the body", async () => {
    const { url, body } = await sent({ ...UNTOUCHED, tag: "lt-2041", name: "Renamed" });
    expect(url).toBe("/api/v1/itsm/assets/LT-2041");
    expect(body).not.toHaveProperty("tag");
  });

  it("addresses the asset by the tag it is stored under, whatever case the sheet wrote", async () => {
    const stored = asset({ tag: "SRV-0042" });
    const { url } = await sent({ tag: "srv-0042", name: "Rack server" }, [], stored);
    expect(url).toBe("/api/v1/itsm/assets/SRV-0042");
  });

  it("treats a zero cost as a real amount, not as blank", async () => {
    const { body } = await sent({ cost: 0 });
    expect(body).toEqual({ cost_minor: 0 });
  });
});

describe("the cost and its currency", () => {
  it("leaves a cost alone that matches the stored amount when the currency cell is blank", () => {
    expect(plan({ cost: 8450000, cost_currency: null })).toBeNull();
  });

  it("accepts the stored currency in any case as no change", () => {
    expect(plan({ cost_currency: "inr", cost: 8450000 })).toBeNull();
    expect(plan({ cost_currency: "INR" })).toBeNull();
  });

  it("changes the currency and the cost together, in one request", async () => {
    const { body } = await sent({ cost: 120000, cost_currency: "usd" });
    expect(body).toEqual({ cost_minor: 120000, cost_currency: "USD" });
  });

  it("sends the currency alone when the cost, read in the new currency, is the amount already stored", async () => {
    const { body } = await sent({ cost: 8450000, cost_currency: "USD" });
    expect(body).toEqual({ cost_currency: "USD" });
  });

  it("refuses a currency change with no cost: the server would relabel the amount, not convert it", () => {
    expect(() => plan({ cost: null, cost_currency: "USD" })).toThrow(RowProblem);
    expect(() => plan({ cost: null, cost_currency: "USD" })).toThrow(
      "Cost currency would change from INR to USD, but changing it does not convert the cost. Give Cost in USD too, or leave Cost currency blank.",
    );
  });

  it("refuses the same change when it is the only cell on the row", () => {
    expect(() => plan({ tag: "LT-2041", cost_currency: "EUR" })).toThrow("Give Cost in EUR too");
  });

  it("compares minor units as stored, whatever the exponent of the asset's currency", () => {
    const yen = asset({ cost_minor: 125000, cost_currency: "JPY" });
    expect(plan({ cost: 125000 }, [], yen)).toBeNull();
    expect(plan({ cost: 130000 }, [], yen)?.changed).toEqual(["Cost"]);
  });
});

describe("clearing with [clear]", () => {
  it.each([
    ["owner", "Owner", null],
    ["purchased_at", "Purchase date", null],
    ["warranty_expires_at", "Warranty expires", null],
    ["location", "Location", ""],
    ["serial", "Serial number", ""],
  ])("empties %s with %j, the only value the endpoint accepts for it", async (column, header, value) => {
    expect(plan({ [column]: null }, [column])?.changed).toEqual([header]);
    const { body } = await sent({ [column]: null }, [column]);
    expect(body).toStrictEqual({ [column]: value });
  });

  it("never clears a text column with null, which the server answers with a conflict", async () => {
    const { body } = await sent({ location: null, serial: null }, ["location", "serial"]);
    expect(body).toStrictEqual({ location: "", serial: "" });
  });

  it.each([
    ["name", "Name"],
    ["type", "Type"],
    ["status", "Status"],
    ["cost", "Cost"],
    ["cost_currency", "Cost currency"],
  ])("refuses to clear %s: a null there is a 409, and on the currency a 500", (column, header) => {
    expect(() => plan({ [column]: null }, [column])).toThrow(RowProblem);
    expect(() => plan({ [column]: null }, [column])).toThrow(`${header} cannot be cleared`);
  });

  it("does nothing to a field that is already empty", () => {
    const bare = asset({ owner: null, location: "", serial: "", purchased_at: null, warranty_expires_at: null });
    const cleared = ["owner", "location", "serial", "purchased_at", "warranty_expires_at"];
    expect(plan(Object.fromEntries(cleared.map((c) => [c, null])), cleared, bare)).toBeNull();
  });

  it("clears only what the row asks and leaves a blank cell's field alone", async () => {
    const { body } = await sent({ owner: null, serial: null }, ["owner"]);
    expect(body).toEqual({ owner: null });
  });
});

describe("the tag is immutable", () => {
  it("accepts a cell that agrees with the stored tag, in any case", () => {
    expect(plan({ tag: "lt-2041" })).toBeNull();
    expect(plan({ tag: "  LT-2041 " })).toBeNull();
  });

  it("refuses a different tag, saying what is stored", () => {
    expect(() => plan({ tag: "LT-9999" })).toThrow(RowProblem);
    expect(() => plan({ tag: "LT-9999" })).toThrow(
      "Asset tag cannot be changed once the record exists (it is 'LT-2041')",
    );
  });

  it("refuses [clear] on it", () => {
    expect(() => plan({ tag: null }, ["tag"])).toThrow("Asset tag cannot be cleared");
  });
});

describe("what the server can answer", () => {
  it.each([
    [404, "That asset is not part of this organization"],
    [403, "Out of scope for this resource"],
    [409, "That conflicts with something that already exists"],
    [422, "Provide at least one field to update"],
  ])("lets a %s through with the server's own sentence", async (status, detail) => {
    stubApi({ "PATCH /api/v1/itsm/assets/LT-2041": () => json(status, { title: "Refused", status, detail }) });
    const attempt = plan({ name: "Renamed" })?.run();
    await expect(attempt).rejects.toMatchObject({ status });
    await expect(attempt).rejects.toThrow(detail);
  });
});
