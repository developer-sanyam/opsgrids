/**
 * Updating employees from Excel end to end, the way a person meets it: the real `useImportExport`
 * hook from `@opsgrids/client` behind the real `ImportExportButton` dialog from `@opsgrids/ui`, with
 * only the gateway stubbed. A workbook is built in memory, "Add new and update existing" is chosen,
 * the file is previewed row by row, imported, and the requests the gateway saw are read back.
 *
 * What matters here is what is NOT sent: an employee whose row changes nothing gets no request, a
 * manager is sent only when it changed, an empty field is a null, and the employee number never
 * travels (the endpoint would drop it and answer 200).
 *
 * The runner spaces requests by 350 ms, so an import of four rows takes about a second here.
 */
import { useImportExport } from "@opsgrids/client";
import type { ImportSummary } from "@opsgrids/client";
import { ImportExportButton } from "@opsgrids/ui";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExcelJS from "exceljs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";
import type { EmployeeOut, LocationOut } from "@/api/types";

import { employeesEntity } from "./employees";
import type { EmployeeFilters } from "./employees";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const TEST_TIMEOUT = 15_000;

// ------------------------------------------------------------------------------------ jsdom gaps

interface Download {
  name: string;
  blob: Blob | undefined;
}

let downloads: Download[];

/** jsdom's `Blob` has no `arrayBuffer()`; the engine and this file both read files through it. */
function polyfillBlobArrayBuffer(): void {
  if (typeof Blob.prototype.arrayBuffer === "function") return;
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (reader.result instanceof ArrayBuffer) resolve(reader.result);
        else reject(new Error("The blob did not read as bytes"));
      };
      reader.onerror = () => reject(reader.error ?? new Error("The blob could not be read"));
      reader.readAsArrayBuffer(this);
    });
  };
}

