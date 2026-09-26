/**
 * The Employees import/export entity, tested directly: what its columns promise, the request body
 * each sheet row turns into, what the lookups are built from, and what an export asks for and
 * writes. The engine and the dialog are not involved; requests go to a stubbed `fetch`.
 */
import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";
import type { EmployeeOut, LocationOut } from "@/api/types";

import { departmentEntries, loadOrgUnitTree } from "./departments";
import { employeesEntity } from "./employees";
import { loadLocations, locationEntries } from "./locations";

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
    id: "emp-uuid-1",
    membership_id: null,
    employee_no: "EMP-0001",
    full_name: "Asha Rao",
    designation: null,
    job_title: null,
    department_id: null,
    location_code: null,
    employment_type: "full_time",
    work_mode: "office",
    date_of_joining: null,
    reporting_manager_id: null,
    dotted_line_manager_id: null,
    status: "active",
    date_of_exit: null,
    created_at: "2026-01-05T09:00:00Z",
    hierarchy_version: 1,
    pii: null,
    ...over,
  };
}

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

const TREE: OrgUnitNode[] = [
  unit("d-eng", "Engineering", [unit("d-eng-plat", "Platform"), unit("d-eng-web", "Web")]),
  unit("d-ops", "Operations", [unit("d-ops-plat", "Platform")]),
  unit("d-hr", "Human Resources"),
];

function site(code: string, name: string): LocationOut {
  return { id: `loc-${code}`, name, code, timezone: "Asia/Kolkata", country: "IN", is_active: true };
}

const SITES: LocationOut[] = [site("BLR-HQ", "Bengaluru HQ"), site("MUM-01", "Mumbai")];

const LOOKUPS: LookupSet = {
  departments: makeLookup(departmentEntries(TREE)),
  locations: makeLookup(locationEntries(SITES)),
  managers: makeLookup([
    { label: "EMP-0007", value: "mgr-7" },
    { label: "EMP-0012", value: "mgr-12" },
  ]),
};

function context(lookups: LookupSet = LOOKUPS): ImportContext {
  return { lookups, signal };
}

function parsed(over: ParsedRow): ParsedRow {
  return { employee_no: "EMP-2001", full_name: "Ravi Iyer", ...over };
}

describe("columns", () => {
  it("asks only for the employee number and the full name", () => {
    const required = employeesEntity.columns.filter((c) => c.required === true);
    expect(required.map((c) => c.header)).toEqual(["Employee number", "Full name"]);
  });

  it("has no column for anything the API seals", () => {
    const sealed = new Set(["pii", "pan", "aadhaar", "uan", "esi", "bank", "personal", "birth", "ifsc"]);
    for (const column of employeesEntity.columns) {
      const words = `${column.key} ${column.header}`.toLowerCase().split(/[^a-z]+/);
      expect(words.filter((word) => sealed.has(word)), column.header).toEqual([]);
    }
    expect(employeesEntity.notes?.join(" ")).toMatch(/PAN, Aadhaar/);
  });

  it("marks what the system sets as read-only, and never accepts a status", () => {
    const readOnly = employeesEntity.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(readOnly).toEqual(["status", "date_of_exit", "created_at", "id"]);
  });

  it("keeps employee numbers and manager numbers as text", () => {
    const kinds = Object.fromEntries(employeesEntity.columns.map((c) => [c.key, c.kind]));
    expect(kinds).toMatchObject({
      employee_no: "text",
      reporting_manager: "text",
      dotted_line_manager: "text",
      department: "text",
    });
  });

  it("points every lookup column at a set that loadLookups returns", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
      "GET /api/v1/locations": () => json(200, SITES),
      "GET /api/v1/employees": () => json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    const named = employeesEntity.columns.flatMap((c) => (c.lookup === undefined ? [] : [c.lookup]));
    expect(named.length).toBeGreaterThan(0);
    for (const name of named) expect(Object.keys(lookups ?? {})).toContain(name);
  });

  it("resolves the department, location and manager columns through lookups, not free text", () => {
    const lookupOf = (key: string) => employeesEntity.columns.find((c) => c.key === key)?.lookup;
    expect(lookupOf("department")).toBe("departments");
    expect(lookupOf("location_code")).toBe("locations");
    expect(lookupOf("reporting_manager")).toBe("managers");
    expect(lookupOf("dotted_line_manager")).toBe("managers");
  });

  it("tells the user how to fix a reference the lists do not hold", () => {
    const hintOf = (key: string) => employeesEntity.columns.find((c) => c.key === key)?.lookupHint;
    expect(hintOf("department")).toBe("Use a department name or code from the Lists sheet.");
    expect(hintOf("location_code")).toBe(
      "Use a location code or name from the Lists sheet. If that sheet lists no locations, your account cannot read them: leave this cell blank.",
    );
    const managers =
      "The manager must already be an employee. Import managers first, then run this file again: rows already imported are skipped.";
    expect(hintOf("reporting_manager")).toBe(managers);
    expect(hintOf("dotted_line_manager")).toBe(managers);
    for (const column of employeesEntity.columns.filter((c) => c.lookup !== undefined)) {
      expect(column.lookupHint, column.header).toBeTruthy();
    }
  });

  it("names the Record ID column as the mark of a row that is already in the system", () => {
    expect(employeesEntity.recordIdKey).toBe("id");
    const idColumn = employeesEntity.columns.find((c) => c.key === employeesEntity.recordIdKey);
    expect(idColumn).toMatchObject({ header: "Record ID", readOnly: true });
  });

  it("says in the help and the notes that a department or location may be given by its code", () => {
    const helpOf = (key: string) => employeesEntity.columns.find((c) => c.key === key)?.help;
    expect(helpOf("department")).toMatch(/or its code/);
    expect(helpOf("location_code")).toMatch(/its code/);
    const notes = (employeesEntity.notes ?? []).join("\n");
    expect(notes).toMatch(/department is found by its name or by its code/);
    expect(notes).toMatch(/location is found by its code or its name, and is saved as its code/);
  });
});

