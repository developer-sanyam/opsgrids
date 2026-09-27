/**
 * Updating existing employees from a sheet, tested directly: the pure planner from
 * `@opsgrids/client` is given a parsed sheet row and a stored employee, and the requests it would
 * make go to a stubbed `fetch`. The engine and the dialog are not involved.
 *
 * What matters most here is what is NOT sent. `PATCH /employees/{id}` has no null guard and drops
 * keys it does not know while answering 200, and a same-value manager still makes the server walk
 * the reporting chain, so the body must hold only the fields that changed, keyed exactly as the
 * endpoint wants them.
 */
import { RowProblem, indexExisting, makeLookup, matchRow, planUpdate } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow, UpdateSpec } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";
import type { EmployeeOut, LocationOut } from "@/api/types";

import { departmentEntries } from "./departments";
import { employeesEntity } from "./employees";
import { locationEntries } from "./locations";

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

function employee(over: Partial<EmployeeOut> = {}): EmployeeOut {
  return {
    id: "emp-1",
    membership_id: null,
    employee_no: "EMP-0001",
    full_name: "Asha Rao",
    designation: "Engineer",
    job_title: "Engineering",
    department_id: "d-eng",
    location_code: "BLR-HQ",
    employment_type: "full_time",
    work_mode: "hybrid",
    date_of_joining: "2025-06-02",
    reporting_manager_id: "mgr-7",
    dotted_line_manager_id: "mgr-12",
    status: "active",
    date_of_exit: null,
    created_at: "2026-01-05T09:00:00Z",
    hierarchy_version: 1,
    pii: null,
    ...over,
  };
}

const STORED = employee();

function unit(id: string, name: string, children: OrgUnitNode[] = []): OrgUnitNode {
  return {
    id,
    name,
    code: id.toUpperCase(),
    parent_id: null,
    head_membership_id: null,
    kind: "department",
    is_active: true,
    children,
  };
}

function site(code: string, name: string): LocationOut {
  return { id: `loc-${code}`, name, code, timezone: "Asia/Kolkata", country: "IN", is_active: true };
}

const LOOKUPS: LookupSet = {
  departments: makeLookup(
    departmentEntries([
      unit("d-eng", "Engineering", [unit("d-eng-plat", "Platform")]),
      unit("d-ops", "Operations", [unit("d-ops-plat", "Platform")]),
      unit("d-hr", "Human Resources"),
    ]),
  ),
  locations: makeLookup(locationEntries([site("BLR-HQ", "Bengaluru HQ"), site("MUM-01", "Mumbai")])),
  managers: makeLookup([
    { label: "EMP-0001", value: "emp-1" },
    { label: "EMP-0007", value: "mgr-7" },
    { label: "EMP-0012", value: "mgr-12" },
    { label: "EMP-0020", value: "mgr-20" },
  ]),
};

const context: ImportContext = { lookups: LOOKUPS, signal };

function spec(): UpdateSpec<EmployeeOut> {
  const update = employeesEntity.update;
  if (update === undefined) throw new Error("The employees entity has no update spec");
  return update;
}

function plan(row: ParsedRow, cleared: readonly string[] = [], existing: EmployeeOut = STORED) {
  return planUpdate(employeesEntity.columns, spec(), row, new Set(cleared), existing, context);
}

/** Runs the plan against a gateway that accepts the PATCH, and returns the request it made. */
async function sent(row: ParsedRow, cleared: readonly string[] = [], existing: EmployeeOut = STORED) {
  const fetchMock = stubApi({ [`PATCH /api/v1/employees/${existing.id}`]: () => json(200, existing) });
  const p = plan(row, cleared, existing);
  expect(p, "the row should change something").not.toBeNull();
  await p?.run();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, method: init.method, body: JSON.parse(init.body as string) as Record<string, unknown> };
}

/** Every column of the stored employee, as a sheet that has not been touched would carry it. */
const UNTOUCHED: ParsedRow = {
  employee_no: "EMP-0001",
  full_name: "Asha Rao",
  designation: "Engineer",
  job_title: "Engineering",
  department: "Engineering",
  location_code: "Bengaluru HQ",
  employment_type: "full_time",
  work_mode: "hybrid",
  date_of_joining: "2025-06-02",
  reporting_manager: "EMP-0007",
  dotted_line_manager: "EMP-0012",
};

