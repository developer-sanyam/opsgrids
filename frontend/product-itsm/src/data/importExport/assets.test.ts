/**
 * The Assets import/export entity, tested directly: what its columns promise, the request body a
 * sheet row turns into, and what an export asks for and writes. The engine and the dialog are not
 * involved; requests go to a stubbed `fetch`.
 */
import { RowProblem } from "@opsgrids/client";
import type { ImportContext, ParsedRow } from "@opsgrids/client";
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

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
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

function parsed(over: ParsedRow = {}): ParsedRow {
  return { tag: "SRV-0042", name: "Rack server", type: "server", ...over };
}

describe("columns", () => {
  it("asks for the tag, the name and the type", () => {
    const required = assetsEntity.columns.filter((c) => c.required === true);
    expect(required.map((c) => c.header)).toEqual(["Asset tag", "Name", "Type"]);
  });

  it("offers every type and status the API takes, by their screen labels", () => {
    const options = (key: string) =>
      assetsEntity.columns.find((c) => c.key === key)?.options?.map((o) => [o.value, o.label]);
    expect(options("type")).toEqual([
      ["laptop", "Laptop"],
      ["desktop", "Desktop"],
      ["server", "Server"],
      ["monitor", "Monitor"],
      ["phone", "Phone"],
      ["network", "Network"],
      ["license", "License"],
    ]);
    expect(options("status")).toEqual([
      ["in_use", "In use"],
      ["in_stock", "In stock"],
      ["in_repair", "In repair"],
      ["retired", "Retired"],
    ]);
  });

  it("holds cost as money that names its currency column", () => {
    const cost = assetsEntity.columns.find((c) => c.key === "cost");
    expect(cost).toMatchObject({ kind: "money", currencyKey: "cost_currency", min: 0 });
    expect(assetsEntity.columns.map((c) => c.key)).toContain("cost_currency");
  });

  it("keeps tags and serial numbers as text, and computed figures read-only", () => {
    const byKey = Object.fromEntries(assetsEntity.columns.map((c) => [c.key, c]));
    expect(byKey.tag?.kind).toBe("text");
    expect(byKey.serial?.kind).toBe("text");
    expect(byKey.open_incidents?.readOnly).toBe(true);
  });

  it("has no id column: the tag is the key, so no row is ever marked as already in the system", () => {
    expect(assetsEntity.recordIdKey).toBeUndefined();
    expect(assetsEntity.columns.some((c) => c.key === "id")).toBe(false);
  });

  it("tells the user two things about tags and ownership that the server does not say", () => {
    const notes = (assetsEntity.notes ?? []).join("\n");
    expect(notes).toMatch(/once belonged to an asset that was later deleted still cannot be used again/);
    expect(notes).toMatch(/reports it as a conflict without saying why/);
    expect(notes).toMatch(/recorded as belonging to the person who runs the import, whatever the Owner column says/);
  });
});

describe("sample rows", () => {
  it("fill the required columns and stay inside every column's own rules", () => {
    const importable = assetsEntity.columns.filter((c) => c.readOnly !== true);
    expect(assetsEntity.sampleRows.length).toBeGreaterThanOrEqual(2);
    for (const sample of assetsEntity.sampleRows) {
      for (const column of importable) {
        const cell = sample[column.key];
        if (column.required === true) expect(cell, column.header).toBeTruthy();
        if (typeof cell !== "string") continue;
        if (column.maxLength !== undefined) expect(cell.length).toBeLessThanOrEqual(column.maxLength);
        if (column.pattern !== undefined) expect(cell).toMatch(column.pattern);
        if (column.kind === "enum") {
          expect(column.options?.some((o) => o.label === cell || o.value === cell)).toBe(true);
        }
        if (column.kind === "money") expect(cell).toMatch(/^\d+(\.\d+)?$/);
      }
    }
  });

  it("use only known importable columns", () => {
    const importable = new Set(
      assetsEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key),
    );
    for (const sample of assetsEntity.sampleRows) {
      for (const key of Object.keys(sample)) expect(importable.has(key), key).toBe(true);
    }
  });
});