beforeEach(() => {
  polyfillBlobArrayBuffer();
  downloads = [];
  const blobs = new Map<string, Blob>();
  URL.createObjectURL = vi.fn((blob: Blob) => {
    const url = `blob:test/${blobs.size + 1}`;
    blobs.set(url, blob);
    return url;
  });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
    downloads.push({ name: this.download, blob: blobs.get(this.href) });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------------ the gateway

interface Recorded {
  method: string;
  path: string;
  params: URLSearchParams;
  body: unknown;
}

interface Refusal {
  status: number;
  detail: string;
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

const SITES: LocationOut[] = [site("BLR-HQ", "Bengaluru HQ"), site("PUN-03", "Pune")];

function employee(over: Partial<EmployeeOut>): EmployeeOut {
  return {
    id: "e-0",
    membership_id: null,
    employee_no: "EMP-0000",
    full_name: "Someone",
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

/** The organisation's employees, served two to a page whatever `size` the caller asks for. */
const EMPLOYEES: EmployeeOut[] = [
  employee({
    id: "e-7",
    employee_no: "EMP-0007",
    full_name: "Rhea Kapoor",
    designation: "=1+1",
    department_id: "d-hr",
    date_of_joining: "2024-02-01",
  }),
  employee({
    id: "e-12",
    employee_no: "EMP-0012",
    full_name: "Sameer Joshi",
    department_id: "d-eng-plat",
    location_code: "BLR-HQ",
    employment_type: "part_time",
    work_mode: "remote",
    reporting_manager_id: "e-7",
    status: "on_notice",
  }),
  employee({ id: "e-31", employee_no: "EMP-0031", full_name: "Tara Menon", dotted_line_manager_id: "e-12" }),
  employee({
    id: "e-41",
    employee_no: "EMP-0041",
    full_name: "Vikram Rao",
    designation: "Analyst",
    status: "exited",
    date_of_exit: "2026-01-31",
  }),
];
const PER_PAGE = 2;

function problem(status: number, detail: string): Response {
  return new Response(JSON.stringify({ status, title: "Refused", detail }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });
}

function ok(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** What differs from the usual organisation: its employees, and whether the caller may read the locations. */
interface World {
  employees?: readonly EmployeeOut[];
  locationsForbidden?: boolean;
}

/**
 * A gateway with the department tree, the locations, the employee list, `PATCH /employees/{id}` and
 * `POST /employees`. An id in `refuse` answers with that refusal instead of saving.
 */
function stubGateway(refuse: Readonly<Record<string, Refusal>> = {}, world: World = {}) {
  const employees = world.employees ?? EMPLOYEES;
  const requests: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      requests.push({ method, path: url.pathname, params: url.searchParams, body });

      if (method === "GET" && url.pathname === "/api/v1/org-units") return ok(200, TREE);
      if (method === "GET" && url.pathname === "/api/v1/locations") {
        return world.locationsForbidden === true ? problem(403, "Missing permission: core.org.location.read") : ok(200, SITES);
      }
      if (method === "GET" && url.pathname === "/api/v1/employees") {
        const page = Number(url.searchParams.get("page") ?? "1");
        const items = employees.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: employees.length, page, size: PER_PAGE, pages: Math.ceil(employees.length / PER_PAGE) });
      }
      const target = /^\/api\/v1\/employees\/([^/]+)$/.exec(url.pathname)?.[1];
      if (method === "PATCH" && target !== undefined && isRecord(body)) {
        const refusal = refuse[target];
        if (refusal !== undefined) return problem(refusal.status, refusal.detail);
        const found = employees.find((candidate) => candidate.id === target);
        if (found === undefined) return problem(404, "That employee is not part of this organization");
        return ok(200, { ...found, ...body });
      }
      if (method === "POST" && url.pathname === "/api/v1/employees" && isRecord(body)) {
        const number = String(body.employee_no);
        return ok(201, employee({ id: `new-${number}`, employee_no: number, full_name: String(body.full_name) }));
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return {
    /** Everything the run wrote, in order: the requests that change data. */
    writes: () =>
      requests
        .filter((request) => request.method !== "GET")
        .map(({ method, path, body }) => ({ method, path, body })),
    lists: () => requests.filter((request) => request.method === "GET" && request.path === "/api/v1/employees"),
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: EmployeeFilters = { q: "", status: "all", departmentId: undefined };

interface HarnessProps {
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ onImported }: HarnessProps) {
  const dx = useImportExport(employeesEntity, { filters: NO_FILTERS, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export employees" });
  return user;
}

const UPDATE_MODE = "Add new and update existing";
const ADD_MODE = "Add new records only";

/** Opens the dialog and chooses to update, as a person does before choosing a file. */
async function openUpdateDialog(onImported?: HarnessProps["onImported"]) {
  const user = await openDialog(onImported);
  await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
  return user;
}

/** The figure beside a count's label, from the term and its definition. */
function figure(label: string): string | null | undefined {
  return screen.getAllByRole("term").find((term) => term.textContent === label)?.nextElementSibling?.textContent;
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (input === null) throw new Error("The dialog has no file input");
  return input;
}

/** A row of the preview's table by the sheet's row number. */
function previewRow(rowNumber: number): HTMLElement {
  const table = screen.getByRole("table", { name: /^Rows found in / });
  const found = within(table)
    .getAllByRole("row")
    .find((row) => within(row).queryByRole("rowheader")?.textContent === String(rowNumber));
  if (found === undefined) throw new Error(`The preview has no row ${rowNumber}`);
  return found;
}

async function reviewFor(file: File, user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.upload(fileInput(), file);
  await screen.findByRole("dialog", { name: "Review your file" });
}

async function finishImport(user: ReturnType<typeof userEvent.setup>, buttonName: string): Promise<void> {
  await user.click(screen.getByRole("button", { name: buttonName }));
  await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });
}

// ------------------------------------------------------------------------------------ workbooks

/** Every column the entity has, read-only ones included: a file exported earlier has them all. */
const FILE_COLUMNS = employeesEntity.columns;

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)));
  for (const row of rows) sheet.addRow(FILE_COLUMNS.map((c) => row[c.key] ?? null));
  sheet.getColumn(FILE_COLUMNS.findIndex((c) => c.key === "date_of_joining") + 1).numFmt = "yyyy-mm-dd";
  return new File([await workbook.xlsx.writeBuffer()], "employees.xlsx", { type: XLSX_TYPE });
}

/** A sheet with only the columns named: the file a person makes to change one thing. */
async function partialFile(headers: readonly string[], rows: readonly ExcelJS.CellValue[][]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...headers]);
  for (const row of rows) sheet.addRow(row);
  return new File([await workbook.xlsx.writeBuffer()], "partial.xlsx", { type: XLSX_TYPE });
}