describe("sample rows", () => {
  it("fill the required columns and stay inside every column's own rules", () => {
    const importable = employeesEntity.columns.filter((c) => c.readOnly !== true);
    expect(employeesEntity.sampleRows.length).toBeGreaterThanOrEqual(2);
    for (const sample of employeesEntity.sampleRows) {
      for (const column of importable) {
        const cell = sample[column.key];
        if (column.required === true) expect(cell, column.header).toBeTruthy();
        if (typeof cell !== "string") continue;
        if (column.maxLength !== undefined) expect(cell.length).toBeLessThanOrEqual(column.maxLength);
        if (column.pattern !== undefined) expect(cell).toMatch(column.pattern);
        if (column.kind === "enum") {
          expect(column.options?.some((o) => o.label === cell || o.value === cell)).toBe(true);
        }
      }
    }
  });

  it("use only known importable columns", () => {
    const importable = new Set(
      employeesEntity.columns.filter((c) => c.readOnly !== true).map((c) => c.key),
    );
    for (const sample of employeesEntity.sampleRows) {
      for (const key of Object.keys(sample)) expect(importable.has(key), key).toBe(true);
    }
  });
});

describe("departmentEntries", () => {
  it("writes a repeated name as Parent / Child, leaves a unique name alone, and adds every code", () => {
    expect(departmentEntries(TREE)).toEqual([
      { value: "d-eng", label: "Engineering" },
      { value: "d-eng-plat", label: "Engineering / Platform" },
      { value: "d-eng-web", label: "Web" },
      { value: "d-ops", label: "Operations" },
      { value: "d-ops-plat", label: "Operations / Platform" },
      { value: "d-hr", label: "Human Resources" },
      { value: "d-eng", label: "D-ENG" },
      { value: "d-eng-plat", label: "D-ENG-PLAT" },
      { value: "d-eng-web", label: "D-ENG-WEB" },
      { value: "d-ops", label: "D-OPS" },
      { value: "d-ops-plat", label: "D-OPS-PLAT" },
      { value: "d-hr", label: "D-HR" },
    ]);
  });

  it("treats names as the same regardless of case and spacing", () => {
    const labels = departmentEntries([
      unit("a", "Sales", [unit("a1", "Field  Ops")]),
      unit("b", "Support", [unit("b1", "field ops")]),
    ]).map((e) => e.label);
    expect(labels.slice(0, 4)).toEqual(["Sales", "Sales / Field  Ops", "Support", "Support / field ops"]);
  });

  it("writes two root departments with the same name by code, so an export names each one exactly", () => {
    const twins = [unit("t-1", "Platform"), unit("t-2", "platform")];
    const lookup = makeLookup(departmentEntries(twins));
    expect(lookup.labels).toEqual(["T-1", "T-2"]);
    expect(lookup.isAmbiguous("Platform")).toBe(true);
    expect(lookup.resolve("Platform")).toBeUndefined();
    expect(lookup.resolve("t-2")).toBe("t-2");
    expect(lookup.labelOf("t-1")).toBe("T-1");
    expect(lookup.labelOf("t-2")).toBe("T-2");
  });

  it("finds a department by its code as well as its name, in any case", () => {
    const lookup = makeLookup(departmentEntries(TREE));
    expect(lookup.resolve("d-eng-plat")).toBe("d-eng-plat");
    expect(lookup.resolve("Engineering / Platform")).toBe("d-eng-plat");
    // The bare name is one both departments share, so neither answers to it.
    expect(lookup.resolve("Platform")).toBeUndefined();
    expect(lookup.labelOf("d-eng-plat")).toBe("Engineering / Platform");
  });
});

