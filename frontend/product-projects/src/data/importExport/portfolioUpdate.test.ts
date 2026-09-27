/**
 * Updating existing projects from a sheet, tested directly: the pure planner from
 * `@opsgrids/client` is given a parsed sheet row and a stored project, and the requests it would
 * make go to a stubbed `fetch`. The engine and the dialog are not involved.
 *
 * `PATCH /projects-workspace/projects/{code}` silently drops keys it does not know and answers
 * 200, answers a null on a required column with a 409 and on either date with a 500, checks the
 * date order only after merging with the stored dates, and relabels a budget when the currency
 * changes instead of converting it. So the body must hold only what changed, never a null the
 * survey did not find nullable, and those two rules are checked before anything is sent.
 */
import { RowProblem, indexExisting, makeLookup, matchRow, planUpdate } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow, UpdateSpec } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectOut, ProjectUpdate } from "@/api/types";

import { portfolioEntity } from "./portfolio";

const signal = new AbortController().signal;

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

function project(over: Partial<ProjectOut> = {}): ProjectOut {
  return {
    id: "proj-1",
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

const STORED = project();

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "Meera Iyer", value: "p-01" },
    { label: "Arjun Rao", value: "p-02" },
    { label: "Sam Lee", value: "p-03" },
    { label: "Sam Lee", value: "p-04" },
  ]),
};

const context: ImportContext = { lookups: LOOKUPS, signal };

function spec(): UpdateSpec<ProjectOut> {
  const update = portfolioEntity.update;
  if (update === undefined) throw new Error("The portfolio entity has no update spec");
  return update;
}

function plan(row: ParsedRow, cleared: readonly string[] = [], existing: ProjectOut = STORED) {
  return planUpdate(portfolioEntity.columns, spec(), row, new Set(cleared), existing, context);
}

/** Runs the plan against a gateway that accepts the PATCH, and returns the request it made. */
async function sent(row: ParsedRow, cleared: readonly string[] = [], existing: ProjectOut = STORED) {
  const fetchMock = stubApi({
    [`PATCH /api/v1/projects-workspace/projects/${existing.code}`]: () => json(200, existing),
  });
  const p = plan(row, cleared, existing);
  expect(p, "the row should change something").not.toBeNull();
  await p?.run();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, method: init.method, body: JSON.parse(init.body as string) as Record<string, unknown> };
}

/** Every column of the stored project, as a sheet that has not been touched would carry it. The
 *  currency cell is blank: on an update a blank currency stays blank. */
const UNTOUCHED: ParsedRow = {
  code: "PRJ-104",
  name: "Customer portal redesign",
  client: "Northwind Traders",
  owner: "Meera Iyer",
  phase: "build",
  health: "at_risk",
  progress_pct: 45,
  start_date: "2026-03-02",
  target_date: "2026-09-30",
  budget: 250000000,
  spent: 112000050,
  currency: null,
  team_size: 6,
  summary: "Rebuild the portal.",
};