describe("the update spec", () => {
  it("matches an employee by Record ID, else by employee number", () => {
    expect(spec().idOf(STORED)).toBe("emp-1");
    expect(spec().keyColumn).toBe("employee_no");
    expect(spec().keyOf?.(STORED)).toBe("EMP-0001");
  });

  it("names only importable columns, and none of the system's, the status or the sealed details", () => {
    const importable = new Set(employeesEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key));
    const named = spec().fields.map((f) => f.column);
    for (const column of named) expect(importable.has(column), column).toBe(true);
    for (const forbidden of ["status", "date_of_exit", "created_at", "id", "pii", "membership_id"]) {
      expect(named).not.toContain(forbidden);
    }
  });

  it("keeps the employee number immutable and nothing else", () => {
    expect(spec().fields.filter((f) => f.immutable === true).map((f) => f.column)).toEqual(["employee_no"]);
  });

  it("lets exactly the nullable columns be cleared, each with null and never with an empty string", () => {
    const clearable = Object.fromEntries(
      spec()
        .fields.filter((f) => f.clear !== undefined)
        .map((f) => [f.column, f.clear]),
    );
    expect(clearable).toEqual({
      designation: null,
      job_title: null,
      department: null,
      location_code: null,
      date_of_joining: null,
      reporting_manager: null,
      dotted_line_manager: null,
    });
  });

  it("sends each column under the key the endpoint reads it from", () => {
    const keys = Object.fromEntries(spec().fields.map((f) => [f.column, f.field ?? f.column]));
    expect(keys).toEqual({
      employee_no: "employee_no",
      full_name: "full_name",
      designation: "designation",
      job_title: "job_title",
      department: "department_id",
      location_code: "location_code",
      employment_type: "employment_type",
      work_mode: "work_mode",
      date_of_joining: "date_of_joining",
      reporting_manager: "reporting_manager_id",
      dotted_line_manager: "dotted_line_manager_id",
    });
  });
});

describe("matching a row to a stored employee", () => {
  const other = employee({ id: "emp-2", employee_no: "EMP-0002" });
  const index = indexExisting(spec(), [STORED, other]);

  it("finds the employee a Record ID names, whatever the number cell says", () => {
    expect(matchRow(index, "emp-2", "EMP-0001")).toMatchObject({ kind: "found", by: "id", item: other });
  });

  it("refuses a Record ID no employee has", () => {
    expect(matchRow(index, "emp-404", "EMP-0001")).toEqual({ kind: "unknown-id" });
  });

  it("finds the employee by number, ignoring case and spacing", () => {
    expect(matchRow(index, null, " emp-0002 ")).toMatchObject({ kind: "found", by: "key", item: other });
  });

  it("treats a number nobody has as a new employee", () => {
    expect(matchRow(index, null, "EMP-9999")).toEqual({ kind: "new" });
  });

  it("refuses a number two employees share instead of guessing", () => {
    const twins = indexExisting(spec(), [STORED, employee({ id: "emp-3", employee_no: "emp-0001" })]);
    expect(matchRow(twins, null, "EMP-0001")).toEqual({ kind: "ambiguous" });
  });
});

describe("loading the employees to compare with", () => {
  it("reads every page of the whole list, once each employee", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/employees": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, { items: [employee({ id: "e-1" }), employee({ id: "e-2" })], total: 3, page: 1, size: 200, pages: 2 })
          : json(200, { items: [employee({ id: "e-2" }), employee({ id: "e-3" })], total: 3, page: 2, size: 200, pages: 2 }),
    });
    const items = await spec().loadExisting(context);
    expect(items.map((e) => e.id)).toEqual(["e-1", "e-2", "e-3"]);
    const params = fetchMock.mock.calls.map(([url]) => new URL(url, "http://gateway.test").searchParams);
    expect(params.map((p) => `${p.get("page")}/${p.get("size")}`)).toEqual(["1/200", "2/200"]);
    expect(params.every((p) => !p.has("q") && !p.has("status") && !p.has("department_id"))).toBe(true);
  });

  it("refuses a list that stays short of the server's own total rather than diff against part of it", async () => {
    stubApi({
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 5, page: 1, size: 200, pages: 1 }),
    });
    await expect(spec().loadExisting(context)).rejects.toThrow(/changed while it was being read/);
  });
});