describe("locationEntries", () => {
  it("offers each location by its code and by its name, both as the code, code first", () => {
    expect(locationEntries(SITES)).toEqual([
      { label: "BLR-HQ", value: "BLR-HQ" },
      { label: "Bengaluru HQ", value: "BLR-HQ" },
      { label: "MUM-01", value: "MUM-01" },
      { label: "Mumbai", value: "MUM-01" },
    ]);
  });

  it("resolves a name or a code in any case to the stored code, and writes the code back", () => {
    const lookup = makeLookup(locationEntries(SITES));
    expect(lookup.resolve("blr-hq")).toBe("BLR-HQ");
    expect(lookup.resolve(" bengaluru   hq ")).toBe("BLR-HQ");
    expect(lookup.resolve("Bangalore")).toBeUndefined();
    expect(lookup.labelOf("BLR-HQ")).toBe("BLR-HQ");
  });

  it("does not call a location ambiguous whose name is its code", () => {
    const lookup = makeLookup(locationEntries([site("HQ", "HQ")]));
    expect(lookup.isAmbiguous("HQ")).toBe(false);
    expect(lookup.labels).toEqual(["HQ"]);
  });

  it("refuses a name that two locations share, and lists it nowhere", () => {
    const lookup = makeLookup(locationEntries([site("N-1", "North"), site("N-2", "North")]));
    expect(lookup.isAmbiguous("north")).toBe(true);
    expect(lookup.resolve("north")).toBeUndefined();
    expect(lookup.labels).toEqual(["N-1", "N-2"]);
  });
});