describe("the update spec", () => {
  it("matches a project by Record ID, else by code", () => {
    expect(spec().idOf(STORED)).toBe("proj-1");
    expect(spec().keyColumn).toBe("code");
    expect(spec().keyOf?.(STORED)).toBe("PRJ-104");
    expect(portfolioEntity.recordIdKey).toBe("id");
  });

  it("reads amounts in the project's own currency when the row names none", () => {
    expect(spec().currencyOf?.(project({ currency: "USD" }))).toBe("USD");
  });

  it("names only importable columns, never the system's figures or the Record ID", () => {
    const importable = new Set(portfolioEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key));
    const named = spec().fields.map((f) => f.column);
    for (const column of named) expect(importable.has(column), column).toBe(true);
    for (const forbidden of ["open_tasks", "milestone_count", "id"]) expect(named).not.toContain(forbidden);
  });

  it("keeps the code immutable and nothing else", () => {
    expect(spec().fields.filter((f) => f.immutable === true).map((f) => f.column)).toEqual(["code"]);
  });

  it("lets exactly the columns the endpoint accepts a clear for be cleared, each with the value it accepts", () => {
    const clearable = Object.fromEntries(
      spec()
        .fields.filter((f) => f.clear !== undefined)
        .map((f) => [f.column, f.clear]),
    );
    expect(clearable).toEqual({ client: "", owner: null, summary: "" });
  });

  it("sends each column under the key the endpoint reads it from", () => {
    const keys = Object.fromEntries(spec().fields.map((f) => [f.column, f.field ?? f.column]));
    expect(keys).toEqual({
      code: "code",
      name: "name",
      client: "client",
      owner: "owner_ref",
      phase: "phase",
      health: "health",
      progress_pct: "progress_pct",
      start_date: "start_date",
      target_date: "target_date",
      budget: "budget_minor",
      spent: "spent_minor",
      currency: "currency",
      team_size: "team_size",
      summary: "summary",
    });
  });

  it("sends only keys the update schema has, each named outright, and never the code or the system's figures", () => {
    const schemaKeys = [
      "name",
      "client",
      "owner_ref",
      "phase",
      "health",
      "progress_pct",
      "start_date",
      "target_date",
      "budget_minor",
      "spent_minor",
      "currency",
      "team_size",
      "summary",
    ] as const satisfies readonly (keyof ProjectUpdate)[];
    const sendable = spec().fields.filter((f) => f.immutable !== true);
    expect(sendable.every((f) => f.field !== undefined)).toBe(true);
    expect(sendable.map((f) => f.field).sort()).toEqual([...schemaKeys].sort());
  });

  it("calls a project by its code and name", () => {
    expect(spec().recordLabel?.(project({ code: "PRJ-104", name: "Customer portal redesign" }))).toBe(
      "PRJ-104 · Customer portal redesign",
    );
  });

  it("keeps the import's own bound on team size, because a larger number is a server error", () => {
    const column = portfolioEntity.columns.find((c) => c.key === "team_size");
    expect(column).toMatchObject({ min: 0, max: 2_147_483_647 });
    expect(spec().fields.map((f) => f.column)).toContain("team_size");
  });
});

describe("matching a row to a stored project", () => {
  const other = project({ id: "proj-2", code: "PRJ-105" });
  const index = indexExisting(spec(), [STORED, other]);

  it("finds the project a Record ID names, whatever the code cell says", () => {
    expect(matchRow(index, "proj-2", "PRJ-104")).toMatchObject({ kind: "found", by: "id", item: other });
  });

  it("refuses a Record ID no project has", () => {
    expect(matchRow(index, "proj-404", "PRJ-104")).toEqual({ kind: "unknown-id" });
  });

  it("finds the project by the code as it is stored, whatever spaces surround it", () => {
    expect(matchRow(index, null, "PRJ-105")).toMatchObject({ kind: "found", by: "key", item: other });
    expect(matchRow(index, null, " PRJ-105 ")).toMatchObject({ kind: "found", by: "key", item: other });
  });

  it("leaves the match rule at the engine's default, exact: the server keeps PRJ-104 and prj-104 as two projects", () => {
    expect(spec().keyMatch).toBeUndefined();
  });

  it("treats a code nobody has as a new project", () => {
    expect(matchRow(index, null, "PRJ-999")).toEqual({ kind: "new" });
  });

  it("refuses a code two projects share instead of guessing", () => {
    const twins = indexExisting(spec(), [STORED, project({ id: "proj-3", code: "PRJ-104" })]);
    expect(matchRow(twins, null, "PRJ-104")).toEqual({ kind: "ambiguous" });
  });
});

