/**
 * The Employees import/export entity, tested directly: what its columns promise, the request body
 * each sheet row turns into, what the lookups are built from, and what an export asks for and
 * writes. The engine and the dialog are not involved; requests go to a stubbed `fetch`.
 */
import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";
import type { EmployeeOut } from "@/api/types";

import { departmentEntries } from "./departments";
import { employeesEntity } from "./employees";

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

const LOOKUPS: LookupSet = {
  departments: makeLookup(departmentEntries(TREE)),
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
      "GET /api/v1/employees": () => json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    const named = employeesEntity.columns.flatMap((c) => (c.lookup === undefined ? [] : [c.lookup]));
    expect(named.length).toBeGreaterThan(0);
    for (const name of named) expect(Object.keys(lookups ?? {})).toContain(name);
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
  it("writes a repeated name as Parent / Child and leaves a unique name alone", () => {
    expect(departmentEntries(TREE)).toEqual([
      { value: "d-eng", label: "Engineering" },
      { value: "d-eng-plat", label: "Engineering / Platform" },
      { value: "d-eng-web", label: "Web" },
      { value: "d-ops", label: "Operations" },
      { value: "d-ops-plat", label: "Operations / Platform" },
      { value: "d-hr", label: "Human Resources" },
    ]);
  });

  it("treats names as the same regardless of case and spacing", () => {
    const labels = departmentEntries([
      unit("a", "Sales", [unit("a1", "Field  Ops")]),
      unit("b", "Support", [unit("b1", "field ops")]),
    ]).map((e) => e.label);
    expect(labels).toEqual(["Sales", "Sales / Field  Ops", "Support", "Support / field ops"]);
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
        location_code: "BLR-HQ",
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

  it("refuses a department that does not exist, and says how to write a repeated one", () => {
    expect(() => employeesEntity.toPayload(parsed({ department: "Platform" }), context())).toThrow(
      new RowProblem(
        'There is no department called "Platform". Where two departments share a name, write it as Parent / Child.',
      ),
    );
  });

  it("refuses a department name that more than one department carries", () => {
    const repeated: LookupSet = {
      ...LOOKUPS,
      departments: makeLookup([
        { label: "Support", value: "s-1" },
        { label: "Support", value: "s-2" },
      ]),
    };
    expect(() => employeesEntity.toPayload(parsed({ department: "support" }), context(repeated))).toThrow(
      'More than one department is called "support".',
    );
  });

  it("explains a department it cannot match when the caller cannot read departments", () => {
    const noDepartments: LookupSet = { ...LOOKUPS, departments: makeLookup([]) };
    expect(() =>
      employeesEntity.toPayload(parsed({ department: "Engineering" }), context(noDepartments)),
    ).toThrow("Your account cannot read the department list");
  });

  it.each(["reporting_manager", "dotted_line_manager"])(
    "refuses a %s who is not already an employee, and says a same-file manager is not found",
    (key) => {
      const attempt = () => employeesEntity.toPayload(parsed({ [key]: "EMP-9999" }), context());
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow(
        'No employee has the number "EMP-9999". A manager must already be an employee: one created in this same file is not found',
      );
    },
  );

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
  it("reads every page of employees and the whole department tree", async () => {
    const fetchMock = stubApi({
      "GET /api/v1/org-units": () => json(200, TREE),
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
    expect(lookups?.departments?.resolve("Human Resources")).toBe("d-hr");
  });

  it("carries on with no departments when the caller may not read them", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(403, { title: "Forbidden", status: 403 }),
      "GET /api/v1/employees": () =>
        json(200, { items: [employee()], total: 1, page: 1, size: 200, pages: 1 }),
    });
    const lookups = await employeesEntity.loadLookups?.(signal);
    expect(lookups?.departments?.labels).toEqual([]);
    expect(lookups?.managers?.labels).toEqual(["EMP-0001"]);
  });

  it("fails when the departments cannot be read for any other reason, instead of calling every name unknown", async () => {
    stubApi({
      "GET /api/v1/org-units": () => json(500, { title: "Server error", status: 500 }),
      "GET /api/v1/employees": () =>
        json(200, { items: [], total: 0, page: 1, size: 200, pages: 1 }),
    });
    await expect(employeesEntity.loadLookups?.(signal)).rejects.toMatchObject({ status: 500 });
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
