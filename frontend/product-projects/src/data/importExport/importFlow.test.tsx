/**
 * The projects import and export end to end, the way a person meets them: the real
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

import type { MemberOut, ProjectOut } from "@/api/types";

import { portfolioEntity } from "./portfolio";
import type { PortfolioFilters } from "./portfolio";

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

function member(id: string, name: string): MemberOut {
  return { id, name, role: "Delivery lead", directory_id: null };
}

/** The team, served two to a page whatever `size` the caller asks for. Two members share a name. */
const TEAM: MemberOut[] = [
  member("p-01", "Meera Iyer"),
  member("p-02", "Arjun Rao"),
  member("p-03", "Neha Kulkarni"),
  member("p-04", "Sam Patel"),
  member("p-05", "Sam Patel"),
];

function project(over: Partial<ProjectOut>): ProjectOut {
  return {
    id: "proj-0",
    code: "PRJ-000",
    name: "Something",
    client: "",
    owner: { id: "", name: "Unassigned", role: "" },
    phase: "discovery",
    health: "on_track",
    progress_pct: 0,
    start_date: "2026-01-05",
    target_date: "2026-06-30",
    budget_minor: 0,
    spent_minor: 0,
    currency: "INR",
    team_size: 0,
    open_tasks: 0,
    milestone_count: 0,
    summary: "",
    ...over,
  };
}

/** The portfolio, served two to a page. Text that looks like a formula is data here. */
const PORTFOLIO: ProjectOut[] = [
  project({
    id: "proj-1",
    code: "PRJ-104",
    name: "=1+1",
    client: "+91 Traders",
    summary: "@SUM(A1)",
    owner: { id: "p-01", name: "Meera Iyer", role: "Delivery lead" },
    phase: "build",
    health: "at_risk",
    progress_pct: 45,
    budget_minor: 250000050,
    spent_minor: 112000000,
    team_size: 6,
    open_tasks: 12,
    milestone_count: 4,
  }),
  project({ id: "proj-2", code: "PRJ-105", name: "Kiosk pilot", currency: "KWD", budget_minor: 12345 }),
  project({
    id: "proj-3",
    code: "PRJ-106",
    name: "Payments gateway migration",
    owner: { id: "p-02", name: "Arjun Rao", role: "Engineer" },
    currency: "JPY",
    budget_minor: 1500000,
    spent_minor: 250000,
  }),
];
const PER_PAGE = 2;

