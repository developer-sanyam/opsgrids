/**
 * The employees import and export end to end, the way a person meets them: the real
 * `useImportExport` hook from `@opsgrids/client` behind the real `ImportExportButton` dialog from
 * `@opsgrids/ui`, with only the gateway stubbed. A workbook is built in memory, chosen in the
 * dialog, previewed, imported row by row and reported; the export and the sample sheet are read
 * back with exceljs.
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

/** Two sites; the sample sheet's second location, MUM-01, is not one of them. */
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
    pii: {
      date_of_birth: "••••",
      personal_email: "s•••@example.com",
      personal_phone: "•••• 3210",
      pan: "•••••1234F",
      aadhaar: "•••• 9012",
      uan: null,
      esi: null,
      bank_account: "•••• 6789",
      bank_ifsc: "HDFC0001234",
    },
  }),
  employee({ id: "e-31", employee_no: "EMP-0031", full_name: "Tara Menon", dotted_line_manager_id: "e-12" }),
];
const PER_PAGE = 2;

function problem(status: number, title: string, detail: string, errors?: unknown): Response {
  return new Response(JSON.stringify({ status, title, detail, ...(errors === undefined ? {} : { errors }) }), {
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

/**
 * A gateway with the department tree, the employee list and `POST /employees`. An employee number
 * already on the list answers 409; EMP-3004 answers 403; anything else is created. A caller who may
 * not read departments or locations gets a 403 for that list.
 */
function stubGateway(options: { mayReadDepartments?: boolean; mayReadLocations?: boolean } = {}) {
  const { mayReadDepartments = true, mayReadLocations = true } = options;
  const requests: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      requests.push({ method, path: url.pathname, params: url.searchParams, body });

      if (method === "GET" && url.pathname === "/api/v1/org-units") {
        return mayReadDepartments ? ok(200, TREE) : problem(403, "Forbidden", "You cannot read the departments.");
      }
      if (method === "GET" && url.pathname === "/api/v1/locations") {
        return mayReadLocations ? ok(200, SITES) : problem(403, "Forbidden", "You cannot read the locations.");
      }
      if (method === "GET" && url.pathname === "/api/v1/employees") {
        const page = Number(url.searchParams.get("page") ?? "1");
        const items = EMPLOYEES.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: EMPLOYEES.length, page, size: PER_PAGE, pages: 2 });
      }
      if (method === "POST" && url.pathname === "/api/v1/employees" && isRecord(body)) {
        const number = String(body.employee_no);
        if (EMPLOYEES.some((existing) => existing.employee_no === number)) {
          return problem(409, "Conflict", `An employee numbered '${number}' already exists`);
        }
        if (number === "EMP-3004") {
          return problem(403, "Forbidden", "Your role cannot add employees to that department.");
        }
        return ok(201, employee({ id: `new-${number}`, employee_no: number, full_name: String(body.full_name) }));
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return {
    posts: () => requests.filter((request) => request.method === "POST"),
    lists: () => requests.filter((request) => request.method === "GET" && request.path === "/api/v1/employees"),
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: EmployeeFilters = { q: "", status: "all", departmentId: undefined };

interface HarnessProps {
  filters: EmployeeFilters;
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ filters, onImported }: HarnessProps) {
  const dx = useImportExport(employeesEntity, { filters, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(filters: EmployeeFilters = NO_FILTERS, onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness filters={filters} onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export employees" });
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

// ------------------------------------------------------------------------------------ workbooks

/** Every column the entity has, read-only ones included: a file exported earlier has them all. */
const FILE_COLUMNS = employeesEntity.columns;

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)), "PAN"]);
  for (const row of rows) sheet.addRow([...FILE_COLUMNS.map((c) => row[c.key] ?? null), row.pan ?? null]);
  const joined = FILE_COLUMNS.findIndex((c) => c.key === "date_of_joining") + 1;
  sheet.getColumn(joined).numFmt = "yyyy-mm-dd";
  return new File([await workbook.xlsx.writeBuffer()], "employees.xlsx", { type: XLSX_TYPE });
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

/** A row of a sheet as text by its header, so a test reads "Department" and not column F. */
function rowByHeader(sheet: ExcelJS.Worksheet, rowNumber: number): Record<string, string> {
  const record: Record<string, string> = {};
  sheet.getRow(1).eachCell({ includeEmpty: false }, (header, column) => {
    record[header.text.replace(/ \*$/, "")] = sheet.getRow(rowNumber).getCell(column).text;
  });
  return record;
}

// ------------------------------------------------------------------------------------ importing

const SAMPLE_ROW = employeesEntity.sampleRows[0] ?? {};

const FILE_ROWS: Record<string, ExcelJS.CellValue>[] = [
  // 2: a full row. The department is written Parent / Child, the location by its name, the manager
  // is on the second page of the employee list, the joining date is a real Excel date, and the
  // file carries a column an import must ignore (Status) and one it never accepts (PAN).
  {
    employee_no: "EMP-3001",
    full_name: "Ravi Iyer",
    designation: "Analyst",
    job_title: "Finance",
    department: "Engineering / Platform",
    location_code: "Bengaluru HQ",
    employment_type: "Contract",
    work_mode: "hybrid",
    date_of_joining: new Date(Date.UTC(2026, 4, 11)),
    reporting_manager: "EMP-0031",
    status: "Exited",
    pan: "ABCDE1234F", // pragma: allowlist secret (a made-up PAN: the test proves this column is never sent)
  },
  // 3: names by case-insensitive text, a department and a location by their codes, an ISO date
  // typed as text, both managers.
  {
    employee_no: "EMP-3002",
    full_name: "Meera Shah",
    department: "d-hr",
    location_code: "pun-03",
    date_of_joining: "2026-06-01",
    reporting_manager: "emp-0007",
    dotted_line_manager: "EMP-0012",
  },
  // 4: the server already has this number.
  { employee_no: "EMP-0012", full_name: "Sameer Joshi" },
  // 5: the server refuses this one.
  { employee_no: "EMP-3004", full_name: "Blocked Person", department: "Operations" },
  // 6: no employee number.
  { full_name: "No Number" },
  // 7: a department the organisation does not have.
  { employee_no: "EMP-3006", full_name: "Lost Department", department: "Skunkworks" },
  // 8: a location that is neither a code nor a name of one of the organisation's sites.
  { employee_no: "EMP-3007", full_name: "Lost Site", location_code: "Bangalore" },
  // 9: the sample sheet's first example, untouched.
  { ...SAMPLE_ROW },
];

const MISSING_NUMBER = "Employee number is required";
/** The column's own lookupHint: the engine appends it to its sentence for a reference not in the list. */
function hintOf(key: string): string {
  const hint = employeesEntity.columns.find((column) => column.key === key)?.lookupHint;
  if (hint === undefined) throw new Error(`Column "${key}" has no lookupHint`);
  return hint;
}

/** The engine's sentence for a cell the list does not hold, then the column's hint. */
function notAccepted(text: string, header: string, key: string): string {
  return `'${text}' is not one of the accepted values for ${header} (see the Lists sheet). ${hintOf(key)}`;
}

const UNKNOWN_DEPARTMENT = notAccepted("Skunkworks", "Department", "department");
const UNKNOWN_LOCATION = notAccepted("Bangalore", "Location code", "location_code");

describe("importing employees from Excel", () => {
  it(
    "previews the file, sends each valid row as the API's own body, and reports what happened to every row",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);

      await user.upload(fileInput(), await importFile(FILE_ROWS));
      await screen.findByRole("dialog", { name: "Review your file" });

      // The preview: counts first, then the reason against each row that cannot go.
      expect(figure("Rows in file")).toBe("8");
      expect(figure("Ready to import")).toBe("4");
      expect(figure("Need fixing")).toBe("3");
      expect(figure("Sample rows skipped")).toBe("1");
      expect(screen.getByText(MISSING_NUMBER)).toBeTruthy();
      expect(screen.getByText(UNKNOWN_DEPARTMENT)).toBeTruthy();
      expect(screen.getByText(UNKNOWN_LOCATION)).toBeTruthy();
      expect(screen.getByText("An unchanged example from the sample sheet. It is skipped.")).toBeTruthy();
      expect(screen.getByText("These columns are ignored: PAN.")).toBeTruthy();
      expect(gateway.posts()).toHaveLength(0);

      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      // (a) What was sent, in sheet order: ids resolved, ISO dates, nothing the API seals or owns.
      const bodies = gateway.posts().map((request) => request.body);
      expect(bodies).toEqual([
        {
          employee_no: "EMP-3001",
          full_name: "Ravi Iyer",
          designation: "Analyst",
          job_title: "Finance",
          department_id: "d-eng-plat",
          location_code: "BLR-HQ",
          employment_type: "contract",
          work_mode: "hybrid",
          date_of_joining: "2026-05-11",
          reporting_manager_id: "e-31",
        },
        {
          employee_no: "EMP-3002",
          full_name: "Meera Shah",
          department_id: "d-hr",
          location_code: "PUN-03",
          date_of_joining: "2026-06-01",
          reporting_manager_id: "e-7",
          dotted_line_manager_id: "e-12",
        },
        { employee_no: "EMP-0012", full_name: "Sameer Joshi" },
        { employee_no: "EMP-3004", full_name: "Blocked Person", department_id: "d-ops" },
      ]);
      for (const body of bodies) {
        for (const key of ["status", "pii", "pan", "id", "membership_id"]) expect(body).not.toHaveProperty(key);
        expect(JSON.stringify(body)).not.toMatch(/status|pii|ABCDE1234F/);
      }

      // (b) The outcome: four counts, one sentence, and the server's own words against its rows.
      expect(figure("Created")).toBe("2");
      expect(figure("Skipped, already exist")).toBe("1");
      expect(figure("Failed")).toBe("1");
      expect(figure("Invalid")).toBe("3");
      expect(screen.getByText("4 rows were not imported. 2 employees were created.")).toBeTruthy();
      const table = within(screen.getByRole("region", { name: "Rows that were not created" }));
      expect(table.getByText("An employee numbered 'EMP-0012' already exists")).toBeTruthy();
      expect(table.getByText("Your role cannot add employees to that department.")).toBeTruthy();
      expect(table.getByText(MISSING_NUMBER)).toBeTruthy();
      expect(table.getByText(UNKNOWN_DEPARTMENT)).toBeTruthy();
      expect(table.getByText(UNKNOWN_LOCATION)).toBeTruthy();
      // Each is against its own row in the sheet: the sheet's row numbers, header excluded.
      const numberOf = (text: string | RegExp) => {
        const row = table.getByText(text).closest("tr");
        return row === null ? null : within(row).getByRole("rowheader").textContent;
      };
      expect(numberOf("An employee numbered 'EMP-0012' already exists")).toBe("4");
      expect(numberOf("Your role cannot add employees to that department.")).toBe("5");
      expect(numberOf(MISSING_NUMBER)).toBe("6");
      expect(numberOf(UNKNOWN_DEPARTMENT)).toBe("7");
      expect(numberOf(UNKNOWN_LOCATION)).toBe("8");

      // (c) The screen is told once, so it can reload its list.
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 2, skipped: 1, failed: 1, invalid: 3, cancelled: false }),
      );

      await user.click(screen.getByRole("button", { name: "Done" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(onImported).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "keeps the result of the run as a workbook the person can download",
    async () => {
      stubGateway();
      const user = await openDialog();
      await user.upload(fileInput(), await importFile(FILE_ROWS));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      await user.click(screen.getByRole("button", { name: "Download result report" }));
      await waitFor(() => expect(downloads).toHaveLength(1));

      expect(downloads[0]?.name).toBe("employees-import-result.xlsx");
      const sheet = sheetNamed(await readDownload(downloads[0]), "Result");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push([1, 2, 3, 4].map((column) => row.getCell(column).text).join(" | ")));
      expect(lines.slice(0, 6)).toEqual([
        "Row | Employee number | Status | Message",
        "2 | EMP-3001 | Created | ",
        "3 | EMP-3002 | Created | ",
        "4 | EMP-0012 | Skipped | An employee numbered 'EMP-0012' already exists",
        "5 | EMP-3004 | Failed | Your role cannot add employees to that department.",
        `6 |  | Invalid | ${MISSING_NUMBER}`,
      ]);
      expect(lines).toHaveLength(8);
      expect(lines[6]).toBe(`7 | EMP-3006 | Invalid | ${UNKNOWN_DEPARTMENT}`);
      expect(lines[7]).toBe(`8 | EMP-3007 | Invalid | ${UNKNOWN_LOCATION}`);
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses a file that lacks the required columns before anything is sent",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      const workbook = new ExcelJS.Workbook();
      const sheet = workbook.addWorksheet("Import");
      sheet.addRow(["Designation", "Job title"]);
      sheet.addRow(["Analyst", "Finance"]);
      const file = new File([await workbook.xlsx.writeBuffer()], "partial.xlsx", { type: XLSX_TYPE });

      await user.upload(fileInput(), file);
      await screen.findByRole("dialog", { name: "Review your file" });

      expect(
        screen.getByText("Your file is missing required columns: Employee number, Full name."),
      ).toBeTruthy();
      expect(screen.getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(gateway.posts()).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );

  it(
    "creates nothing when every row already exists, and does not ask the screen to reload",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);
      const again = [
        { employee_no: "EMP-0007", full_name: "Rhea Kapoor" },
        { employee_no: "EMP-0012", full_name: "Sameer Joshi" },
      ];

      await user.upload(fileInput(), await importFile(again));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 2 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      expect(gateway.posts()).toHaveLength(2);
      expect(screen.getByText("Nothing new to import. Every row already exists, so nothing was overwritten.")).toBeTruthy();
      expect(figure("Created")).toBe("0");
      expect(figure("Skipped, already exist")).toBe("2");
      expect(onImported).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it(
    "stops after the row in flight when cancelled, keeps what was created, and tells the screen once",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);
      const rows = [3021, 3022, 3023, 3024].map((number) => ({
        employee_no: `EMP-${number}`,
        full_name: `Person ${number}`,
      }));

      await user.upload(fileInput(), await importFile(rows));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await waitFor(() => expect(gateway.posts().length).toBeGreaterThan(0));
      await user.click(screen.getByRole("button", { name: "Cancel import" }));
      await screen.findByRole("dialog", { name: "Import cancelled" }, { timeout: 10_000 });

      const sent = gateway.posts().length;
      expect(sent).toBeGreaterThanOrEqual(1);
      expect(sent).toBeLessThan(4);
      expect(figure("Created")).toBe(String(sent));
      expect(screen.getByText(/The remaining rows were not sent\.$/)).toBeTruthy();
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ created: sent, cancelled: true }));
    },
    TEST_TIMEOUT,
  );

  it(
    "still imports the rows that name no department when the caller may not read departments",
    async () => {
      const gateway = stubGateway({ mayReadDepartments: false });
      const user = await openDialog();
      const rows = [
        { employee_no: "EMP-3010", full_name: "No Department", reporting_manager: "EMP-0007" },
        { employee_no: "EMP-3011", full_name: "Names One", department: "Engineering" },
      ];

      await user.upload(fileInput(), await importFile(rows));
      await screen.findByRole("dialog", { name: "Review your file" });
      expect(figure("Ready to import")).toBe("1");
      expect(figure("Need fixing")).toBe("1");
      expect(screen.getByText(notAccepted("Engineering", "Department", "department"))).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Import 1 row" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      expect(gateway.posts().map((request) => request.body)).toEqual([
        { employee_no: "EMP-3010", full_name: "No Department", reporting_manager_id: "e-7" },
      ]);
      expect(figure("Created")).toBe("1");
      expect(figure("Invalid")).toBe("1");
    },
    TEST_TIMEOUT,
  );

  it(
    "still imports the rows that name no location when the caller may not read locations, and refuses the rest rather than save a code nobody can check",
    async () => {
      const gateway = stubGateway({ mayReadLocations: false });
      const user = await openDialog();
      const rows = [
        { employee_no: "EMP-3020", full_name: "No Location", department: "Human Resources" },
        { employee_no: "EMP-3021", full_name: "Names One", location_code: "BLR-HQ" },
      ];

      await user.upload(fileInput(), await importFile(rows));
      await screen.findByRole("dialog", { name: "Review your file" });
      expect(figure("Ready to import")).toBe("1");
      expect(figure("Need fixing")).toBe("1");
      expect(screen.getByText(notAccepted("BLR-HQ", "Location code", "location_code"))).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Import 1 row" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      expect(gateway.posts().map((request) => request.body)).toEqual([
        { employee_no: "EMP-3020", full_name: "No Location", department_id: "d-hr" },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "never saves the name a location was written by: 'Bangalore' and 'blr-hq' would leave the employee invisible to location-based roles",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      const rows = [
        { employee_no: "EMP-3030", full_name: "By Code", location_code: "blr-hq" },
        { employee_no: "EMP-3031", full_name: "By Name", location_code: "PUNE" },
        { employee_no: "EMP-3032", full_name: "By City", location_code: "Bangalore" },
      ];

      await user.upload(fileInput(), await importFile(rows));
      await screen.findByRole("dialog", { name: "Review your file" });
      expect(figure("Ready to import")).toBe("2");
      expect(figure("Need fixing")).toBe("1");
      expect(screen.getByText(UNKNOWN_LOCATION)).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Import 2 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      expect(gateway.posts().map((request) => request.body)).toEqual([
        { employee_no: "EMP-3030", full_name: "By Code", location_code: "BLR-HQ" },
        { employee_no: "EMP-3031", full_name: "By Name", location_code: "PUN-03" },
      ]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("exporting employees to Excel", () => {
  it(
    "asks the list for every page with the screen's filters and writes a workbook with no formulas",
    async () => {
      const gateway = stubGateway();
      const filters: EmployeeFilters = { q: "asha", status: "active", departmentId: "d-hr" };
      const user = await openDialog(filters);

      await user.click(screen.getByRole("button", { name: "Export to Excel" }));
      await waitFor(() => expect(downloads).toHaveLength(1));

      // The list endpoint, once per page, with the filters the screen had; not the page window.
      const exportCalls = gateway.lists().filter((request) => request.params.has("status"));
      expect(exportCalls.map((request) => Object.fromEntries(request.params))).toEqual([
        { q: "asha", status: "active", department_id: "d-hr", page: "1", size: "200" },
        { q: "asha", status: "active", department_id: "d-hr", page: "2", size: "200" },
      ]);

      expect(downloads[0]?.name).toMatch(/^employees-\d{4}-\d{2}-\d{2}\.xlsx$/);
      const sheet = sheetNamed(await readDownload(downloads[0]), "Export");

      const headers: string[] = [];
      sheet.getRow(1).eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      expect(headers).toEqual(employeesEntity.columns.map((column) => column.header));
      expect(sheet.actualRowCount).toBe(1 + EMPLOYEES.length);

      expect(rowByHeader(sheet, 3)).toMatchObject({
        "Employee number": "EMP-0012",
        "Full name": "Sameer Joshi",
        Department: "Engineering / Platform",
        "Location code": "BLR-HQ",
        "Employment type": "Part-time",
        "Work mode": "Remote",
        "Manager employee number": "EMP-0007",
        Status: "On Notice",
        "Record ID": "e-12",
      });
      expect(rowByHeader(sheet, 4)).toMatchObject({
        "Employee number": "EMP-0031",
        "Dotted-line manager employee number": "EMP-0012",
      });

      // Text that looks like a formula is a string, and no cell anywhere is a formula.
      const injected = sheet.getCell(2, headers.indexOf("Designation") + 1);
      expect(injected.value).toBe("=1+1");
      expect(injected.type).toBe(ExcelJS.ValueType.String);
      const formulas: string[] = [];
      sheet.eachRow((row) =>
        row.eachCell({ includeEmpty: false }, (cell) => {
          if (cell.type === ExcelJS.ValueType.Formula) formulas.push(cell.address);
        }),
      );
      expect(formulas).toEqual([]);

      // Sealed details never reach the file, masked or not.
      const everything: string[] = [];
      sheet.eachRow((row) => row.eachCell({ includeEmpty: false }, (cell) => everything.push(cell.text)));
      expect(everything.join("\n")).not.toMatch(/••|HDFC|1234F/);
    },
    TEST_TIMEOUT,
  );

  it(
    "writes a file that imports again as the same records once the Record ID column is taken out",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Export to Excel" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const blob = downloads[0]?.blob;
      if (blob === undefined) throw new Error("Nothing was downloaded");

      // A row with a Record ID came out of the system and is refused, so the column goes first.
      const workbook = new ExcelJS.Workbook();
      await workbook.xlsx.load(await blob.arrayBuffer());
      const exported = sheetNamed(workbook, "Export");
      let idColumn = 0;
      exported.getRow(1).eachCell((cell, column) => {
        if (cell.text === "Record ID") idColumn = column;
      });
      expect(idColumn).toBeGreaterThan(0);
      exported.spliceColumns(idColumn, 1);
      const edited = new File([await workbook.xlsx.writeBuffer()], "employees-export.xlsx", { type: XLSX_TYPE });

      await user.upload(fileInput(), edited);
      await screen.findByRole("dialog", { name: "Review your file" });
      expect(figure("Rows in file")).toBe("3");
      expect(figure("Ready to import")).toBe("3");
      expect(figure("Need fixing")).toBe("0");

      await user.click(screen.getByRole("button", { name: "Import 3 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      // Every number is on the register already, so each row is skipped; what was sent is what the
      // API had returned, department and manager included, and never the status or the id.
      expect(gateway.posts().map((request) => request.body)).toEqual([
        {
          employee_no: "EMP-0007",
          full_name: "Rhea Kapoor",
          designation: "=1+1",
          department_id: "d-hr",
          employment_type: "full_time",
          work_mode: "office",
          date_of_joining: "2024-02-01",
        },
        {
          employee_no: "EMP-0012",
          full_name: "Sameer Joshi",
          department_id: "d-eng-plat",
          location_code: "BLR-HQ",
          employment_type: "part_time",
          work_mode: "remote",
          reporting_manager_id: "e-7",
        },
        {
          employee_no: "EMP-0031",
          full_name: "Tara Menon",
          employment_type: "full_time",
          work_mode: "office",
          dotted_line_manager_id: "e-12",
        },
      ]);
      expect(figure("Skipped, already exist")).toBe("3");
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ sample sheet

describe("the employees sample sheet", () => {
  async function downloadSample(): Promise<ExcelJS.Workbook> {
    stubGateway();
    const user = await openDialog();
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe("employees-sample.xlsx");
    return readDownload(downloads[0]);
  }

  const importable = employeesEntity.columns.filter((column) => column.readOnly !== true);

  it(
    "has Import, Instructions and Lists, with required headers marked * and nothing the system sets",
    async () => {
      const workbook = await downloadSample();
      expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Import", "Instructions", "Lists"]);

      const headers: string[] = [];
      sheetNamed(workbook, "Import")
        .getRow(1)
        .eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      expect(headers).toEqual(
        importable.map((column) => (column.required === true ? `${column.header} *` : column.header)),
      );
      expect(headers.filter((header) => header.endsWith("*"))).toEqual(["Employee number *", "Full name *"]);
      for (const readOnly of ["Status", "Date of exit", "Created", "Record ID"]) {
        expect(headers).not.toContain(readOnly);
      }
    },
    TEST_TIMEOUT,
  );

  it(
    "carries the example rows, swapping a department or location the organisation lacks for one it has",
    async () => {
      const sheet = sheetNamed(await downloadSample(), "Import");
      expect(sheet.actualRowCount).toBe(1 + employeesEntity.sampleRows.length);

      const first = rowByHeader(sheet, 2);
      expect(first).toMatchObject({
        "Employee number": "EMP-1001",
        "Full name": "Aarav Mehta",
        Department: "Engineering",
        "Location code": "BLR-HQ",
        "Employment type": "Full-time",
        "Work mode": "Hybrid",
      });
      // Written as a real date, not as text Excel might reinterpret.
      const joined = sheet.getCell(2, importable.findIndex((column) => column.key === "date_of_joining") + 1).value;
      if (!(joined instanceof Date)) throw new Error("The joining date was not written as a date");
      expect(joined.toISOString().slice(0, 10)).toBe("2025-06-02");

      expect(rowByHeader(sheet, 3).Department).toBe("Human Resources");
      // The third example names Sales, which this organisation does not have.
      expect(employeesEntity.sampleRows[2]?.department).toBe("Sales");
      expect(rowByHeader(sheet, 4).Department).toBe("Engineering");
      expect(rowByHeader(sheet, 4)["Manager employee number"]).toBe("EMP-0007");
      // ... and MUM-01, which is not one of this organisation's locations, becomes one that is.
      expect(employeesEntity.sampleRows[2]?.location_code).toBe("MUM-01");
      expect(rowByHeader(sheet, 4)["Location code"]).toBe("BLR-HQ");
    },
    TEST_TIMEOUT,
  );

  it(
    "explains every column and repeats every rule the entity states",
    async () => {
      const sheet = sheetNamed(await downloadSample(), "Instructions");
      const firstColumn: string[] = [];
      const rows = new Map<string, string[]>();
      sheet.eachRow((row) => {
        const cells: string[] = [];
        for (let column = 1; column <= 6; column += 1) cells.push(row.getCell(column).text);
        firstColumn.push(cells[0] ?? "");
        rows.set(cells[0] ?? "", cells);
      });

      for (const column of importable) {
        const line = rows.get(column.header);
        expect(line, column.header).toBeDefined();
        expect(line?.[1], `${column.header} required`).toBe(column.required === true ? "Yes" : "No");
        expect(line?.[5], `${column.header} help`).toBe(column.help);
      }
      expect(rows.get("Employment type")?.[3]).toBe("Full-time, Part-time, Contract, Intern");
      expect(rows.get("Department")?.[3]).toBe('The "Department" list on the Lists sheet');
      expect(rows.get("Location code")?.[3]).toBe('The "Location code" list on the Lists sheet');
      for (const readOnly of ["Status", "Date of exit", "Created", "Record ID"]) {
        expect(firstColumn).not.toContain(readOnly);
      }
      for (const note of employeesEntity.notes ?? []) expect(firstColumn, note).toContain(note);
    },
    TEST_TIMEOUT,
  );

  it(
    "lists this organisation's departments, locations and employee numbers, and offers dropdowns over them and over the enums",
    async () => {
      const workbook = await downloadSample();
      const lists = sheetNamed(workbook, "Lists");
      const columnOf = (header: string): string[] => {
        const found: string[] = [];
        let index = 0;
        lists.getRow(1).eachCell({ includeEmpty: false }, (cell, column) => {
          if (cell.text === header) index = column;
        });
        lists.eachRow((row, rowNumber) => {
          if (rowNumber > 1 && index > 0) found.push(row.getCell(index).text);
        });
        return found.filter((label) => label !== "");
      };
      // Each department by name, then by code; each location by code and by name.
      const departments = [
        "Engineering",
        "Engineering / Platform",
        "Web",
        "Operations",
        "Operations / Platform",
        "Human Resources",
        "D-ENG",
        "D-ENG-PLAT",
        "D-ENG-WEB",
        "D-OPS",
        "D-OPS-PLAT",
        "D-HR",
      ];
      const locations = ["BLR-HQ", "Bengaluru HQ", "PUN-03", "Pune"];
      const managers = ["EMP-0007", "EMP-0012", "EMP-0031"];
      expect(columnOf("Department")).toEqual(departments);
      expect(columnOf("Location code")).toEqual(locations);
      expect(columnOf("Manager employee number")).toEqual(managers);

      const imports = sheetNamed(workbook, "Import");
      const validationOf = (key: string): ExcelJS.DataValidation | undefined =>
        imports.getCell(2, importable.findIndex((column) => column.key === key) + 1).dataValidation;
      expect(validationOf("employment_type")).toMatchObject({
        type: "list",
        formulae: ['"Full-time,Part-time,Contract,Intern"'],
      });
      expect(validationOf("work_mode")).toMatchObject({
        type: "list",
        formulae: ['"Office,Field,Remote,Hybrid"'],
      });
      // Short lists sit inside the dropdown itself; the Lists sheet holds the same values to read.
      expect(validationOf("department")).toMatchObject({ type: "list", formulae: [`"${departments.join(",")}"`] });
      expect(validationOf("location_code")).toMatchObject({ type: "list", formulae: [`"${locations.join(",")}"`] });
      expect(validationOf("reporting_manager")).toMatchObject({ type: "list", formulae: [`"${managers.join(",")}"`] });
      expect(validationOf("dotted_line_manager")).toMatchObject({ type: "list", formulae: [`"${managers.join(",")}"`] });
      // Free text and dates get no dropdown.
      expect(validationOf("designation")).toBeUndefined();
      expect(validationOf("date_of_joining")).toBeUndefined();
    },
    TEST_TIMEOUT,
  );

  it(
    "is recognised as examples when it is chosen again unchanged, so nothing is imported",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const blob = downloads[0]?.blob;
      if (blob === undefined) throw new Error("Nothing was downloaded");

      await user.upload(fileInput(), new File([blob], "employees-sample.xlsx", { type: XLSX_TYPE }));
      await screen.findByRole("dialog", { name: "Review your file" });

      expect(figure("Rows in file")).toBe("3");
      expect(figure("Sample rows skipped")).toBe("3");
      expect(figure("Ready to import")).toBe("0");
      expect(screen.getByText("Only the sample rows are in this file.")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(gateway.posts()).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );
});