describe("toPayload", () => {
  it("sends only the two required fields when nothing else is filled", () => {
    expect(employeesEntity.toPayload(parsed({}), context())).toEqual({
      employee_no: "EMP-2001",
      full_name: "Ravi Iyer",
    });
  });

  it("carries every filled column and resolves names to ids", () => {
    const body = employeesEntity.toPayload(
      parsed({
        designation: "Senior Engineer",
        job_title: "Engineering",
        department: "engineering / platform",
        location_code: "bengaluru hq",
        employment_type: "contract",
        work_mode: "hybrid",
        date_of_joining: "2026-04-01",
        reporting_manager: "emp-0007",
        dotted_line_manager: "EMP-0012",
      }),
      context(),
    );
    expect(body).toEqual({
      employee_no: "EMP-2001",
      full_name: "Ravi Iyer",
      designation: "Senior Engineer",
      job_title: "Engineering",
      department_id: "d-eng-plat",
      location_code: "BLR-HQ",
      employment_type: "contract",
      work_mode: "hybrid",
      date_of_joining: "2026-04-01",
      reporting_manager_id: "mgr-7",
      dotted_line_manager_id: "mgr-12",
    });
  });

  it("never sends a status, a membership or sealed details, whatever the row holds", () => {
    const body = employeesEntity.toPayload(
      parsed({ status: "active", membership_id: "m-1", pan: "ABCDE1234F", pii: "x" }),
      context(),
    );
    expect(Object.keys(body).sort()).toEqual(["employee_no", "full_name"]);
  });

  it("sends a department found by its code, and a location by its code however it was written", () => {
    expect(
      employeesEntity.toPayload(parsed({ department: "D-OPS-PLAT", location_code: "mum-01" }), context()),
    ).toMatchObject({ department_id: "d-ops-plat", location_code: "MUM-01" });
  });

  it.each([
    ["department", "Platform"],
    ["department", "Skunkworks"],
    ["location_code", "Bangalore"],
    ["reporting_manager", "EMP-9999"],
    ["dotted_line_manager", "EMP-9999"],
  ])(
    "fails the row, not the run, for a %s (%s) the lists do not resolve: the engine's own message and hint reach the user first",
    (key, text) => {
      const attempt = () => employeesEntity.toPayload(parsed({ [key]: text }), context());
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow(`"${text}" is not in the list.`);
    },
  );

  it("fails the row for a location when the caller may not read locations", () => {
    const none: LookupSet = { ...LOOKUPS, locations: makeLookup([]) };
    expect(() => employeesEntity.toPayload(parsed({ location_code: "BLR-HQ" }), context(none))).toThrow(
      RowProblem,
    );
  });

  it("fails the row rather than crash when a required cell is blank", () => {
    expect(() => employeesEntity.toPayload({ employee_no: null, full_name: "X" }, context())).toThrow(
      new RowProblem("Employee number is required."),
    );
  });
});

describe("create", () => {
  it("posts the payload to /employees and returns the new id", async () => {
    const fetchMock = stubApi({
      "POST /api/v1/employees": () => json(201, employee({ id: "new-id", employee_no: "EMP-2001" })),
    });
    const created = await employeesEntity.create(
      { employee_no: "EMP-2001", full_name: "Ravi Iyer", department_id: "d-hr" },
      context(),
    );
    expect(created).toEqual({ id: "new-id" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      employee_no: "EMP-2001",
      full_name: "Ravi Iyer",
      department_id: "d-hr",
    });
  });

  it("lets the server's refusal through, so the engine can report it against the row", async () => {
    stubApi({
      "POST /api/v1/employees": () => json(409, { title: "Conflict", status: 409, detail: "An employee numbered 'EMP-2001' already exists" }),
    });
    await expect(
      employeesEntity.create({ employee_no: "EMP-2001", full_name: "Ravi Iyer" }, context()),
    ).rejects.toMatchObject({ status: 409 });
  });
});