function problem(status: number, title: string, detail: string): Response {
  return new Response(JSON.stringify({ status, title, detail }), {
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
 * A gateway with the team, the portfolio and `POST /projects-workspace/projects`. A code already
 * in the portfolio answers 409; PRJ-205 answers 403; anything else is created.
 */
function stubGateway(team: readonly MemberOut[] = TEAM) {
  const requests: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      requests.push({ method, path: url.pathname, params: url.searchParams, body });
      const page = Number(url.searchParams.get("page") ?? "1");

      if (method === "GET" && url.pathname === "/api/v1/projects-workspace/members") {
        const items = team.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: team.length, page, size: PER_PAGE, pages: Math.ceil(team.length / PER_PAGE) });
      }
      if (method === "GET" && url.pathname === "/api/v1/projects-workspace/projects") {
        const items = PORTFOLIO.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: PORTFOLIO.length, page, size: PER_PAGE, pages: 2 });
      }
      if (method === "POST" && url.pathname === "/api/v1/projects-workspace/projects" && isRecord(body)) {
        const code = String(body.code);
        if (PORTFOLIO.some((existing) => existing.code === code)) {
          return problem(409, "Conflict", `A project with code '${code}' already exists`);
        }
        if (code === "PRJ-205") {
          return problem(403, "Forbidden", "You can only make yourself the owner of a project.");
        }
        return ok(201, project({ id: `new-${code}`, code, name: String(body.name) }));
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return {
    posts: () => requests.filter((request) => request.method === "POST"),
    projectLists: () =>
      requests.filter((request) => request.method === "GET" && request.path.endsWith("/projects")),
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: PortfolioFilters = { q: "", health: "all", phase: "all", sort: "code" };

interface HarnessProps {
  filters: PortfolioFilters;
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ filters, onImported }: HarnessProps) {
  const dx = useImportExport(portfolioEntity, { filters, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(filters: PortfolioFilters = NO_FILTERS, onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness filters={filters} onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export projects" });
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
const FILE_COLUMNS = portfolioEntity.columns;
const DATE_KEYS = ["start_date", "target_date"];

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)), "Cost centre"]);
  for (const row of rows) sheet.addRow([...FILE_COLUMNS.map((c) => row[c.key] ?? null), row.cost_centre ?? null]);
  for (const key of DATE_KEYS) sheet.getColumn(FILE_COLUMNS.findIndex((c) => c.key === key) + 1).numFmt = "yyyy-mm-dd";
  return new File([await workbook.xlsx.writeBuffer()], "projects.xlsx", { type: XLSX_TYPE });
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

/** A row of a sheet as text by its header, so a test reads "Owner" and not column D. */
function rowByHeader(sheet: ExcelJS.Worksheet, rowNumber: number): Record<string, string> {
  const record: Record<string, string> = {};
  sheet.getRow(1).eachCell({ includeEmpty: false }, (header, column) => {
    record[header.text.replace(/ \*$/, "")] = sheet.getRow(rowNumber).getCell(column).text;
  });
  return record;
}

// ------------------------------------------------------------------------------------ importing

const SAMPLE_ROW = portfolioEntity.sampleRows[0] ?? {};

const FILE_ROWS: Record<string, ExcelJS.CellValue>[] = [
  // 2: a full row. The owner is on the team's second page, both dates are there (one a real Excel
  // date, one text), the budget has cents, the summary runs over two lines, and the file carries
  // columns an import must ignore (Open tasks, Record ID) or does not know (Cost centre).
  {
    code: "PRJ-201",
    name: "Ledger migration",
    client: "Fabrikam Retail",
    owner: "Neha Kulkarni",
    phase: "stabilize",
    health: "At risk",
    progress_pct: 45,
    start_date: new Date(Date.UTC(2026, 2, 2)),
    target_date: "2026-09-30",
    budget: 2500000.5,
    spent: 1120000,
    currency: "eur",
    team_size: 6,
    summary: "Rebuild the ledger.\nCut over in Q3.",
    open_tasks: 99,
    id: "not-an-id-to-send",
    cost_centre: "CC-9",
  },
  // 3: a currency with three decimals, and an owner in the wrong case.
  {
    code: "PRJ-202",
    name: "Kiosk rollout",
    owner: "meera iyer",
    start_date: "2026-04-06",
    target_date: "2026-07-31",
    budget: "12.345",
    currency: "KWD",
  },
  // 4: the portfolio already has this code.
  { code: "PRJ-105", name: "Duplicate project", start_date: "2026-01-05", target_date: "2026-02-05" },
  // 5: the server refuses this one.
  {
    code: "PRJ-205",
    name: "Contested ownership",
    owner: "Arjun Rao",
    start_date: "2026-01-05",
    target_date: "2026-02-05",
  },
  // 6: no start date.
  { code: "PRJ-206", name: "Undated", target_date: "2026-02-05" },
  // 7: an owner the team does not have.
  { code: "PRJ-207", name: "Ownerless", owner: "Nobody Here", start_date: "2026-01-05", target_date: "2026-02-05" },
  // 8: an owner two members share.
  { code: "PRJ-208", name: "Twin owner", owner: "Sam Patel", start_date: "2026-01-05", target_date: "2026-02-05" },
  // 9: due before it starts.
  { code: "PRJ-209", name: "Backwards", start_date: "2026-03-01", target_date: "2026-02-01" },
  // 10: the sample sheet's first example, untouched.
  { ...SAMPLE_ROW },
];

const MISSING_START = "Start date is required";
const UNKNOWN_OWNER = "'Nobody Here' is not in the list of Owners (see the Lists sheet)";
const AMBIGUOUS_OWNER = "'Sam Patel' matches more than one of the Owners (see the Lists sheet), so it cannot be chosen";
const BACKWARDS = "The target date cannot be before the start date.";

describe("importing projects from Excel", () => {
  it(
    "previews the file, sends each valid row as the API's own body, and reports what happened to every row",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);

      await user.upload(fileInput(), await importFile(FILE_ROWS));
      await screen.findByRole("dialog", { name: "Review your file" });

      // The preview: counts first, then the reason against each row that cannot go.
      expect(figure("Rows in file")).toBe("9");
      expect(figure("Ready to import")).toBe("4");
      expect(figure("Need fixing")).toBe("4");
      expect(figure("Sample rows skipped")).toBe("1");
      for (const reason of [MISSING_START, UNKNOWN_OWNER, AMBIGUOUS_OWNER, BACKWARDS]) {
        expect(screen.getByText(reason), reason).toBeTruthy();
      }
      expect(screen.getByText("An unchanged example from the sample sheet. It is skipped.")).toBeTruthy();
      expect(screen.getByText("These columns are ignored: Cost centre.")).toBeTruthy();
      expect(gateway.posts()).toHaveLength(0);

      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      // (a) What was sent, in sheet order: the owner's id, minor units by each currency's own
      // decimals, ISO dates, and nothing the system counts or owns.
      const bodies = gateway.posts().map((request) => request.body);
      expect(bodies).toEqual([
        {
          code: "PRJ-201",
          name: "Ledger migration",
          client: "Fabrikam Retail",
          owner_ref: "p-03",
          phase: "stabilize",
          health: "at_risk",
          progress_pct: 45,
          start_date: "2026-03-02",
          target_date: "2026-09-30",
          budget_minor: 250000050,
          spent_minor: 112000000,
          currency: "EUR",
          team_size: 6,
          summary: "Rebuild the ledger.\nCut over in Q3.",
        },
        {
          code: "PRJ-202",
          name: "Kiosk rollout",
          owner_ref: "p-01",
          start_date: "2026-04-06",
          target_date: "2026-07-31",
          budget_minor: 12345,
          currency: "KWD",
        },
        { code: "PRJ-105", name: "Duplicate project", start_date: "2026-01-05", target_date: "2026-02-05" },
        {
          code: "PRJ-205",
          name: "Contested ownership",
          owner_ref: "p-02",
          start_date: "2026-01-05",
          target_date: "2026-02-05",
        },
      ]);
      for (const body of bodies) {
        for (const key of ["open_tasks", "milestone_count", "id", "cost_centre"]) expect(body).not.toHaveProperty(key);
      }

      // (b) The outcome: four counts, one sentence, and the server's own words against its rows.
      expect(figure("Created")).toBe("2");
      expect(figure("Skipped, already exist")).toBe("1");
      expect(figure("Failed")).toBe("1");
      expect(figure("Invalid")).toBe("4");
      expect(screen.getByText("5 rows were not imported. 2 projects were created.")).toBeTruthy();
      const table = within(screen.getByRole("region", { name: "Rows that were not created" }));
      const duplicate = "A project with code 'PRJ-105' already exists";
      const refused = "You can only make yourself the owner of a project.";
      for (const text of [duplicate, refused, MISSING_START, UNKNOWN_OWNER, AMBIGUOUS_OWNER, BACKWARDS]) {
        expect(table.getByText(text), text).toBeTruthy();
      }
      // Each is against its own row in the sheet: the sheet's row numbers, header excluded.
      const numberOf = (text: string) => {
        const row = table.getByText(text).closest("tr");
        return row === null ? null : within(row).getByRole("rowheader").textContent;
      };
      expect(numberOf(duplicate)).toBe("4");
      expect(numberOf(refused)).toBe("5");
      expect(numberOf(MISSING_START)).toBe("6");
      expect(numberOf(UNKNOWN_OWNER)).toBe("7");
      expect(numberOf(AMBIGUOUS_OWNER)).toBe("8");
      expect(numberOf(BACKWARDS)).toBe("9");

      // (c) The screen is told once, so it can reload its list.
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 2, skipped: 1, failed: 1, invalid: 4, cancelled: false }),
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

      expect(downloads[0]?.name).toBe("projects-import-result.xlsx");
      const sheet = sheetNamed(await readDownload(downloads[0]), "Result");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push([1, 2, 3, 4].map((column) => row.getCell(column).text).join(" | ")));
      expect(lines).toEqual([
        "Row | Project code | Status | Message",
        "2 | PRJ-201 | Created | ",
        "3 | PRJ-202 | Created | ",
        "4 | PRJ-105 | Skipped | A project with code 'PRJ-105' already exists",
        "5 | PRJ-205 | Failed | You can only make yourself the owner of a project.",
        `6 | PRJ-206 | Invalid | ${MISSING_START}`,
        `7 | PRJ-207 | Invalid | ${UNKNOWN_OWNER}`,
        `8 | PRJ-208 | Invalid | ${AMBIGUOUS_OWNER}`,
        `9 | PRJ-209 | Invalid | ${BACKWARDS}`,
      ]);
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
      sheet.addRow(["Client", "Summary"]);
      sheet.addRow(["Fabrikam Retail", "A project"]);
      const file = new File([await workbook.xlsx.writeBuffer()], "partial.xlsx", { type: XLSX_TYPE });

      await user.upload(fileInput(), file);
      await screen.findByRole("dialog", { name: "Review your file" });

      expect(
        screen.getByText("Your file is missing required columns: Project code, Name, Start date, Target date."),
      ).toBeTruthy();
      expect(screen.getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(gateway.posts()).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("exporting projects to Excel", () => {
  it(
    "asks the list for every page with the screen's filters and writes a workbook with no formulas",
    async () => {
      const gateway = stubGateway();
      const filters: PortfolioFilters = { q: "portal", health: "at_risk", phase: "build", sort: "target" };
      const user = await openDialog(filters);

      await user.click(screen.getByRole("button", { name: "Export to Excel" }));
      await waitFor(() => expect(downloads).toHaveLength(1));

      // The list endpoint, once per page, with the filters and sort the screen had.
      expect(gateway.projectLists().map((request) => Object.fromEntries(request.params))).toEqual([
        { q: "portal", health: "at_risk", phase: "build", sort: "target", page: "1", size: "200" },
        { q: "portal", health: "at_risk", phase: "build", sort: "target", page: "2", size: "200" },
      ]);

      expect(downloads[0]?.name).toMatch(/^projects-\d{4}-\d{2}-\d{2}\.xlsx$/);
      const sheet = sheetNamed(await readDownload(downloads[0]), "Export");
      const headers: string[] = [];
      sheet.getRow(1).eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      expect(headers).toEqual(portfolioEntity.columns.map((column) => column.header));
      expect(sheet.actualRowCount).toBe(1 + PORTFOLIO.length);

      expect(rowByHeader(sheet, 2)).toMatchObject({
        "Project code": "PRJ-104",
        Owner: "Meera Iyer",
        Phase: "Build",
        Health: "At risk",
        Currency: "INR",
        "Open tasks": "12",
        Milestones: "4",
        "Record ID": "proj-1",
      });
      // An unowned project has no owner to name, not one called "Unassigned".
      expect(rowByHeader(sheet, 3).Owner).toBe("");

      // Money is a number in major units, shown with the currency's own decimals.
      const budget = headers.indexOf("Budget") + 1;
      const cells = [2, 3, 4].map((row) => sheet.getCell(row, budget));
      expect(cells.map((cell) => cell.value)).toEqual([2500000.5, 12.345, 1500000]);
      expect(cells.map((cell) => cell.numFmt)).toEqual(["#,##0.00", "#,##0.000", "#,##0"]);
      expect(sheet.getCell(2, headers.indexOf("Spent") + 1).value).toBe(1120000);

      // Text that looks like a formula is a string, and no cell anywhere is a formula.
      for (const [header, text] of [
        ["Name", "=1+1"],
        ["Client", "+91 Traders"],
        ["Summary", "@SUM(A1)"],
      ] as const) {
        const cell = sheet.getCell(2, headers.indexOf(header) + 1);
        expect(cell.value, header).toBe(text);
        expect(cell.type, header).toBe(ExcelJS.ValueType.String);
      }
      const formulas: string[] = [];
      sheet.eachRow((row) =>
        row.eachCell({ includeEmpty: false }, (cell) => {
          if (cell.type === ExcelJS.ValueType.Formula) formulas.push(cell.address);
        }),
      );
      expect(formulas).toEqual([]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ sample sheet

describe("the projects sample sheet", () => {
  /** A team that has one of the owners the examples name, and lacks the other. */
  const SMALL_TEAM = [member("p-06", "Ishaan Verma"), member("p-02", "Arjun Rao")];

  async function downloadSample(): Promise<ExcelJS.Workbook> {
    stubGateway(SMALL_TEAM);
    const user = await openDialog();
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe("projects-sample.xlsx");
    return readDownload(downloads[0]);
  }

  const importable = portfolioEntity.columns.filter((column) => column.readOnly !== true);
  const position = (key: string) => importable.findIndex((column) => column.key === key) + 1;

  it(
    "has Import, Instructions and Lists, with required headers marked * and nothing the system counts",
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
      expect(headers.filter((header) => header.endsWith("*"))).toEqual([
        "Project code *",
        "Name *",
        "Start date *",
        "Target date *",
      ]);
      for (const readOnly of ["Open tasks", "Milestones", "Record ID"]) expect(headers).not.toContain(readOnly);
    },
    TEST_TIMEOUT,
  );

  it(
    "carries the example rows, swapping an owner the team lacks for one it has, with money as numbers and dates as dates",
    async () => {
      const sheet = sheetNamed(await downloadSample(), "Import");
      expect(sheet.actualRowCount).toBe(1 + portfolioEntity.sampleRows.length);

      expect(rowByHeader(sheet, 2)).toMatchObject({
        "Project code": "PRJ-104",
        Name: "Customer portal redesign",
        Phase: "Build",
        Health: "On track",
        Currency: "INR",
      });
      // The examples name Meera Iyer and Arjun Rao; this team has Arjun and not Meera.
      expect(portfolioEntity.sampleRows.map((sample) => sample.owner)).toEqual(["Meera Iyer", "Arjun Rao", "Meera Iyer"]);
      expect(rowByHeader(sheet, 2).Owner).toBe("Ishaan Verma");
      expect(rowByHeader(sheet, 3).Owner).toBe("Arjun Rao");
      expect(rowByHeader(sheet, 4).Owner).toBe("Ishaan Verma");
      expect(rowByHeader(sheet, 4)).toMatchObject({ "Project code": "PRJ-106", Currency: "USD" });

      const budget = sheet.getCell(2, position("budget"));
      expect(budget.value).toBe(2500000);
      expect(budget.numFmt).toBe("#,##0.00");
      expect(sheet.getCell(2, position("spent")).value).toBe(1120000);
      const start = sheet.getCell(2, position("start_date")).value;
      if (!(start instanceof Date)) throw new Error("The start date was not written as a date");
      expect(start.toISOString().slice(0, 10)).toBe("2026-03-02");
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
      expect(rows.get("Phase")?.[3]).toBe("Discovery, Design, Build, Stabilize, Launch, Live");
      expect(rows.get("Health")?.[3]).toBe("On track, At risk, Delayed");
      expect(rows.get("Owner")?.[3]).toBe('The "Owner" list on the Lists sheet');
      expect(rows.get("Budget")?.[2]).toBe(
        'Amount in major units (1499.50, not 149950); currency in the "Currency" column, at least 0',
      );
      expect(rows.get("Progress (%)")?.[2]).toBe("Whole number, from 0 to 100");
      for (const readOnly of ["Open tasks", "Milestones", "Record ID"]) expect(firstColumn).not.toContain(readOnly);
      for (const note of portfolioEntity.notes ?? []) expect(firstColumn, note).toContain(note);
    },
    TEST_TIMEOUT,
  );

  it(
    "lists this team's members, and offers dropdowns over them and over the phases and healths",
    async () => {
      const workbook = await downloadSample();
      const lists = sheetNamed(workbook, "Lists");
      expect(lists.getCell("A1").text).toBe("Owner");
      expect([lists.getCell("A2").text, lists.getCell("A3").text, lists.getCell("A4").text]).toEqual([
        "Ishaan Verma",
        "Arjun Rao",
        "",
      ]);

      const imports = sheetNamed(workbook, "Import");
      const validationOf = (key: string): ExcelJS.DataValidation | undefined =>
        imports.getCell(2, position(key)).dataValidation;
      expect(validationOf("owner")).toMatchObject({ type: "list", formulae: ['"Ishaan Verma,Arjun Rao"'] });
      expect(validationOf("phase")).toMatchObject({
        type: "list",
        formulae: ['"Discovery,Design,Build,Stabilize,Launch,Live"'],
      });
      expect(validationOf("health")).toMatchObject({ type: "list", formulae: ['"On track,At risk,Delayed"'] });
      for (const key of ["code", "name", "client", "progress_pct", "start_date", "budget", "currency", "summary"]) {
        expect(validationOf(key), key).toBeUndefined();
      }
    },
    TEST_TIMEOUT,
  );

  it(
    "is recognised as examples when it is chosen again unchanged, so nothing is imported",
    async () => {
      const gateway = stubGateway(SMALL_TEAM);
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const blob = downloads[0]?.blob;
      if (blob === undefined) throw new Error("Nothing was downloaded");

      await user.upload(fileInput(), new File([blob], "projects-sample.xlsx", { type: XLSX_TYPE }));
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