describe("toPayload", () => {
  it("sends only the tag, name and type when nothing else is filled", () => {
    expect(assetsEntity.toPayload(parsed(), context)).toEqual({
      tag: "SRV-0042",
      name: "Rack server",
      type: "server",
    });
  });

  it("carries every filled column, with cost as minor units in its currency", () => {
    const body = assetsEntity.toPayload(
      parsed({
        status: "in_repair",
        owner: "Data centre team",
        location: "Mumbai DC, row 4",
        serial: "00123ABC",
        purchased_at: "2025-11-03",
        warranty_expires_at: "2028-11-02",
        cost: 12500050,
        cost_currency: "usd",
      }),
      context,
    );
    expect(body).toEqual({
      tag: "SRV-0042",
      name: "Rack server",
      type: "server",
      status: "in_repair",
      owner: "Data centre team",
      location: "Mumbai DC, row 4",
      serial: "00123ABC",
      purchased_at: "2025-11-03",
      warranty_expires_at: "2028-11-02",
      cost_minor: 12500050,
      cost_currency: "USD",
    });
  });

  it("sends a cost with no currency in the default currency the engine converted it in", () => {
    expect(assetsEntity.toPayload(parsed({ cost: 4200000 }), context)).toMatchObject({
      cost_minor: 4200000,
      cost_currency: "INR",
    });
  });

  it("sends no currency without a cost, even though the engine fills a blank currency cell with INR", () => {
    expect(assetsEntity.toPayload(parsed({ cost: null, cost_currency: "INR" }), context)).toEqual({
      tag: "SRV-0042",
      name: "Rack server",
      type: "server",
    });
    expect(assetsEntity.toPayload(parsed({ cost: null, cost_currency: "USD" }), context)).not.toHaveProperty(
      "cost_currency",
    );
  });

  it("keeps a zero cost, which is a real amount", () => {
    expect(assetsEntity.toPayload(parsed({ cost: 0 }), context)).toMatchObject({ cost_minor: 0 });
  });

  it("never sends the read-only count", () => {
    const body = assetsEntity.toPayload(parsed({ open_incidents: 3 }), context);
    expect(body).not.toHaveProperty("open_incidents");
  });

  it("fails the row, with a sentence, when the type is missing", () => {
    expect(() => assetsEntity.toPayload({ tag: "X-1", name: "Thing", type: null }, context)).toThrow(
      new RowProblem("Type is required."),
    );
  });
});

describe("create", () => {
  it("posts the payload to /itsm/assets and returns the tag as the id", async () => {
    const fetchMock = stubFetch(json(201, asset({ tag: "SRV-0042" })));
    const created = await assetsEntity.create(
      { tag: "SRV-0042", name: "Rack server", type: "server", cost_minor: 500000, cost_currency: "INR" },
      context,
    );
    expect(created).toEqual({ id: "SRV-0042" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/assets");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      tag: "SRV-0042",
      name: "Rack server",
      type: "server",
      cost_minor: 500000,
      cost_currency: "INR",
    });
  });

  it("lets a duplicate tag's 409 through, so the engine can report the row as already existing", async () => {
    stubFetch(json(409, { title: "Conflict", status: 409, detail: "That tag is already in use." }));
    await expect(
      assetsEntity.create({ tag: "LT-2041", name: "Laptop", type: "laptop" }, context),
    ).rejects.toMatchObject({ status: 409 });
  });
});

describe("export", () => {
  const exportSource = assetsEntity.export;

  it("asks for the screen's current filters, one page at a time", async () => {
    const fetchMock = stubFetch(
      json(200, { items: [asset()], total: 1, page: 2, size: 200, pages: 2 }),
    );
    const page = await exportSource?.fetchPage({
      page: 2,
      size: 200,
      filters: { search: "dell", type: "laptop", status: "in_use" },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.startsWith("/api/v1/itsm/assets?")).toBe(true);
    expect(Object.fromEntries(new URL(url, "http://gateway.test").searchParams)).toEqual({
      q: "dell",
      type: "laptop",
      status: "in_use",
      page: "2",
      size: "200",
    });
    expect(page).toMatchObject({ total: 1, pages: 2 });
  });

  it("sends no filter for a screen that has none", async () => {
    const fetchMock = stubFetch(json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }));
    await exportSource?.fetchPage({
      page: 1,
      size: 200,
      filters: { search: "", type: "all", status: "all" },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(Object.fromEntries(new URL(url, "http://gateway.test").searchParams)).toEqual({
      page: "1",
      size: "200",
    });
  });

  it("writes an asset in the sheet's own words, with cost as an exact major-unit decimal", () => {
    expect(
      exportSource?.toRow(
        asset({
          type: "network",
          status: "in_repair",
          cost_minor: 1250,
          cost_currency: "KWD",
          open_incident_ids: ["INC-1", "INC-2"],
        }),
        {},
      ),
    ).toEqual({
      tag: "LT-2041",
      name: "Dell Latitude 7440",
      type: "Network",
      status: "In repair",
      owner: "Anita Desai",
      location: "Bengaluru HQ",
      serial: "8H2K3Z3",
      purchased_at: "2025-08-14",
      warranty_expires_at: "2028-08-13",
      cost: "1.250",
      cost_currency: "KWD",
      open_incidents: 2,
    });
  });

  it("leaves unrecorded values blank and writes only declared columns", () => {
    const row = exportSource?.toRow(
      asset({ owner: null, purchased_at: null, warranty_expires_at: null, cost_minor: 0 }),
      {},
    );
    expect(row).toMatchObject({
      owner: null,
      purchased_at: null,
      warranty_expires_at: null,
      cost: "0.00",
    });
    const declared = new Set(assetsEntity.columns.map((c) => c.key));
    expect(Object.keys(row ?? {}).every((key) => declared.has(key))).toBe(true);
  });
});