async function readDownload(download: Download | undefined): Promise<ExcelJS.Workbook> {
  if (download?.blob === undefined) throw new Error("Nothing was downloaded");
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await download.blob.arrayBuffer());
  return workbook;
}

function sheetNamed(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`The workbook has no sheet "${name}"`);
  return sheet;
}

function hintUpdateOf(key: string): string {
  const hint = employeesEntity.columns.find((column) => column.key === key)?.lookupHintUpdate;
  if (hint === undefined) throw new Error(`Column "${key}" has no lookupHintUpdate`);
  return hint;
}

// ------------------------------------------------------------------------------------ updating

describe("updating employees from Excel", () => {
  it(
    "previews what each row will do and sends only the fields that changed, in the endpoint's own keys",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          // 2: found by its number; the manager is the one it already has.
          { employee_no: "EMP-0012", designation: "Staff Engineer", work_mode: "Hybrid", reporting_manager: "EMP-0007" },
          // 3: every cell agrees with the record, including a joining date that is a real Excel date.
          {
            employee_no: "EMP-0007",
            full_name: "Rhea Kapoor",
            designation: "=1+1",
            department: "Human Resources",
            date_of_joining: new Date(Date.UTC(2024, 1, 1)),
          },
          // 4: found by Record ID, with no employee number at all. Two managers named: one is new,
          // the dotted-line one is what the record already has.
          { id: "e-31", department: "Operations", reporting_manager: "EMP-0012", dotted_line_manager: "EMP-0012" },
          // 5: an employee who has exited can still be updated.
          { employee_no: "EMP-0041", location_code: "Pune" },
          // 6: nobody has this number, so it is added.
          { employee_no: "EMP-9001", full_name: "New Hire", department: "Human Resources" },
          // 7: the sample sheet's first example, untouched.
          { ...(employeesEntity.sampleRows[0] ?? {}) },
        ]),
        user,
      );

      expect(figure("Rows in file")).toBe("6");
      expect(figure("New records")).toBe("1");
      expect(figure("Updates")).toBe("3");
      expect(figure("No change")).toBe("1");
      expect(figure("Need fixing")).toBe("0");
      expect(figure("Sample rows skipped")).toBe("1");
      expect(screen.getByText("4 rows ready to import (3 to update, 1 new), 1 unchanged.")).toBeTruthy();

      const table = within(screen.getByRole("table", { name: /^Rows found in / }));
      expect(table.getAllByRole("columnheader").slice(0, 2).map((cell) => cell.textContent)).toEqual(["Row", "Action"]);
      expect(within(previewRow(2)).getByText("Update")).toBeTruthy();
      expect(within(previewRow(2)).getByText("Changes: Designation, Work mode")).toBeTruthy();
      expect(within(previewRow(3)).getByText("No change")).toBeTruthy();
      expect(within(previewRow(4)).getByText("Changes: Department, Manager employee number")).toBeTruthy();
      expect(within(previewRow(5)).getByText("Changes: Location code")).toBeTruthy();
      expect(within(previewRow(6)).getByText("New")).toBeTruthy();
      expect(within(previewRow(7)).getByText("Sample")).toBeTruthy();
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Update 3, add 1");

      // Nothing for row 3. A manager only where it changed. The employee number never travels.
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-12", body: { designation: "Staff Engineer", work_mode: "hybrid" } },
        {
          method: "PATCH",
          path: "/api/v1/employees/e-31",
          body: { department_id: "d-ops", reporting_manager_id: "e-12" },
        },
        { method: "PATCH", path: "/api/v1/employees/e-41", body: { location_code: "PUN-03" } },
        {
          method: "POST",
          path: "/api/v1/employees",
          body: { employee_no: "EMP-9001", full_name: "New Hire", department_id: "d-hr" },
        },
      ]);
      // Every list read, for the lookups and for the records to compare with, took whole pages of 200 and no filter.
      for (const request of gateway.lists()) {
        expect(Object.fromEntries(request.params)).toMatchObject({ size: "200" });
        expect(request.params.has("q") || request.params.has("status") || request.params.has("department_id")).toBe(false);
      }

      expect(screen.getByText("Everything imported. 3 updated, 1 created, 1 unchanged.")).toBeTruthy();
      expect(figure("Created")).toBe("1");
      expect(figure("Updated")).toBe("3");
      expect(figure("Unchanged")).toBe("1");
      expect(figure("Failed")).toBe("0");
      expect(figure("Invalid")).toBe("0");
      expect(figure("Skipped, already exist")).toBeUndefined();
      expect(screen.queryByRole("table")).toBeNull();

      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 1, updated: 3, unchanged: 1, failed: 0, invalid: 0, cancelled: false }),
      );
      await user.click(screen.getByRole("button", { name: "Done" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(onImported).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "accepts a sheet of only the employee number and one column, and checks the same file again when the mode changes",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      const file = await partialFile(["Employee number", "Designation"], [["EMP-0012", "Staff Engineer"]]);

      // Adding: a sheet without a Full name column cannot add anyone.
      await reviewFor(file, user);
      expect(screen.getByText("Your file is missing required columns: Full name.")).toBeTruthy();
      expect(screen.getByText("Full name is required")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Import 0 rows" }) as HTMLButtonElement).disabled).toBe(true);

      // Updating: the same file is a change of one field, and nothing is missing.
      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      expect(screen.queryByText(/missing required columns/)).toBeNull();
      expect(within(previewRow(2)).getByText("Changes: Designation")).toBeTruthy();
      expect(screen.getByRole("radio", { name: UPDATE_MODE })).toHaveProperty("checked", true);

      // And back: the file is checked as an add-only file again.
      await user.click(screen.getByRole("radio", { name: ADD_MODE }));
      await waitFor(() => expect(screen.getByText("Your file is missing required columns: Full name.")).toBeTruthy());
      expect(gateway.writes()).toEqual([]);

      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      await finishImport(user, "Update 1");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-12", body: { designation: "Staff Engineer" } },
      ]);
      expect(screen.getByText("Everything imported. 1 updated.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses in the preview what an update must not do, and sends only the row that is fine",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: a Record ID nobody has.
          { id: "e-404", designation: "Ghost" },
          // 3: a Record ID with a different employee number: the number cannot change.
          { id: "e-31", employee_no: "EMP-9999" },
          // 4: the name cannot be emptied.
          { employee_no: "EMP-0041", full_name: "[clear]" },
          // 5: a manager who is not an employee.
          { employee_no: "EMP-0031", reporting_manager: "EMP-9999" },
          // 6: a department the organisation does not have.
          { employee_no: "EMP-0031", department: "Skunkworks" },
          // 7: an employee who would report to themselves.
          { employee_no: "EMP-0012", reporting_manager: "EMP-0012" },
          // 8: fine.
          { employee_no: "EMP-0007", designation: "Lead" },
          // 9: the same employee as row 8.
          { employee_no: "EMP-0007", job_title: "Ops" },
          // 10: a location that is neither a code nor a name.
          { employee_no: "EMP-0012", location_code: "Bangalore" },
        ]),
        user,
      );

      expect(figure("Rows in file")).toBe("9");
      expect(figure("Updates")).toBe("1");
      expect(figure("Need fixing")).toBe("8");
      const refusals = [
        "That Record ID is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see.",
        "Employee number cannot be changed once the record exists (it is 'EMP-0031'). Leave the cell as it is, or blank.",
        "Full name cannot be cleared",
        `'EMP-9999' is not one of the accepted values for Manager employee number (see the Lists sheet). ${hintUpdateOf("reporting_manager")}`,
        `'Skunkworks' is not one of the accepted values for Department (see the Lists sheet). ${hintUpdateOf("department")}`,
        "An employee cannot report to themselves.",
        "This row updates the same record as row 8. Keep only one of the two rows.",
        `'Bangalore' is not one of the accepted values for Location code (see the Lists sheet). ${hintUpdateOf("location_code")}`,
      ];
      for (const refusal of refusals) expect(screen.getByText(refusal), refusal).toBeTruthy();
      expect(screen.getAllByText("Needs fixing")).toHaveLength(8);
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Update 1");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-7", body: { designation: "Lead" } },
      ]);
      expect(screen.getByText("1 updated, 8 failed.")).toBeTruthy();
      expect(screen.getByText("8 rows were not imported. The result report lists every row.")).toBeTruthy();
      expect(figure("Updated")).toBe("1");
      expect(figure("Invalid")).toBe("8");
    },
    TEST_TIMEOUT,
  );

  it(
    "empties a field with [clear] using null and never an empty string, and ignores a [clear] on a field that is already empty",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: a location and a manager to remove; the designation and the dotted-line manager are already empty.
          {
            employee_no: "EMP-0012",
            designation: "[clear]",
            location_code: "[clear]",
            reporting_manager: "[clear]",
            dotted_line_manager: "[clear]",
          },
          // 3: a joining date to remove; a real date cell would be read as a date, this one says [clear].
          { employee_no: "EMP-0007", date_of_joining: "[clear]", job_title: "[clear]" },
          // 4: nothing there to clear.
          { employee_no: "EMP-0031", department: "[clear]" },
        ]),
        user,
      );

      expect(within(previewRow(2)).getByText("Changes: Location code, Manager employee number")).toBeTruthy();
      expect(within(previewRow(3)).getByText("Changes: Date of joining")).toBeTruthy();
      expect(within(previewRow(4)).getByText("No change")).toBeTruthy();

      await finishImport(user, "Update 2");

      const writes = gateway.writes();
      expect(writes).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-12", body: { location_code: null, reporting_manager_id: null } },
        { method: "PATCH", path: "/api/v1/employees/e-7", body: { date_of_joining: null } },
      ]);
      expect(JSON.stringify(writes)).not.toContain('""');
      expect(screen.getByText("Everything imported. 2 updated, 1 unchanged.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "reports a 409 and a 404 from the server as failed rows with its own words, never as skipped",
    async () => {
      const gateway = stubGateway({
        "e-31": { status: 409, detail: "That conflicts with something that already exists" },
        "e-41": { status: 404, detail: "That employee is not part of this organization" },
      });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          { employee_no: "EMP-0012", designation: "Staff Engineer" },
          { employee_no: "EMP-0031", designation: "Recruiter" },
          { employee_no: "EMP-0041", designation: "Senior Analyst" },
        ]),
        user,
      );
      await finishImport(user, "Update 3");

      expect(gateway.writes().map((request) => request.path)).toEqual([
        "/api/v1/employees/e-12",
        "/api/v1/employees/e-31",
        "/api/v1/employees/e-41",
      ]);
      expect(screen.getByText("1 updated, 2 failed.")).toBeTruthy();
      expect(figure("Updated")).toBe("1");
      expect(figure("Failed")).toBe("2");
      expect(figure("Skipped, already exist")).toBeUndefined();
      const table = within(screen.getByRole("table", { name: "Rows that were not imported" }));
      expect(table.getByText("That conflicts with something that already exists")).toBeTruthy();
      expect(table.getByText("That employee is not part of this organization")).toBeTruthy();
      expect(table.getAllByText("Failed")).toHaveLength(2);
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ updated: 1, failed: 2 }));
    },
    TEST_TIMEOUT,
  );

  it(
    "does not ask the screen to reload when every update failed",
    async () => {
      stubGateway({ "e-31": { status: 409, detail: "That conflicts with something that already exists" } });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(await importFile([{ employee_no: "EMP-0031", designation: "Recruiter" }]), user);
      await finishImport(user, "Update 1");

      expect(figure("Failed")).toBe("1");
      expect(figure("Updated")).toBe("0");
      expect(onImported).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it(
    "says there is nothing to change when every row already matches, and sends nothing",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          { employee_no: "EMP-0012", full_name: "Sameer Joshi", employment_type: "Part-time", work_mode: "remote", location_code: "Bengaluru HQ" },
          { id: "e-7", department: "human resources" },
        ]),
        user,
      );

      expect(figure("No change")).toBe("2");
      expect(figure("Updates")).toBe("0");
      expect(screen.getByText("Nothing to change: every row already matches its record.")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Nothing to change" }) as HTMLButtonElement).disabled).toBe(true);
      expect(gateway.writes()).toEqual([]);
      expect(onImported).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it(
    "warns when the file has no column that can name an employee, and adds every row as new",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(await partialFile(["Full name", "Designation"], [["Sameer Joshi", "Staff Engineer"]]), user);

      expect(
        screen.getByText(
          "This file has no Record ID or Employee number column, so it cannot update existing records; every row would be added as new.",
        ),
      ).toBeTruthy();
      expect(figure("New records")).toBe("0");
      expect(screen.getByText("Employee number is required to add a new record")).toBeTruthy();
      expect(gateway.writes()).toEqual([]);
    },
    TEST_TIMEOUT,
  );

  it(
    "does not refuse a row for a reference cell it did not change, whatever the lists can no longer say",
    async () => {
      // The caller cannot read the locations, and two employees have numbers that differ only in capitals.
      const gateway = stubGateway(
        {},
        {
          locationsForbidden: true,
          employees: [
            employee({ id: "e-7", employee_no: "EMP-0007", full_name: "Rhea Kapoor" }),
            employee({ id: "e-70", employee_no: "emp-0007", full_name: "Rhea Kapoor Jr" }),
            employee({
              id: "e-12",
              employee_no: "EMP-0012",
              full_name: "Sameer Joshi",
              location_code: "BLR-HQ",
              reporting_manager_id: "e-7",
            }),
            employee({ id: "e-31", employee_no: "EMP-0031", full_name: "Tara Menon" }),
          ],
        },
      );
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: an export sent back with one cell edited: the location and the manager are what it already has.
          {
            employee_no: "EMP-0012",
            location_code: "BLR-HQ",
            reporting_manager: "EMP-0007",
            designation: "Staff Engineer",
          },
          // 3: the same label, typed for an employee with no manager: it could be either of the two.
          { employee_no: "EMP-0031", reporting_manager: "emp-0007" },
        ]),
        user,
      );

      expect(figure("Updates")).toBe("1");
      expect(figure("Need fixing")).toBe("1");
      expect(within(previewRow(2)).getByText("Changes: Designation")).toBeTruthy();
      expect(
        screen.getByText(
          `'emp-0007' matches more than one of the accepted values for Manager employee number, so it cannot be chosen. ${hintUpdateOf("reporting_manager")}`,
        ),
      ).toBeTruthy();

      await finishImport(user, "Update 1");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-12", body: { designation: "Staff Engineer" } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses a joining date after the date of exit, and accepts one before it",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: Vikram Rao left on 2026-01-31.
          { employee_no: "EMP-0041", date_of_joining: "2026-02-15" },
          // 3: the same date for an employee who has not left.
          { employee_no: "EMP-0031", date_of_joining: "2026-02-15" },
          // 4: a joining date before the exit date.
          { employee_no: "EMP-0007", date_of_joining: "2024-03-01" },
        ]),
        user,
      );

      expect(figure("Updates")).toBe("2");
      expect(figure("Need fixing")).toBe("1");
      expect(screen.getByText("Date of joining cannot be after the date of exit, which is 2026-01-31.")).toBeTruthy();

      await finishImport(user, "Update 2");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/employees/e-31", body: { date_of_joining: "2026-02-15" } },
        { method: "PATCH", path: "/api/v1/employees/e-7", body: { date_of_joining: "2024-03-01" } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "aborts the request in flight when the screen is left, instead of reading on",
    async () => {
      const listSignals: AbortSignal[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((input: string, init?: RequestInit) => {
          const url = new URL(input, "http://gateway.test");
          if (url.pathname === "/api/v1/org-units") return Promise.resolve(ok(200, TREE));
          if (url.pathname === "/api/v1/locations") return Promise.resolve(ok(200, SITES));
          const given = init?.signal;
          if (url.pathname === "/api/v1/employees" && given instanceof AbortSignal) {
            listSignals.push(given);
            return new Promise<Response>((_, reject) => {
              given.addEventListener("abort", () => reject(given.reason as Error), { once: true });
            });
          }
          throw new Error(`Unexpected request: ${input}`);
        }),
      );
      const user = userEvent.setup();
      const view = render(<Harness />);
      await user.click(screen.getByRole("button", { name: "Import / Export" }));
      await screen.findByRole("dialog", { name: "Import / Export employees" });
      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await user.upload(fileInput(), await importFile([{ employee_no: "EMP-0012", designation: "Lead" }]));

      await waitFor(() => expect(listSignals.length).toBeGreaterThan(0));
      expect(listSignals.some((given) => given.aborted)).toBe(false);

      view.unmount();
      expect(listSignals.every((given) => given.aborted)).toBe(true);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("updating from an exported file", () => {
  async function exportedFile(user: ReturnType<typeof userEvent.setup>): Promise<Blob> {
    await user.click(screen.getByRole("button", { name: "Export to Excel" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    const blob = downloads[0]?.blob;
    if (blob === undefined) throw new Error("Nothing was downloaded");
    return blob;
  }

  it(
    "finds every employee by its Record ID and changes nothing when the export is sent back as it was",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();
      const blob = await exportedFile(user);

      await reviewFor(new File([blob], "employees-export.xlsx", { type: XLSX_TYPE }), user);

      expect(figure("Rows in file")).toBe("4");
      expect(figure("No change")).toBe("4");
      expect(figure("Updates")).toBe("0");
      expect(figure("Need fixing")).toBe("0");
      expect((screen.getByRole("button", { name: "Nothing to change" }) as HTMLButtonElement).disabled).toBe(true);
      expect(gateway.writes()).toEqual([]);
    },
    TEST_TIMEOUT,
  );

  it(
    "updates only the cell that was edited in the exported file",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();
      const blob = await exportedFile(user);

      const workbook = new ExcelJS.Workbook();
      await workbook.xlsx.load(await blob.arrayBuffer());
      const sheet = sheetNamed(workbook, "Export");
      const headers: string[] = [];
      sheet.getRow(1).eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      sheet.getCell(3, headers.indexOf("Designation") + 1).value = "Principal Engineer";
      sheet.getCell(3, headers.indexOf("Work mode") + 1).value = "Hybrid";
      const edited = new File([await workbook.xlsx.writeBuffer()], "employees-edited.xlsx", { type: XLSX_TYPE });

      await reviewFor(edited, user);
      expect(figure("Updates")).toBe("1");
      expect(figure("No change")).toBe("3");
      expect(within(previewRow(3)).getByText("Changes: Designation, Work mode")).toBeTruthy();

      await finishImport(user, "Update 1");
      expect(gateway.writes()).toEqual([
        {
          method: "PATCH",
          path: "/api/v1/employees/e-12",
          body: { designation: "Principal Engineer", work_mode: "hybrid" },
        },
      ]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ instructions

describe("the employees Instructions sheet", () => {
  it(
    "explains updating existing employees, from the engine's rules and the entity's own",
    async () => {
      stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const sheet = sheetNamed(await readDownload(downloads[0]), "Instructions");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push(row.getCell(1).text));

      const start = lines.indexOf("Updating existing employees");
      const end = lines.indexOf("General rules");
      expect(start).toBeGreaterThan(-1);
      expect(end).toBeGreaterThan(start);
      const section = lines.slice(start + 1, end).filter((line) => line !== "");
      // What an update ignores: the read-only columns an export fills and an import never reads.
      const ignored = employeesEntity.columns
        .filter((column) => column.readOnly === true && column.key !== employeesEntity.recordIdKey)
        .map((column) => column.header);
      expect(section).toEqual([
        'Choose "Add new and update existing" in the import dialog to use these rules.',
        "A row is matched to an existing record by its Record ID (the column an export writes). A row without one is matched by Employee number in its exact spelling (capitals and spaces count; a Employee number that differs from an existing one only in capitals or spaces is refused, not matched). A row that matches nothing is added as a new record. If two records have the same Employee number, the row is refused.",
        "A blank cell leaves the field alone. Only the cells you fill in are compared with the record, and only the fields that differ are changed.",
        "Type [clear] in a cell to empty a field. Only some fields can be emptied.",
        "Can be emptied with [clear]: Designation, Job title, Department, Location code, Date of joining, Manager employee number, Dotted-line manager employee number.",
        "Cannot be changed once created: Employee number. A row whose cell differs from the record is refused.",
        `Ignored when updating: ${ignored.join(", ")}.`,
        "Warning: updating overwrites live data and cannot be undone. In a file exported from this list, cells you did not change since the export are left alone, so someone else's later edit to them stays. Nothing detects that someone else changed a cell you did change after your export: your value replaces theirs. Check the preview before you import.",
      ]);
      // The entity's own rules about updating sit under Notes, in the same sheet.
      for (const note of (employeesEntity.notes ?? []).filter((text) => text.startsWith("Updating:"))) {
        expect(lines, note).toContain(note);
      }
      expect(lines).toContain(
        'With "Add new records only", existing employees are skipped, never overwritten. Importing the same file again is safe.',
      );
    },
    TEST_TIMEOUT,
  );
});