describe("what a row changes", () => {
  it("changes nothing, and makes no request, when every cell agrees with the stored employee", () => {
    const fetchMock = stubApi({});
    expect(plan(UNTOUCHED)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves everything alone when every cell is blank", () => {
    const blank: ParsedRow = Object.fromEntries(Object.keys(UNTOUCHED).map((key) => [key, null]));
    expect(plan(blank)).toBeNull();
  });

  it("ignores the columns the system owns, so an exported file can be edited and sent back", () => {
    const exported: ParsedRow = {
      ...UNTOUCHED,
      status: "exited",
      date_of_exit: "2026-02-01",
      created_at: "2020-01-01T00:00:00Z",
      id: "some-other-id",
    };
    expect(plan(exported)).toBeNull();
  });

  it.each([
    ["Full name", { full_name: "Asha R. Rao" }, { full_name: "Asha R. Rao" }],
    ["Designation", { designation: "Staff Engineer" }, { designation: "Staff Engineer" }],
    ["Job title", { job_title: "Platform" }, { job_title: "Platform" }],
    ["Department", { department: "Human Resources" }, { department_id: "d-hr" }],
    ["Department", { department: "engineering / platform" }, { department_id: "d-eng-plat" }],
    ["Department", { department: "D-OPS" }, { department_id: "d-ops" }],
    ["Location code", { location_code: "mumbai" }, { location_code: "MUM-01" }],
    ["Location code", { location_code: "mum-01" }, { location_code: "MUM-01" }],
    ["Employment type", { employment_type: "contract" }, { employment_type: "contract" }],
    ["Work mode", { work_mode: "remote" }, { work_mode: "remote" }],
    ["Date of joining", { date_of_joining: "2026-01-05" }, { date_of_joining: "2026-01-05" }],
    ["Manager employee number", { reporting_manager: "emp-0020" }, { reporting_manager_id: "mgr-20" }],
    [
      "Dotted-line manager employee number",
      { dotted_line_manager: "EMP-0007" },
      { dotted_line_manager_id: "mgr-7" },
    ],
  ] satisfies [string, ParsedRow, Record<string, string>][])(
    "sends only %s, under the endpoint's own key, when only that cell differs",
    async (header, cells, wire) => {
      const p = plan({ ...UNTOUCHED, ...cells });
      expect(p?.changed).toEqual([header]);
      const request = await sent({ ...UNTOUCHED, ...cells });
      expect(request).toEqual({ url: "/api/v1/employees/emp-1", method: "PATCH", body: wire });
    },
  );

  it("sends several changes in one request, and nothing that did not change", async () => {
    const row: ParsedRow = {
      ...UNTOUCHED,
      full_name: "Asha Iyer",
      work_mode: "office",
      department: "Human Resources",
      designation: "Engineer",
    };
    const p = plan(row);
    expect(p?.changed).toEqual(["Full name", "Department", "Work mode"]);
    const { body } = await sent(row);
    expect(body).toEqual({ full_name: "Asha Iyer", department_id: "d-hr", work_mode: "office" });
    expect(Object.values(body).every((value) => value !== null)).toBe(true);
  });

  it("sends a manager only when it changed: a same-value manager would still make the server walk the chain", async () => {
    const row: ParsedRow = {
      ...UNTOUCHED,
      full_name: "Asha Iyer",
      reporting_manager: "emp-0007",
      dotted_line_manager: "EMP-0012",
    };
    const { body } = await sent(row);
    expect(body).toEqual({ full_name: "Asha Iyer" });
    expect(body).not.toHaveProperty("reporting_manager_id");
    expect(body).not.toHaveProperty("dotted_line_manager_id");
  });

  it("sends a changed manager and leaves the unchanged one out", async () => {
    const { body } = await sent({ ...UNTOUCHED, reporting_manager: "EMP-0020" });
    expect(body).toEqual({ reporting_manager_id: "mgr-20" });
  });

  it("never sends the employee number, the status, the exit date or sealed details", async () => {
    const { body } = await sent({
      ...UNTOUCHED,
      employee_no: "emp-0001",
      full_name: "Asha Iyer",
      status: "exited",
      date_of_exit: "2026-02-01",
      pii: "x",
      pan: "ABCDE1234F",
    });
    expect(Object.keys(body)).toEqual(["full_name"]);
  });

  it("fixes a location code the record holds in a spelling the organisation does not", () => {
    const p = plan({ location_code: "BLR-HQ" }, [], employee({ location_code: "blr-hq" }));
    expect(p?.changed).toEqual(["Location code"]);
  });
});

describe("clearing with [clear]", () => {
  it.each([
    ["designation", "Designation", "designation"],
    ["job_title", "Job title", "job_title"],
    ["department", "Department", "department_id"],
    ["location_code", "Location code", "location_code"],
    ["date_of_joining", "Date of joining", "date_of_joining"],
    ["reporting_manager", "Manager employee number", "reporting_manager_id"],
    ["dotted_line_manager", "Dotted-line manager employee number", "dotted_line_manager_id"],
  ])("empties %s with an explicit null, the only value the endpoint accepts", async (column, header, wire) => {
    expect(plan({ [column]: null }, [column])?.changed).toEqual([header]);
    const { body } = await sent({ [column]: null }, [column]);
    expect(body).toEqual({ [wire]: null });
  });

  it("never clears a location code with an empty string, which would hide the employee from location-scoped roles", async () => {
    const { body } = await sent({ location_code: null }, ["location_code"]);
    expect(body).toStrictEqual({ location_code: null });
    expect(JSON.stringify(body)).not.toContain('""');
  });

  it.each([
    ["full_name", "Full name"],
    ["employment_type", "Employment type"],
    ["work_mode", "Work mode"],
  ])("refuses to clear %s, which the server would answer with a conflict", (column, header) => {
    expect(() => plan({ [column]: null }, [column])).toThrow(RowProblem);
    expect(() => plan({ [column]: null }, [column])).toThrow(`${header} cannot be cleared`);
  });

  it("does nothing to a field that is already empty", () => {
    const bare = employee({
      designation: null,
      job_title: "",
      department_id: null,
      location_code: null,
      date_of_joining: null,
      reporting_manager_id: null,
      dotted_line_manager_id: null,
    });
    const cleared = ["designation", "job_title", "department", "location_code", "date_of_joining", "reporting_manager", "dotted_line_manager"];
    expect(plan(Object.fromEntries(cleared.map((c) => [c, null])), cleared, bare)).toBeNull();
  });

  it("clears only what the row asks and leaves a blank cell's field alone", async () => {
    const { body } = await sent({ designation: null, job_title: null }, ["designation"]);
    expect(body).toEqual({ designation: null });
  });

  it("clears one manager without touching the other", async () => {
    const { body } = await sent({ dotted_line_manager: null }, ["dotted_line_manager"]);
    expect(body).toEqual({ dotted_line_manager_id: null });
  });
});

describe("the employee number is immutable", () => {
  it("accepts a cell that agrees with the stored number, in any case", () => {
    expect(plan({ employee_no: "emp-0001" })).toBeNull();
    expect(plan({ employee_no: "  EMP-0001 " })).toBeNull();
  });

  it("refuses a row that names an employee by Record ID and a different number, saying what is stored", () => {
    expect(() => plan({ employee_no: "EMP-0999" })).toThrow(RowProblem);
    expect(() => plan({ employee_no: "EMP-0999" })).toThrow(
      "Employee number cannot be changed once the record exists (it is 'EMP-0001')",
    );
  });

  it("refuses [clear] on it", () => {
    expect(() => plan({ employee_no: null }, ["employee_no"])).toThrow("Employee number cannot be cleared");
  });
});

describe("references that do not resolve", () => {
  const column = (key: string) => {
    const found = employeesEntity.columns.find((c) => c.key === key);
    if (found === undefined) throw new Error(`No column ${key}`);
    return found;
  };

  it.each([
    ["department", "Skunkworks", "is not one of the accepted values for Department (see the Lists sheet)."],
    ["department", "Platform", "is not one of the accepted values for Department (see the Lists sheet)."],
    ["location_code", "Bangalore", "is not one of the accepted values for Location code (see the Lists sheet)."],
    ["reporting_manager", "EMP-9999", "is not one of the accepted values for Manager employee number (see the Lists sheet)."],
    [
      "dotted_line_manager",
      "EMP-9999",
      "is not one of the accepted values for Dotted-line manager employee number (see the Lists sheet).",
    ],
  ])(
    "refuses the row for a %s (%s) the organisation's lists do not hold, in the engine's words and with the update hint",
    (key, text, reason) => {
      const hint = column(key).lookupHintUpdate;
      expect(hint, `${key} has an update hint`).toBeTruthy();
      expect(() => plan({ [key]: text })).toThrow(RowProblem);
      expect(() => plan({ [key]: text })).toThrow(`'${text}' ${reason} ${hint ?? ""}`);
    },
  );

  it("says a name two departments share cannot be chosen, rather than picking one", () => {
    const twins: ImportContext = {
      lookups: { ...LOOKUPS, departments: makeLookup(departmentEntries([unit("t-1", "Platform"), unit("t-2", "platform")])) },
      signal,
    };
    const attempt = () => planUpdate(employeesEntity.columns, spec(), { department: "Platform" }, new Set(), STORED, twins);
    expect(attempt).toThrow("'Platform' matches more than one of the accepted values for Department, so it cannot be chosen.");
    expect(attempt).toThrow(column("department").lookupHintUpdate ?? "");
  });

  it("gives every lookup column its own hint for updating, apart from the one for adding", () => {
    for (const lookupColumn of employeesEntity.columns.filter((c) => c.lookup !== undefined)) {
      expect(lookupColumn.lookupHintUpdate, lookupColumn.header).toBeTruthy();
      expect(lookupColumn.lookupHintUpdate, lookupColumn.header).not.toBe(lookupColumn.lookupHint);
    }
  });

  it("says a lookup that failed to load could not be checked, instead of calling every name unknown", () => {
    const none: ImportContext = { lookups: {}, signal };
    expect(() => planUpdate(employeesEntity.columns, spec(), { department: "Human Resources" }, new Set(), STORED, none)).toThrow(
      "The accepted values for Department could not be loaded, so it cannot be checked",
    );
  });

  it("refuses a location when the caller cannot read the organisation's locations", () => {
    const none: ImportContext = { lookups: { ...LOOKUPS, locations: makeLookup([]) }, signal };
    expect(() => planUpdate(employeesEntity.columns, spec(), { location_code: "BLR-HQ" }, new Set(), STORED, none)).toThrow(
      RowProblem,
    );
  });

  it("does not refuse a manager the sheet does not name, whatever the lists hold", () => {
    const none: ImportContext = { lookups: { ...LOOKUPS, managers: makeLookup([]) }, signal };
    expect(planUpdate(employeesEntity.columns, spec(), { full_name: "Asha Iyer" }, new Set(), STORED, none)?.changed).toEqual([
      "Full name",
    ]);
  });
});

describe("reporting lines the server would refuse", () => {
  it("refuses an employee who is their own manager, before anything is sent", () => {
    const fetchMock = stubApi({});
    expect(() => plan({ reporting_manager: "EMP-0001" })).toThrow("An employee cannot report to themselves.");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an employee who is their own dotted-line manager", () => {
    expect(() => plan({ dotted_line_manager: "emp-0001" })).toThrow(
      "An employee cannot be their own dotted-line manager.",
    );
  });

  it("lets the server judge a longer cycle: it answers with its own sentence, which reaches the row", async () => {
    stubApi({
      "PATCH /api/v1/employees/emp-1": () =>
        json(422, { title: "Unprocessable", status: 422, detail: "That would make the reporting line circular" }),
    });
    await expect(plan({ reporting_manager: "EMP-0020" })?.run()).rejects.toMatchObject({
      status: 422,
      message: expect.stringContaining("circular") as unknown,
    });
  });
});

describe("what the server can answer", () => {
  it.each([
    [404, "That employee is not part of this organization"],
    [403, "This grant is OWN; requires ORG scope"],
    [422, "An employee cannot report to themselves"],
  ])("lets a %s through with the server's own sentence", async (status, detail) => {
    stubApi({ "PATCH /api/v1/employees/emp-1": () => json(status, { title: "Refused", status, detail }) });
    const attempt = plan({ full_name: "Asha Iyer" })?.run();
    await expect(attempt).rejects.toMatchObject({ status });
    await expect(attempt).rejects.toThrow(detail);
  });
});