describe("loadLookups", () => {
  it("reads every page of employees, the whole department tree and every location", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
      "GET /api/v1/locations": () => json(200, SITES),
      "GET /api/v1/employees": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, {
              items: [employee({ id: "e-1", employee_no: "EMP-0001" })],
              total: 2,
              page: 1,
              size: 200,
              pages: 2,
            })
          : json(200, {
              items: [employee({ id: "e-2", employee_no: "EMP-0002" })],
              total: 2,
              page: 2,
              size: 200,
              pages: 2,
            }),
    });

    const lookups = await employeesEntity.loadLookups?.(signal);

    const employeeCalls = fetchMock.mock.calls
      .map(([url]) => new URL(url, "http://gateway.test"))
      .filter((url) => url.pathname === "/api/v1/employees");
    expect(employeeCalls.map((url) => `${url.searchParams.get("page")}/${url.searchParams.get("size")}`)).toEqual([
      "1/200",
      "2/200",
    ]);
    expect(lookups?.managers?.labels).toEqual(["EMP-0001", "EMP-0002"]);
    expect(lookups?.managers?.resolve("emp-0002")).toBe("e-2");
    expect(lookups?.departments?.labels).toContain("Operations / Platform");
    expect(lookups?.departments?.labels).toContain("D-OPS-PLAT");
    expect(lookups?.departments?.resolve("Human Resources")).toBe("d-hr");
    expect(lookups?.departments?.resolve("d-hr")).toBe("d-hr");
    expect(lookups?.locations?.labels).toEqual(["BLR-HQ", "Bengaluru HQ", "MUM-01", "Mumbai"]);
    expect(lookups?.locations?.resolve("mumbai")).toBe("MUM-01");
  });

  it("carries on with no departments when the caller may not read them", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(403, { title: "Forbidden", status: 403 }),
      "GET /api/v1/locations": () => json(200, SITES),
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 1, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    expect(lookups?.departments?.labels).toEqual([]);
    expect(lookups?.locations?.labels).toContain("BLR-HQ");
    expect(lookups?.managers?.labels).toEqual(["EMP-0001"]);
  });

  it("carries on with no locations when the caller may not read them", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
      "GET /api/v1/locations": () => json(403, { title: "Forbidden", status: 403 }),
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 1, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    expect(lookups?.locations?.labels).toEqual([]);
    expect(lookups?.departments?.labels).toContain("Human Resources");
    expect(lookups?.managers?.labels).toEqual(["EMP-0001"]);
  });

  it.each([
    ["departments", "GET /api/v1/org-units"],
    ["locations", "GET /api/v1/locations"],
  ])(
    "fails when the %s cannot be read for any other reason, instead of calling every name unknown",
    async (_name, failing) => {
      stubApi({
        "GET /api/v1/org-units": () => json(200, TREE),
        "GET /api/v1/locations": () => json(200, SITES),
        [failing]: () => json(500, { title: "Server error", status: 500 }),
        "GET /api/v1/employees": () =>
          json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
      });
      await expect(employeesEntity.loadLookups?.(signal)).rejects.toMatchObject({ status: 500 });
    },
  );

  it("fails rather than hand over a list of employees that stays short of the server's total", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
      "GET /api/v1/locations": () => json(200, SITES),
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 5, page: 1, size: 200, pages: 1 }),
    });
    await expect(employeesEntity.loadLookups?.(signal)).rejects.toThrow(/changed while it was being read/);
  });

  it("keeps an employee the list serves twice once, so a real number is never called ambiguous", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
      "GET /api/v1/locations": () => json(200, SITES),
      "GET /api/v1/employees": (url) =>
        url.searchParams.get("page") === "1"
          ? json(200, {
              items: [employee({ id: "e-1", employee_no: "EMP-0001" }), employee({ id: "e-2", employee_no: "EMP-0002" })],
              total: 3,
              page: 1,
              size: 200,
              pages: 2,
            })
          : json(200, {
              items: [employee({ id: "e-2", employee_no: "EMP-0002" }), employee({ id: "e-3", employee_no: "EMP-0003" })],
              total: 3,
              page: 2,
              size: 200,
              pages: 2,
            }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    expect(lookups?.managers?.labels).toEqual(["EMP-0001", "EMP-0002", "EMP-0003"]);
    expect(lookups?.managers?.resolve("EMP-0002")).toBe("e-2");
  });
});

describe("an expired session", () => {
  /** Answers 401 once; a refresh then hands out a new token, and the same request succeeds. */
  function expiresOnce(path: string, body: unknown) {
    let refreshed = false;
    const seen: (string | undefined)[] = [];
    stubApi({
      [`GET ${path}`]: (_url, init) => {
        const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
        seen.push(auth);
        return refreshed ? json(200, body) : json(401, { title: "Unauthorized", status: 401 });
      },
      "POST /api/v1/auth/refresh": () => {
        refreshed = true;
        return json(200, { access_token: "fresh-token", expires_in: 900 });
      },
    });
    return seen;
  }

  it("refreshes the session and reads the departments, instead of reading none", async () => {
    const seen = expiresOnce("/api/v1/org-units", TREE);
    await expect(loadOrgUnitTree()).resolves.toEqual(TREE);
    expect(seen).toHaveLength(2);
    expect(seen[1]).toBe("Bearer fresh-token");
  });

  it("refreshes the session and reads the locations, instead of reading none", async () => {
    const seen = expiresOnce("/api/v1/locations", SITES);
    await expect(loadLocations()).resolves.toEqual(SITES);
    expect(seen).toHaveLength(2);
    expect(seen[1]).toBe("Bearer fresh-token");
  });
});

