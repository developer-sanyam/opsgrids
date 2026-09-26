/**
 * The Projects portfolio import/export entity, tested directly: what its columns promise, the
 * request body a sheet row turns into, what the owner lookup is built from, and what an export asks
 * for and writes. The engine and the dialog are not involved; requests go to a stubbed `fetch`.
 */
import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemberOut, ProjectOut } from "@/api/types";

import { portfolioEntity } from "./portfolio";

const signal = new AbortController().signal;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
  });
}

type Handler = (url: URL) => Response;

/** A stubbed gateway: one handler per `METHOD /path`; any other request fails the test. */
function stubApi(handlers: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://gateway.test");
    const handler = handlers[`${init?.method ?? "GET"} ${url.pathname}`];
    if (handler === undefined) throw new Error(`Unexpected request: ${input}`);
    return handler(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function member(id: string, name: string): MemberOut {
  return { id, name, role: "Delivery lead", directory_id: null };
}

function project(over: Partial<ProjectOut> = {}): ProjectOut {
  return {
    id: "proj-uuid-1",
    code: "PRJ-104",
    name: "Customer portal redesign",
    client: "Northwind Traders",
    owner: { id: "p-01", name: "Meera Iyer", role: "Delivery lead" },
    phase: "build",
    health: "at_risk",
    progress_pct: 45,
    start_date: "2026-03-02",
    target_date: "2026-09-30",
    budget_minor: 250000000,
    spent_minor: 112000050,
    currency: "INR",
    team_size: 6,
    open_tasks: 12,
    milestone_count: 4,
    summary: "Rebuild the portal.",
    ...over,
  };
}

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "Meera Iyer", value: "p-01" },
    { label: "Arjun Rao", value: "p-02" },
  ]),
};

const context: ImportContext = { lookups: LOOKUPS, signal };

function parsed(over: ParsedRow = {}): ParsedRow {
  return {
    code: "PRJ-200",
    name: "Data warehouse",
    start_date: "2026-04-01",
    target_date: "2026-10-31",
    ...over,
  };
}