describe("loading the projects to compare with", () => {
  it("reads every page of the whole list, once each project", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/projects-workspace/projects": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, { items: [project({ id: "a" }), project({ id: "b" })], total: 3, page: 1, size: 200, pages: 2 })
          : json(200, { items: [project({ id: "b" }), project({ id: "c" })], total: 3, page: 2, size: 200, pages: 2 }),
    });
    const items = await spec().loadExisting(context);
    expect(items.map((p) => p.id)).toEqual(["a", "b", "c"]);
    const params = fetchMock.mock.calls.map(([url]) => new URL(url, "http://gateway.test").searchParams);
    expect(params.map((p) => `${p.get("page")}/${p.get("size")}`)).toEqual(["1/200", "2/200"]);
    expect(params.every((p) => !p.has("q") && !p.has("health") && !p.has("phase"))).toBe(true);
  });

  it("refuses a list that stays short of the server's own total rather than diff against part of it", async () => {
    stubApi({
      "GET /api/v1/projects-workspace/projects": () =>
        json(200, { items: [project()], total: 4, page: 1, size: 200, pages: 1 }),
    });
    await expect(spec().loadExisting(context)).rejects.toThrow(/changed while it was being read/);
  });

  it("hands the dialog's signal to every page request, so leaving the screen aborts the one in flight", async () => {
    const controller = new AbortController();
    const seen: (AbortSignal | null | undefined)[] = [];
    stubApi({
      "GET /api/v1/projects-workspace/projects": (url, init) => {
        seen.push(init?.signal);
        return url.searchParams.get("page") === "1"
          ? json(200, { items: [project({ id: "a" })], total: 2, page: 1, size: 200, pages: 2 })
          : json(200, { items: [project({ id: "b" })], total: 2, page: 2, size: 200, pages: 2 });
      },
    });
    await spec().loadExisting({ lookups: LOOKUPS, signal: controller.signal });
    expect(seen).toHaveLength(2);
    expect(seen.every((given) => given === controller.signal)).toBe(true);
  });

  it("stops asking for pages once cancelled", async () => {
    const controller = new AbortController();
    let asked = 0;
    stubApi({
      "GET /api/v1/projects-workspace/projects": () => {
        asked += 1;
        controller.abort();
        return json(200, { items: [project({ id: "a" })], total: 5, page: 1, size: 200, pages: 3 });
      },
    });
    await expect(spec().loadExisting({ lookups: LOOKUPS, signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(asked).toBe(1);
  });

  it("hands the signal to the owner lookup's page requests too", async () => {
    const controller = new AbortController();
    const seen: (AbortSignal | null | undefined)[] = [];
    stubApi({
      "GET /api/v1/projects-workspace/members": (_url, init) => {
        seen.push(init?.signal);
        return json(200, { items: [{ id: "p-01", name: "Meera Iyer", role: "", directory_id: null }], total: 1, page: 1, size: 200, pages: 1 });
      },
    });
    await portfolioEntity.loadLookups?.(controller.signal);
    expect(seen).toEqual([controller.signal]);
  });
});

describe("what a row changes", () => {
  it("changes nothing, and makes no request, when every cell agrees with the stored project", () => {
    const fetchMock = stubApi({});
    expect(plan(UNTOUCHED)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves everything alone when every cell is blank", () => {
    const blank: ParsedRow = Object.fromEntries(Object.keys(UNTOUCHED).map((key) => [key, null]));
    expect(plan(blank)).toBeNull();
  });

  it("ignores the columns the system owns, so an exported file can be edited and sent back", () => {
    expect(plan({ ...UNTOUCHED, open_tasks: 99, milestone_count: 9, id: "some-other-id" })).toBeNull();
  });

  it.each([
    ["Name", { name: "Customer portal v2" }, { name: "Customer portal v2" }],
    ["Client", { client: "Contoso" }, { client: "Contoso" }],
    ["Owner", { owner: "arjun rao" }, { owner_ref: "p-02" }],
    ["Phase", { phase: "launch" }, { phase: "launch" }],
    ["Health", { health: "delayed" }, { health: "delayed" }],
    ["Progress (%)", { progress_pct: 60 }, { progress_pct: 60 }],
    ["Start date", { start_date: "2026-03-09" }, { start_date: "2026-03-09" }],
    ["Target date", { target_date: "2026-10-31" }, { target_date: "2026-10-31" }],
    ["Budget", { budget: 260000000 }, { budget_minor: 260000000 }],
    ["Spent", { spent: 130000000 }, { spent_minor: 130000000 }],
    ["Team size", { team_size: 8 }, { team_size: 8 }],
    ["Summary", { summary: "A new summary." }, { summary: "A new summary." }],
  ] satisfies [string, ParsedRow, Record<string, string | number>][])(
    "sends only %s, under the endpoint's own key, when only that cell differs",
    async (header, cells, wire) => {
      expect(plan({ ...UNTOUCHED, ...cells })?.changed).toEqual([header]);
      const request = await sent({ ...UNTOUCHED, ...cells });
      expect(request).toEqual({ url: "/api/v1/projects-workspace/projects/PRJ-104", method: "PATCH", body: wire });
    },
  );

  it("sends several changes in one request, and nothing that did not change", async () => {
    const { body } = await sent({ ...UNTOUCHED, health: "on_track", progress_pct: 50, phase: "build", team_size: 6 });
    expect(body).toEqual({ health: "on_track", progress_pct: 50 });
    expect(Object.values(body).every((value) => value !== null)).toBe(true);
  });

  it("updates by the code in the URL and never puts the code in the body", async () => {
    const { url, body } = await sent({ ...UNTOUCHED, code: "PRJ-104", name: "Renamed" });
    expect(url).toBe("/api/v1/projects-workspace/projects/PRJ-104");
    expect(body).not.toHaveProperty("code");
  });

  it("never sends the system's figures, which this endpoint would drop without saying so", async () => {
    const { body } = await sent({ ...UNTOUCHED, name: "Renamed", open_tasks: 99, milestone_count: 9 });
    expect(Object.keys(body)).toEqual(["name"]);
  });

  it("treats zero as a real value for progress, team size and money, not as blank", async () => {
    const { body } = await sent({ progress_pct: 0, team_size: 0, budget: 0, spent: 0 });
    expect(body).toEqual({ progress_pct: 0, team_size: 0, budget_minor: 0, spent_minor: 0 });
  });

  it("compares a summary regardless of the line break it was written with", () => {
    const multi = project({ summary: "Line one\nLine two" });
    expect(plan({ summary: "Line one\r\nLine two" }, [], multi)).toBeNull();
    expect(plan({ summary: "Line one\nLine 2" }, [], multi)?.changed).toEqual(["Summary"]);
  });

  it("compares a summary with its ends trimmed, as a sheet hands it back: an untouched exported cell is no change", () => {
    const padded = project({ summary: "  Rebuild the portal.\n\n" });
    expect(plan({ summary: "Rebuild the portal." }, [], padded)).toBeNull();
    expect(plan({ summary: "Rebuild the portal now." }, [], padded)?.changed).toEqual(["Summary"]);
  });

  it("keeps a difference inside the summary: only the ends are trimmed", () => {
    const multi = project({ summary: "Line one\n\nLine two" });
    expect(plan({ summary: "Line one\nLine two" }, [], multi)?.changed).toEqual(["Summary"]);
  });
});

describe("the owner", () => {
  it("is sent as the member's ref, not the name the sheet gave", async () => {
    const { body } = await sent({ owner: "ARJUN  RAO" });
    expect(body).toEqual({ owner_ref: "p-02" });
  });

  it("is no change when the sheet names the member the project already has", () => {
    expect(plan({ owner: "meera iyer" })).toBeNull();
  });

  it("assigns an owner to a project that has none", async () => {
    const unowned = project({ owner: { id: "", name: "Unassigned", role: "" } });
    const { body } = await sent({ owner: "Meera Iyer" }, [], unowned);
    expect(body).toEqual({ owner_ref: "p-01" });
  });

  const hint = portfolioEntity.columns.find((c) => c.key === "owner")?.lookupHintUpdate ?? "";

  it("refuses a name the team list does not hold, in the engine's words and with the hint for updating", () => {
    expect(hint).not.toBe("");
    expect(() => plan({ owner: "Nobody Here" })).toThrow(RowProblem);
    expect(() => plan({ owner: "Nobody Here" })).toThrow(
      `'Nobody Here' is not one of the accepted values for Owner (see the Lists sheet). ${hint}`,
    );
  });

  it("refuses a name two members share rather than guess", () => {
    expect(() => plan({ owner: "Sam Lee" })).toThrow(
      `'Sam Lee' matches more than one of the accepted values for Owner, so it cannot be chosen. ${hint}`,
    );
  });

  it("says a team list that failed to load could not be checked, instead of calling every name unknown", () => {
    const none: ImportContext = { lookups: {}, signal };
    expect(() => planUpdate(portfolioEntity.columns, spec(), { owner: "Arjun Rao" }, new Set(), STORED, none)).toThrow(
      "The accepted values for Owner could not be loaded, so it cannot be checked",
    );
  });

  describe("when the sheet repeats the owner the project already has", () => {
    const planIn = (lookups: LookupSet, row: ParsedRow, existing: ProjectOut) =>
      planUpdate(portfolioEntity.columns, spec(), row, new Set(), existing, { lookups, signal });
    const sharedName = project({ owner: { id: "p-03", name: "Sam Lee", role: "" } });
    const leftTheTeam = project({ owner: { id: "p-99", name: "Priya Nair", role: "" } });

    it("is no change when two members share that name: the export wrote it, so it is what the record has", () => {
      expect(planIn(LOOKUPS, { ...UNTOUCHED, owner: "Sam Lee" }, sharedName)).toBeNull();
    });

    it("does not hold the rest of the row back", () => {
      expect(planIn(LOOKUPS, { owner: "Sam Lee", health: "on_track" }, sharedName)?.changed).toEqual(["Health"]);
    });

    it("is still refused when it is a change: it could be either member", () => {
      expect(() => planIn(LOOKUPS, { owner: "Sam Lee" }, STORED)).toThrow("matches more than one of the accepted values");
    });

    it("is no change when the member has left the team list", () => {
      expect(planIn(LOOKUPS, { ...UNTOUCHED, owner: "Priya Nair" }, leftTheTeam)).toBeNull();
    });

    it("is no change when the team list could not be loaded", () => {
      expect(planIn({}, { ...UNTOUCHED, owner: "Meera Iyer" }, STORED)).toBeNull();
    });

    it("still moves a project whose member left to one who is on the list", () => {
      expect(planIn(LOOKUPS, { owner: "Arjun Rao" }, leftTheTeam)?.changed).toEqual(["Owner"]);
    });

    it("still refuses a change to a name that is not on the list", () => {
      expect(() => planIn(LOOKUPS, { owner: "Nobody Here" }, leftTheTeam)).toThrow("is not one of the accepted values");
    });
  });

  it("is cleared with an explicit null", async () => {
    const { body } = await sent({ owner: null }, ["owner"]);
    expect(body).toStrictEqual({ owner_ref: null });
  });

  it("is left alone when already unassigned and cleared again", () => {
    const unowned = project({ owner: { id: "", name: "Unassigned", role: "" } });
    expect(plan({ owner: null }, ["owner"], unowned)).toBeNull();
  });
});

describe("the dates", () => {
  it("accepts a start and a target on the same day", async () => {
    const { body } = await sent({ start_date: "2026-09-30" });
    expect(body).toEqual({ start_date: "2026-09-30" });
  });

  it("refuses a target date before the stored start date, before anything is sent", () => {
    const fetchMock = stubApi({});
    expect(() => plan({ target_date: "2026-03-01" })).toThrow(RowProblem);
    expect(() => plan({ target_date: "2026-03-01" })).toThrow(
      "The target date cannot be before the start date. With this row the project would start on 2026-03-02 and be due on 2026-03-01.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a start date after the stored target date", () => {
    expect(() => plan({ start_date: "2026-10-01" })).toThrow(
      "With this row the project would start on 2026-10-01 and be due on 2026-09-30.",
    );
  });

  it("refuses two dates in the sheet that are out of order together", () => {
    expect(() => plan({ start_date: "2026-12-01", target_date: "2026-11-01" })).toThrow(RowProblem);
  });

  it("accepts two changed dates that are in order together, though each alone would not be", async () => {
    const { body } = await sent({ start_date: "2027-01-04", target_date: "2027-06-30" });
    expect(body).toEqual({ start_date: "2027-01-04", target_date: "2027-06-30" });
  });

  it("does not let a row that leaves the dates alone trip over them", async () => {
    const { body } = await sent({ name: "Renamed" });
    expect(body).toEqual({ name: "Renamed" });
  });

  it.each([
    ["start_date", "Start date"],
    ["target_date", "Target date"],
  ])("refuses to clear %s: a null there is a server error", (column, header) => {
    expect(() => plan({ [column]: null }, [column])).toThrow(RowProblem);
    expect(() => plan({ [column]: null }, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("the currency and the amounts", () => {
  it("reads amounts with no currency in the project's own currency: a blank currency is no change", () => {
    expect(plan({ ...UNTOUCHED, currency: null })).toBeNull();
    expect(plan({ budget: 260000000, currency: null })?.changed).toEqual(["Budget"]);
  });

  it("sends an amount with no currency when the row names none", async () => {
    const { body } = await sent({ budget: 260000000, currency: null });
    expect(body).toEqual({ budget_minor: 260000000 });
    expect(body).not.toHaveProperty("currency");
  });

  it("accepts the stored currency in any case as no change", () => {
    expect(plan({ currency: "inr", budget: 250000000, spent: 112000050 })).toBeNull();
  });

  it("changes the currency together with both amounts, in one request", async () => {
    const { body } = await sent({ currency: "usd", budget: 3000000, spent: 1350000 });
    expect(body).toEqual({ budget_minor: 3000000, spent_minor: 1350000, currency: "USD" });
  });

  it("sends the currency alone when both amounts, read in the new currency, are the ones already stored", async () => {
    const { body } = await sent({ currency: "USD", budget: 250000000, spent: 112000050 });
    expect(body).toEqual({ currency: "USD" });
  });

  it("refuses a currency change that leaves the spend unstated: the server would relabel it, not convert it", () => {
    expect(() => plan({ currency: "USD", budget: 3000000, spent: null })).toThrow(RowProblem);
    expect(() => plan({ currency: "USD", budget: 3000000, spent: null })).toThrow(
      "Currency would change from INR to USD, but changing it does not convert amounts. Give Spent in USD too, or leave Currency blank.",
    );
  });

  it("names both amounts when the row states neither", () => {
    expect(() => plan({ currency: "USD" })).toThrow("Give Budget and Spent in USD too");
  });

  it("refuses a blank amount that is 0 today too, so the rule does not depend on what is on file", () => {
    const fresh = project({ budget_minor: 0, spent_minor: 0 });
    expect(() => plan({ currency: "EUR", budget: 500000 }, [], fresh)).toThrow("Give Spent in EUR too");
    expect(() => plan({ currency: "EUR" }, [], fresh)).toThrow("Give Budget and Spent in EUR too");
  });

  it("lets stated amounts of 0 change the currency of a project that has none", async () => {
    const fresh = project({ budget_minor: 0, spent_minor: 0 });
    const { body } = await sent({ currency: "EUR", budget: 0, spent: 0 }, [], fresh);
    expect(body).toEqual({ currency: "EUR" });
  });

  it.each([
    ["budget", "Budget"],
    ["spent", "Spent"],
    ["currency", "Currency"],
  ])("refuses to clear %s: a null there is a conflict", (column, header) => {
    expect(() => plan({ [column]: null }, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("clearing with [clear]", () => {
  it.each([
    ["client", "Client"],
    ["summary", "Summary"],
  ])("empties %s with an empty string, never null: the column is never null", async (column, header) => {
    expect(plan({ [column]: null }, [column])?.changed).toEqual([header]);
    const { body } = await sent({ [column]: null }, [column]);
    expect(body).toStrictEqual({ [column]: "" });
  });

  it.each([
    ["name", "Name"],
    ["phase", "Phase"],
    ["health", "Health"],
    ["progress_pct", "Progress (%)"],
    ["team_size", "Team size"],
  ])("refuses to clear %s, which the server would answer with a conflict", (column, header) => {
    expect(() => plan({ [column]: null }, [column])).toThrow(RowProblem);
    expect(() => plan({ [column]: null }, [column])).toThrow(`${header} cannot be cleared`);
  });

  it("does nothing to a field that is already empty", () => {
    const bare = project({ client: "", summary: "" });
    expect(plan({ client: null, summary: null }, ["client", "summary"], bare)).toBeNull();
  });

  it("clears only what the row asks and leaves a blank cell's field alone", async () => {
    const { body } = await sent({ client: null, summary: null }, ["client"]);
    expect(body).toEqual({ client: "" });
  });

  it("sends a null only for the owner, whatever else a row clears", async () => {
    const { body } = await sent({ client: null, owner: null, summary: null }, ["client", "owner", "summary"]);
    expect(body).toStrictEqual({ client: "", owner_ref: null, summary: "" });
  });
});

describe("the code is immutable", () => {
  it("accepts a cell that agrees with the stored code", () => {
    expect(plan({ code: "PRJ-104" })).toBeNull();
  });

  it("refuses a cell that differs from the stored code only in capitals: the server counts them as two codes", () => {
    expect(() => plan({ code: "prj-104" })).toThrow(
      "Project code cannot be changed once the record exists (it is 'PRJ-104')",
    );
  });

  it("refuses a different code, saying what is stored: the server would drop it and answer 200", () => {
    expect(() => plan({ code: "PRJ-999" })).toThrow(RowProblem);
    expect(() => plan({ code: "PRJ-999" })).toThrow(
      "Project code cannot be changed once the record exists (it is 'PRJ-104')",
    );
  });

  it("refuses [clear] on it", () => {
    expect(() => plan({ code: null }, ["code"])).toThrow("Project code cannot be cleared");
  });
});

describe("what the server can answer", () => {
  it.each([
    [404, "That project is not part of this organization"],
    [403, "Missing permission: projects.portfolio.project.write"],
    [409, "That conflicts with something that already exists"],
    [422, "target_date must be on or after start_date"],
  ])("lets a %s through with the server's own sentence", async (status, detail) => {
    stubApi({
      "PATCH /api/v1/projects-workspace/projects/PRJ-104": () => json(status, { title: "Refused", status, detail }),
    });
    const attempt = plan({ name: "Renamed" })?.run();
    await expect(attempt).rejects.toMatchObject({ status });
    await expect(attempt).rejects.toThrow(detail);
  });
});