describe("export", () => {
  const exportSource = employeesEntity.export;

  it("asks for the screen's current filters, one page at a time", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 1, page: 3, size: 200, pages: 3 }),
    });
    const page = await exportSource?.fetchPage({
      page: 3,
      size: 200,
      filters: { q: "asha", status: "on_notice", departmentId: "d-hr" },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    const params = new URL(url, "http://gateway.test").searchParams;
    expect(Object.fromEntries(params)).toEqual({
      q: "asha",
      status: "on_notice",
      department_id: "d-hr",
      page: "3",
      size: "200",
    });
    expect(page).toMatchObject({ total: 1, pages: 3 });
  });

  it("sends no filter for a screen that has none", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/employees": () =>
        json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    await exportSource?.fetchPage({
      page: 1,
      size: 200,
      filters: { q: "", status: "all", departmentId: undefined },
      signal,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(Object.fromEntries(new URL(url, "http://gateway.test").searchParams)).toEqual({
      page: "1",
      size: "200",
    });
  });

  it("writes an employee as the sheet's own words: labels, employee numbers, dates", () => {
    const row = exportSource?.toRow(
      employee({
        id: "emp-77",
        employee_no: "EMP-0077",
        full_name: "Meera Shah",
        designation: "Analyst",
        job_title: "Finance",
        department_id: "d-eng-plat",
        location_code: "BLR-HQ",
        employment_type: "part_time",
        work_mode: "remote",
        date_of_joining: "2025-06-02",
        reporting_manager_id: "mgr-7",
        dotted_line_manager_id: "mgr-12",
        status: "on_notice",
        created_at: "2025-06-01T08:30:00Z",
      }),
      LOOKUPS,
    );
    expect(row).toEqual({
      employee_no: "EMP-0077",
      full_name: "Meera Shah",
      designation: "Analyst",
      job_title: "Finance",
      department: "Engineering / Platform",
      location_code: "BLR-HQ",
      employment_type: "Part-time",
      work_mode: "Remote",
      date_of_joining: "2025-06-02",
      reporting_manager: "EMP-0007",
      dotted_line_manager: "EMP-0012",
      status: "On Notice",
      date_of_exit: null,
      created_at: "2025-06-01T08:30:00Z",
      id: "emp-77",
    });
  });

  it("leaves a reference blank when there is none or it cannot be named", () => {
    const row = exportSource?.toRow(
      employee({ department_id: "d-unknown", reporting_manager_id: null }),
      {},
    );
    expect(row).toMatchObject({ department: "", reporting_manager: "", dotted_line_manager: "" });
  });

  it("writes the location's code as stored, even when the caller cannot read the list of locations", () => {
    expect(exportSource?.toRow(employee({ location_code: "BLR-HQ" }), LOOKUPS)).toMatchObject({
      location_code: "BLR-HQ",
    });
    expect(exportSource?.toRow(employee({ location_code: "BLR-HQ" }), {})).toMatchObject({
      location_code: "BLR-HQ",
    });
  });

  it("writes a root department shared by name as its code, which an import accepts", () => {
    const twins = [unit("t-1", "Platform"), unit("t-2", "Platform")];
    const lookups: LookupSet = { departments: makeLookup(departmentEntries(twins)) };
    const row = exportSource?.toRow(employee({ department_id: "t-2" }), lookups);
    expect(row).toMatchObject({ department: "T-2" });
    expect(lookups.departments?.resolve("T-2")).toBe("t-2");
  });

  it("writes only columns the entity declares, and nothing from the sealed details", () => {
    const row = exportSource?.toRow(
      employee({
        pii: {
          date_of_birth: "••••",
          personal_email: "a•••@x.com",
          personal_phone: "•••• 3210",
          pan: "•••••1234F",
          aadhaar: "•••• 9012",
          uan: null,
          esi: null,
          bank_account: "•••• 6789",
          bank_ifsc: "HDFC0001234",
        },
      }),
      LOOKUPS,
    );
    const declared = new Set(employeesEntity.columns.map((c) => c.key));
    expect(Object.keys(row ?? {}).every((key) => declared.has(key))).toBe(true);
    expect(JSON.stringify(row)).not.toMatch(/••|HDFC/);
  });
});