describe("columns", () => {
  it("asks for the code, the name and both dates", () => {
    const required = portfolioEntity.columns.filter((c) => c.required === true);
    expect(required.map((c) => c.header)).toEqual([
      "Project code",
      "Name",
      "Start date",
      "Target date",
    ]);
  });

  it("offers every phase and health the API takes, by their screen labels", () => {
    const options = (key: string) =>
      portfolioEntity.columns.find((c) => c.key === key)?.options?.map((o) => [o.value, o.label]);
    expect(options("phase")).toEqual([
      ["discovery", "Discovery"],
      ["design", "Design"],
      ["build", "Build"],
      ["stabilize", "Stabilize"],
      ["launch", "Launch"],
      ["live", "Live"],
    ]);
    expect(options("health")).toEqual([
      ["on_track", "On track"],
      ["at_risk", "At risk"],
      ["delayed", "Delayed"],
    ]);
  });

  it("holds budget and spend as money that names the currency column", () => {
    for (const key of ["budget", "spent"]) {
      const column = portfolioEntity.columns.find((c) => c.key === key);
      expect(column, key).toMatchObject({ kind: "money", currencyKey: "currency", min: 0 });
    }
    expect(portfolioEntity.columns.map((c) => c.key)).toContain("currency");
  });

  it("keeps the project code as text, and system figures read-only", () => {
    const byKey = Object.fromEntries(portfolioEntity.columns.map((c) => [c.key, c]));
    expect(byKey.code?.kind).toBe("text");
    const readOnly = portfolioEntity.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(readOnly).toEqual(["open_tasks", "milestone_count", "id"]);
  });

  it("points the owner column at a set that loadLookups returns", async () => {
    stubApi({
      "GET /api/v1/projects-workspace/members": () =>
        json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await portfolioEntity.loadLookups?.(signal);
    const named = portfolioEntity.columns.flatMap((c) => (c.lookup === undefined ? [] : [c.lookup]));
    expect(named).toEqual(["owners"]);
    expect(Object.keys(lookups ?? {})).toEqual(named);
  });
});

describe("sample rows", () => {
  it("fill the required columns and stay inside every column's own rules", () => {
    const importable = portfolioEntity.columns.filter((c) => c.readOnly !== true);
    expect(portfolioEntity.sampleRows.length).toBeGreaterThanOrEqual(2);
    for (const sample of portfolioEntity.sampleRows) {
      for (const column of importable) {
        const cell = sample[column.key];
        if (column.required === true) expect(cell, column.header).toBeTruthy();
        if (typeof cell === "number") {
          if (column.min !== undefined) expect(cell).toBeGreaterThanOrEqual(column.min);
          if (column.max !== undefined) expect(cell).toBeLessThanOrEqual(column.max);
        }
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

  it("use only known importable columns, and put every target date on or after its start", () => {
    const importable = new Set(
      portfolioEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key),
    );
    for (const sample of portfolioEntity.sampleRows) {
      for (const key of Object.keys(sample)) expect(importable.has(key), key).toBe(true);
      expect(String(sample.target_date) >= String(sample.start_date)).toBe(true);
    }
  });

  it("pass the entity's own cross-column rule", () => {
    for (const sample of portfolioEntity.sampleRows) {
      const asParsed: ParsedRow = {
        start_date: String(sample.start_date),
        target_date: String(sample.target_date),
      };
      expect(portfolioEntity.validateRow?.(asParsed, context)).toBeNull();
    }
  });
});

describe("validateRow", () => {
  it("refuses a target date before the start date", () => {
    expect(
      portfolioEntity.validateRow?.(
        { start_date: "2026-04-01", target_date: "2026-03-31" },
        context,
      ),
    ).toBe("The target date cannot be before the start date.");
  });

  it("accepts a project that starts and ends on the same day", () => {
    expect(
      portfolioEntity.validateRow?.(
        { start_date: "2026-04-01", target_date: "2026-04-01" },
        context,
      ),
    ).toBeNull();
  });
});

describe("toPayload", () => {
  it("sends only the code, name and dates when nothing else is filled", () => {
    expect(portfolioEntity.toPayload(parsed(), context)).toEqual({
      code: "PRJ-200",
      name: "Data warehouse",
      start_date: "2026-04-01",
      target_date: "2026-10-31",
    });
  });

  it("carries every filled column, resolving the owner's name to the member's ref", () => {
    const body = portfolioEntity.toPayload(
      parsed({
        client: "Fabrikam Retail",
        owner: "arjun rao",
        phase: "launch",
        health: "delayed",
        progress_pct: 80,
        budget: 12000050,
        spent: 13150000,
        currency: "usd",
        team_size: 5,
        summary: "Move payments.",
      }),
      context,
    );
    expect(body).toEqual({
      code: "PRJ-200",
      name: "Data warehouse",
      start_date: "2026-04-01",
      target_date: "2026-10-31",
      client: "Fabrikam Retail",
      owner_ref: "p-02",
      phase: "launch",
      health: "delayed",
      progress_pct: 80,
      budget_minor: 12000050,
      spent_minor: 13150000,
      currency: "USD",
      team_size: 5,
      summary: "Move payments.",
    });
  });

  it("sends money with no currency in the default currency the engine converted it in", () => {
    expect(portfolioEntity.toPayload(parsed({ budget: 5000000 }), context)).toMatchObject({
      budget_minor: 5000000,
      currency: "INR",
    });
  });

  it("keeps a zero progress, spend or team size, which are real values", () => {
    expect(
      portfolioEntity.toPayload(parsed({ progress_pct: 0, spent: 0, team_size: 0 }), context),
    ).toMatchObject({ progress_pct: 0, spent_minor: 0, team_size: 0 });
  });

  it("leaves the project unassigned when the owner is blank", () => {
    expect(portfolioEntity.toPayload(parsed({ owner: null }), context)).not.toHaveProperty(
      "owner_ref",
    );
  });

  it("never sends the counts the system keeps", () => {
    const body = portfolioEntity.toPayload(parsed({ open_tasks: 9, milestone_count: 2 }), context);
    expect(body).not.toHaveProperty("open_tasks");
    expect(body).not.toHaveProperty("milestone_count");
  });

  it("refuses an owner who is not a team member, and says where to add them", () => {
    const attempt = () => portfolioEntity.toPayload(parsed({ owner: "Zed Nobody" }), context);
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(
      'There is no team member called "Zed Nobody". Add them on the Team screen first, or leave Owner blank.',
    );
  });

  it("refuses an owner name that two members share, and says what to do", () => {
    const shared: ImportContext = {
      signal,
      lookups: {
        owners: makeLookup([
          { label: "Sam Lee", value: "p-07" },
          { label: "Sam Lee", value: "p-08" },
        ]),
      },
    };
    expect(() => portfolioEntity.toPayload(parsed({ owner: "sam lee" }), shared)).toThrow(
      'More than one team member is called "sam lee", so the owner cannot be chosen. Rename one of them on the Team screen, then import again.',
    );
  });

  it("fails the row rather than crash when a required date is blank", () => {
    expect(() => portfolioEntity.toPayload(parsed({ target_date: null }), context)).toThrow(
      new RowProblem("Target date is required."),
    );
  });
});

describe("create", () => {
  it("posts the payload to /projects-workspace/projects and returns the new id", async () => {
    const fetchMock = stubApi({
      "POST /api/v1/projects-workspace/projects": () =>
        json(201, project({ id: "new-project-id", code: "PRJ-200" })),
    });
    const created = await portfolioEntity.create(
      {
        code: "PRJ-200",
        name: "Data warehouse",
        start_date: "2026-04-01",
        target_date: "2026-10-31",
        owner_ref: "p-01",
      },
      context,
    );
    expect(created).toEqual({ id: "new-project-id" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      code: "PRJ-200",
      name: "Data warehouse",
      start_date: "2026-04-01",
      target_date: "2026-10-31",
      owner_ref: "p-01",
    });
  });

  it("lets a duplicate code's 409 through, so the engine can report the row as already existing", async () => {
    stubApi({
      "POST /api/v1/projects-workspace/projects": () =>
        json(409, { title: "Conflict", status: 409, detail: "A project coded 'PRJ-104' already exists" }),
    });
    await expect(
      portfolioEntity.create(
        { code: "PRJ-104", name: "Portal", start_date: "2026-03-02", target_date: "2026-09-30" },
        context,
      ),
    ).rejects.toMatchObject({ status: 409 });
  });
});

describe("loadLookups", () => {
  it("reads every page of members, not the first 200", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/projects-workspace/members": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, { items: [member("p-01", "Meera Iyer")], total: 2, page: 1, size: 200, pages: 2 })
          : json(200, { items: [member("p-02", "Arjun Rao")], total: 2, page: 2, size: 200, pages: 2 }),
    });

    const lookups = await portfolioEntity.loadLookups?.(signal);

    const asked = fetchMock.mock.calls.map(([url]) => {
      const params = new URL(url, "http://gateway.test").searchParams;
      return `${params.get("page")}/${params.get("size")}`;
    });
    expect(asked).toEqual(["1/200", "2/200"]);
    expect(lookups?.owners?.labels).toEqual(["Meera Iyer", "Arjun Rao"]);
    expect(lookups?.owners?.resolve(" arjun  RAO ")).toBe("p-02");
  });

  it("fails when the members cannot be read, rather than calling every owner unknown", async () => {
    stubApi({
      "GET /api/v1/projects-workspace/members": () =>
        json(500, { title: "Server error", status: 500 }),
    });
    await expect(portfolioEntity.loadLookups?.(signal)).rejects.toMatchObject({ status: 500 });
  });
});

describe("export", () => {
  const exportSource = portfolioEntity.export;

  it("asks for the screen's current filters and sort, one page at a time", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/projects-workspace/projects": () =>
        json(200, { items: [project()], total: 1, page: 2, size: 200, pages: 2 }),
    });
    const page = await exportSource?.fetchPage({
      page: 2,
      size: 200,
      filters: { q: "portal", health: "at_risk", phase: "build", sort: "target" },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(Object.fromEntries(new URL(url, "http://gateway.test").searchParams)).toEqual({
      q: "portal",
      health: "at_risk",
      phase: "build",
      sort: "target",
      page: "2",
      size: "200",
    });
    expect(page).toMatchObject({ total: 1, pages: 2 });
  });

  it("sends no health or phase for a screen showing all of them", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/projects-workspace/projects": () =>
        json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    await exportSource?.fetchPage({
      page: 1,
      size: 200,
      filters: { q: "", health: "all", phase: "all", sort: "code" },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(Object.fromEntries(new URL(url, "http://gateway.test").searchParams)).toEqual({
      sort: "code",
      page: "1",
      size: "200",
    });
  });

  it("writes a project in the sheet's own words, with money as exact major-unit decimals", () => {
    expect(exportSource?.toRow(project(), {})).toEqual({
      code: "PRJ-104",
      name: "Customer portal redesign",
      client: "Northwind Traders",
      owner: "Meera Iyer",
      phase: "Build",
      health: "At risk",
      progress_pct: 45,
      start_date: "2026-03-02",
      target_date: "2026-09-30",
      budget: "2500000.00",
      spent: "1120000.50",
      currency: "INR",
      team_size: 6,
      summary: "Rebuild the portal.",
      open_tasks: 12,
      milestone_count: 4,
      id: "proj-uuid-1",
    });
  });

  it("writes money by the currency's own decimals", () => {
    const row = exportSource?.toRow(
      project({ currency: "JPY", budget_minor: 1500000, spent_minor: 250000 }),
      {},
    );
    expect(row).toMatchObject({ budget: "1500000", spent: "250000", currency: "JPY" });
  });

  it("leaves the owner blank for an unassigned project, so the file can be re-imported", () => {
    const row = exportSource?.toRow(
      project({ owner: { id: "", name: "Unassigned", role: "" } }),
      {},
    );
    expect(row).toMatchObject({ owner: "" });
  });

  it("writes only columns the entity declares", () => {
    const declared = new Set(portfolioEntity.columns.map((c) => c.key));
    const row = exportSource?.toRow(project(), {});
    expect(Object.keys(row ?? {}).every((key) => declared.has(key))).toBe(true);
  });
});
