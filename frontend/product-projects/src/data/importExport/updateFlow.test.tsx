/**
 * Updating the project portfolio from Excel end to end, the way a person meets it: the real
 * `useImportExport` hook from `@opsgrids/client` behind the real `ImportExportButton` dialog from
 * `@opsgrids/ui`, with only the gateway stubbed. A workbook is built in memory, "Add new and update
 * existing" is chosen, the file is previewed row by row, imported, and the requests the gateway saw
 * are read back.
 *
 * The stubbed gateway answers `PATCH /projects-workspace/projects/{code}` the way the service does,
 * and stricter where the service is silent: a key it would drop without a word (the code, the open
 * task count) is a 422 here so that sending one fails the run, a null on a required column is a 409
 * and on a date a 500, and the date order is judged after merging with the stored dates.
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

interface Refusal {
  status: number;
  detail: string;
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
  project({ id: "proj-4", code: "PRJ-107", name: "Warehouse scanners", summary: "Line one\nLine two" }),
];
const PER_PAGE = 2;

/** What `ProjectUpdate` declares. A key outside it is one the service drops without a word. */
const PATCHABLE = new Set([
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
]);

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

function patched(code: string, body: Record<string, unknown>, refuse: Readonly<Record<string, Refusal>>): Response {
  const refusal = refuse[code];
  if (refusal !== undefined) return problem(refusal.status, refusal.detail);
  const found = PORTFOLIO.find((candidate) => candidate.code === code);
  if (found === undefined) return problem(404, "That project is not part of this organization");
  const dropped = Object.keys(body).find((key) => !PATCHABLE.has(key));
  if (dropped !== undefined) return problem(422, `The service would drop "${dropped}" and answer 200`);
  for (const [key, value] of Object.entries(body)) {
    if (value !== null || key === "owner_ref") continue;
    return key === "start_date" || key === "target_date"
      ? problem(500, "Internal Server Error")
      : problem(409, "That conflicts with something that already exists");
  }
  const owner = body.owner_ref;
  if (typeof owner === "string" && !TEAM.some((candidate) => candidate.id === owner)) {
    return problem(404, `No member with ref '${owner}' in this organization`);
  }
  const start = typeof body.start_date === "string" ? body.start_date : found.start_date;
  const target = typeof body.target_date === "string" ? body.target_date : found.target_date;
  if (target < start) return problem(422, "target_date must be on or after start_date");
  return ok(200, { ...found, ...body });
}

/**
 * A gateway with the team, the portfolio, `PATCH /projects-workspace/projects/{code}` and
 * `POST /projects-workspace/projects`. A code in `refuse` answers with that refusal instead of saving.
 */
function stubGateway(refuse: Readonly<Record<string, Refusal>> = {}) {
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
        const items = TEAM.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: TEAM.length, page, size: PER_PAGE, pages: Math.ceil(TEAM.length / PER_PAGE) });
      }
      if (method === "GET" && url.pathname === "/api/v1/projects-workspace/projects") {
        const items = PORTFOLIO.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: PORTFOLIO.length, page, size: PER_PAGE, pages: Math.ceil(PORTFOLIO.length / PER_PAGE) });
      }
      const target = /^\/api\/v1\/projects-workspace\/projects\/([^/]+)$/.exec(url.pathname)?.[1];
      if (method === "PATCH" && target !== undefined && isRecord(body)) return patched(decodeURIComponent(target), body, refuse);
      if (method === "POST" && url.pathname === "/api/v1/projects-workspace/projects" && isRecord(body)) {
        return ok(201, project({ id: `new-${String(body.code)}`, code: String(body.code), name: String(body.name) }));
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
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: PortfolioFilters = { q: "", health: "all", phase: "all", sort: "code" };

interface HarnessProps {
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ onImported }: HarnessProps) {
  const dx = useImportExport(portfolioEntity, { filters: NO_FILTERS, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export projects" });
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

type User = ReturnType<typeof userEvent.setup>;

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

async function reviewFor(file: File, user: User): Promise<void> {
  await user.upload(fileInput(), file);
  await screen.findByRole("dialog", { name: "Review your file" });
}

async function finishImport(user: User, buttonName: string): Promise<void> {
  await user.click(screen.getByRole("button", { name: buttonName }));
  await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });
}

function ownerHintUpdate(): string {
  const hint = portfolioEntity.columns.find((column) => column.key === "owner")?.lookupHintUpdate;
  if (hint === undefined) throw new Error("The Owner column has no lookupHintUpdate");
  return hint;
}

// ------------------------------------------------------------------------------------ workbooks

/** Every column the entity has, read-only ones included: a file exported earlier has them all. */
const FILE_COLUMNS = portfolioEntity.columns;
const DATE_KEYS = ["start_date", "target_date"];

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)));
  for (const row of rows) sheet.addRow(FILE_COLUMNS.map((c) => row[c.key] ?? null));
  for (const key of DATE_KEYS) sheet.getColumn(FILE_COLUMNS.findIndex((c) => c.key === key) + 1).numFmt = "yyyy-mm-dd";
  return new File([await workbook.xlsx.writeBuffer()], "projects.xlsx", { type: XLSX_TYPE });
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

const PATCH = "/api/v1/projects-workspace/projects";

// ------------------------------------------------------------------------------------ updating

describe("updating projects from Excel", () => {
  it(
    "previews what each row will do and patches each project by its code with only the fields that changed",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          // 2: found by its code in any case; the team size is what it already is.
          { code: "prj-104", health: "On track", progress_pct: 50, team_size: 6 },
          // 3: found by Record ID; every cell agrees, the budget read in dinars with the currency blank.
          { id: "proj-2", name: "Kiosk pilot", budget: 12.345 },
          // 4: a budget with no currency is read in yen, the project's own, not in rupees; the owner by name.
          { code: "PRJ-106", owner: "meera iyer", budget: 1600000 },
          // 5: nobody has this code, so it is added.
          { code: "PRJ-201", name: "Ledger migration", start_date: "2026-03-02", target_date: "2026-09-30" },
          // 6: the sample sheet's first example, untouched.
          { ...(portfolioEntity.sampleRows[0] ?? {}) },
          // 7: a summary that runs over two lines and is what it was; only the start date differs.
          { code: "PRJ-107", start_date: "2026-01-12", summary: "Line one\nLine two" },
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
      expect(within(previewRow(2)).getByText("Update")).toBeTruthy();
      expect(within(previewRow(2)).getByText("Changes: Health, Progress (%)")).toBeTruthy();
      expect(within(previewRow(3)).getByText("No change")).toBeTruthy();
      expect(within(previewRow(4)).getByText("Changes: Owner, Budget")).toBeTruthy();
      expect(within(previewRow(5)).getByText("New")).toBeTruthy();
      expect(within(previewRow(6)).getByText("Sample")).toBeTruthy();
      expect(within(previewRow(7)).getByText("Changes: Start date")).toBeTruthy();
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Import 4 rows");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: `${PATCH}/PRJ-104`, body: { health: "on_track", progress_pct: 50 } },
        { method: "PATCH", path: `${PATCH}/PRJ-106`, body: { owner_ref: "p-01", budget_minor: 1600000 } },
        {
          method: "POST",
          path: PATCH,
          body: { code: "PRJ-201", name: "Ledger migration", start_date: "2026-03-02", target_date: "2026-09-30" },
        },
        { method: "PATCH", path: `${PATCH}/PRJ-107`, body: { start_date: "2026-01-12" } },
      ]);
      // The code is the URL's and never the body's, and the system's figures never travel.
      for (const write of gateway.writes().filter((request) => request.method === "PATCH")) {
        for (const key of ["code", "id", "open_tasks", "milestone_count"]) expect(write.body).not.toHaveProperty(key);
      }

      expect(screen.getByText("Everything imported. 3 updated, 1 created, 1 unchanged.")).toBeTruthy();
      expect(figure("Created")).toBe("1");
      expect(figure("Updated")).toBe("3");
      expect(figure("Unchanged")).toBe("1");
      expect(figure("Failed")).toBe("0");
      expect(figure("Skipped, already exist")).toBeUndefined();
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
    "accepts a sheet of only the project code and one column, and checks the same file again when the mode changes",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();

      await reviewFor(await partialFile(["Project code", "Health"], [["PRJ-107", "Delayed"]]), user);
      expect(screen.getByText("Your file is missing required columns: Name, Start date, Target date.")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Import 0 rows" }) as HTMLButtonElement).disabled).toBe(true);

      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      expect(screen.queryByText(/missing required columns/)).toBeNull();
      expect(within(previewRow(2)).getByText("Changes: Health")).toBeTruthy();

      await user.click(screen.getByRole("radio", { name: ADD_MODE }));
      await waitFor(() =>
        expect(screen.getByText("Your file is missing required columns: Name, Start date, Target date.")).toBeTruthy(),
      );
      expect(gateway.writes()).toEqual([]);

      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      await finishImport(user, "Import 1 row");
      expect(gateway.writes()).toEqual([{ method: "PATCH", path: `${PATCH}/PRJ-107`, body: { health: "delayed" } }]);
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
          { id: "proj-404", name: "Ghost" },
          // 3: a Record ID with a different code: the code cannot change.
          { id: "proj-2", code: "PRJ-999" },
          // 4, 5: fields that cannot be emptied.
          { code: "PRJ-104", name: "[clear]" },
          { code: "PRJ-104", start_date: "[clear]" },
          // 6, 7: a date that puts the target before the start, once merged with the stored one.
          { code: "PRJ-105", target_date: "2025-12-31" },
          { code: "PRJ-105", start_date: "2026-07-01" },
          // 8, 9: a currency change that leaves an amount unstated: the server would relabel it.
          { code: "PRJ-104", currency: "USD" },
          { code: "PRJ-106", currency: "USD", budget: 1600 },
          // 10, 11: an owner nobody has, and one two members share.
          { code: "PRJ-107", owner: "Nobody Here" },
          { code: "PRJ-107", owner: "Sam Patel" },
          // 12: fine.
          { code: "PRJ-107", health: "Delayed" },
          // 13: a team size past what the column holds.
          { code: "PRJ-104", team_size: 3000000000 },
          // 14: the same project as row 12.
          { code: "prj-107", progress_pct: 10 },
        ]),
        user,
      );

      expect(figure("Rows in file")).toBe("13");
      expect(figure("Updates")).toBe("1");
      expect(figure("Need fixing")).toBe("12");
      for (const refusal of [
        "That Record ID is not in this list: the record may have been deleted, or the file came from another organisation.",
        "Project code cannot be changed once the record exists (it is 'PRJ-105'). Leave the cell as it is, or blank.",
        "Name cannot be cleared",
        "Start date cannot be cleared",
        "The target date cannot be before the start date. With this row the project would start on 2026-01-05 and be due on 2025-12-31.",
        "The target date cannot be before the start date. With this row the project would start on 2026-07-01 and be due on 2026-06-30.",
        "Currency would change from INR to USD, but changing it does not convert amounts. Give Budget and Spent in USD too, or leave Currency blank.",
        "Currency would change from JPY to USD, but changing it does not convert amounts. Give Spent in USD too, or leave Currency blank.",
        `'Nobody Here' is not one of the accepted values for Owner (see the Lists sheet). ${ownerHintUpdate()}`,
        `'Sam Patel' matches more than one of the accepted values for Owner, so it cannot be chosen. ${ownerHintUpdate()}`,
        "Team size must be between 0 and 2147483647",
        "This row updates the same record as row 12. Keep only one of the two rows.",
      ]) {
        expect(screen.getByText(refusal), refusal).toBeTruthy();
      }
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Import 1 row");

      expect(gateway.writes()).toEqual([{ method: "PATCH", path: `${PATCH}/PRJ-107`, body: { health: "delayed" } }]);
      expect(screen.getByText("1 updated, 12 failed.")).toBeTruthy();
      expect(screen.getByText("12 rows were not imported. The result report lists every row.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "empties a field with [clear] using the value its endpoint accepts: null for the owner, an empty string for text",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: everything the project has that can be emptied.
          { code: "PRJ-104", client: "[clear]", owner: "[clear]", summary: "[clear]" },
          // 3: nothing there to clear.
          { code: "PRJ-107", client: "[clear]", owner: "[clear]" },
        ]),
        user,
      );
      expect(within(previewRow(2)).getByText("Changes: Client, Owner, Summary")).toBeTruthy();
      expect(within(previewRow(3)).getByText("No change")).toBeTruthy();

      await finishImport(user, "Import 1 row");

      // The stubbed service answers a null on client or summary with a 409, so passing proves the
      // two text columns were cleared with an empty string.
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: `${PATCH}/PRJ-104`, body: { client: "", owner_ref: null, summary: "" } },
      ]);
      expect(screen.getByText("Everything imported. 1 updated, 1 unchanged.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "reads amounts in the project's own currency when the currency cell is blank, and never re-labels them",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: dinars have three decimals: 12.5 is 12500 fils, not 1250.
          { code: "PRJ-105", budget: 12.5 },
          // 3: yen have none: the amounts are what they say.
          { code: "PRJ-106", budget: 1600000, spent: 300000 },
          // 4: a currency change that restates both amounts: all three are sent together.
          { code: "PRJ-104", currency: "usd", budget: 3000000, spent: 1350000 },
          // 5: a currency change on a project with nothing entered: a zero has no unit to lose.
          { code: "PRJ-107", currency: "eur", budget: 500 },
        ]),
        user,
      );
      await finishImport(user, "Import 4 rows");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: `${PATCH}/PRJ-105`, body: { budget_minor: 12500 } },
        { method: "PATCH", path: `${PATCH}/PRJ-106`, body: { budget_minor: 1600000, spent_minor: 300000 } },
        {
          method: "PATCH",
          path: `${PATCH}/PRJ-104`,
          body: { budget_minor: 300000000, spent_minor: 135000000, currency: "USD" },
        },
        { method: "PATCH", path: `${PATCH}/PRJ-107`, body: { budget_minor: 50000, currency: "EUR" } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "accepts two changed dates that are in order together, and one that is in order against the stored other",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          { code: "PRJ-105", start_date: "2027-01-04", target_date: "2027-06-30" },
          { code: "PRJ-106", target_date: "2026-12-31" },
        ]),
        user,
      );
      await finishImport(user, "Import 2 rows");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: `${PATCH}/PRJ-105`, body: { start_date: "2027-01-04", target_date: "2027-06-30" } },
        { method: "PATCH", path: `${PATCH}/PRJ-106`, body: { target_date: "2026-12-31" } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "reports a 409, a 404 and a 422 from the server as failed rows with its own words, never as skipped",
    async () => {
      const gateway = stubGateway({
        "PRJ-105": { status: 409, detail: "That conflicts with something that already exists" },
        "PRJ-106": { status: 404, detail: "That project is not part of this organization" },
        "PRJ-107": { status: 422, detail: "target_date must be on or after start_date" },
      });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          { code: "PRJ-104", health: "Delayed" },
          { code: "PRJ-105", health: "Delayed" },
          { code: "PRJ-106", health: "Delayed" },
          { code: "PRJ-107", health: "Delayed" },
        ]),
        user,
      );
      await finishImport(user, "Import 4 rows");

      expect(gateway.writes()).toHaveLength(4);
      expect(screen.getByText("1 updated, 3 failed.")).toBeTruthy();
      expect(figure("Updated")).toBe("1");
      expect(figure("Failed")).toBe("3");
      expect(figure("Skipped, already exist")).toBeUndefined();
      const table = within(screen.getByRole("table", { name: "Rows that were not imported" }));
      for (const message of [
        "That conflicts with something that already exists",
        "That project is not part of this organization",
        "target_date must be on or after start_date",
      ]) {
        expect(table.getByText(message), message).toBeTruthy();
      }
      expect(table.getAllByText("Failed")).toHaveLength(3);
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ updated: 1, failed: 3 }));
    },
    TEST_TIMEOUT,
  );

  it(
    "does not ask the screen to reload when every update failed",
    async () => {
      stubGateway({ "PRJ-105": { status: 409, detail: "That conflicts with something that already exists" } });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(await importFile([{ code: "PRJ-105", health: "Delayed" }]), user);
      await finishImport(user, "Import 1 row");

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
          { code: "PRJ-106", owner: "Arjun Rao", budget: 1500000, spent: 250000, currency: "jpy" },
          { id: "proj-4", summary: "Line one\nLine two" },
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
    "warns when the file has neither a Record ID nor a project code column, and adds every row as new",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(await partialFile(["Name", "Health"], [["Kiosk pilot", "Delayed"]]), user);

      expect(
        screen.getByText(
          "This file has no Record ID or Project code column, so it cannot update existing records; every row would be added as new.",
        ),
      ).toBeTruthy();
      expect(figure("Updates")).toBe("0");
      expect(figure("Need fixing")).toBe("1");
      expect(gateway.writes()).toEqual([]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("updating projects from an exported file", () => {
  async function exportedBlob(user: User): Promise<Blob> {
    await user.click(screen.getByRole("button", { name: "Export to Excel" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    const blob = downloads[0]?.blob;
    if (blob === undefined) throw new Error("Nothing was downloaded");
    return blob;
  }

  it(
    "finds every project by its Record ID and changes nothing when the export is sent back as it was",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();
      const blob = await exportedBlob(user);

      await reviewFor(new File([blob], "projects-export.xlsx", { type: XLSX_TYPE }), user);

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
    "updates only the cells that were edited in the exported file",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();
      const blob = await exportedBlob(user);

      const workbook = new ExcelJS.Workbook();
      await workbook.xlsx.load(await blob.arrayBuffer());
      const sheet = sheetNamed(workbook, "Export");
      const headers: string[] = [];
      sheet.getRow(1).eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      sheet.getCell(2, headers.indexOf("Health") + 1).value = "Delayed";
      sheet.getCell(2, headers.indexOf("Progress (%)") + 1).value = 60;
      const edited = new File([await workbook.xlsx.writeBuffer()], "projects-edited.xlsx", { type: XLSX_TYPE });

      await reviewFor(edited, user);
      expect(figure("Updates")).toBe("1");
      expect(figure("No change")).toBe("3");
      expect(within(previewRow(2)).getByText("Changes: Health, Progress (%)")).toBeTruthy();

      await finishImport(user, "Import 1 row");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: `${PATCH}/PRJ-104`, body: { health: "delayed", progress_pct: 60 } },
      ]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ instructions

describe("the projects Instructions sheet", () => {
  it(
    "explains updating existing projects, from the engine's rules and the entity's own",
    async () => {
      stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const sheet = sheetNamed(await readDownload(downloads[0]), "Instructions");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push(row.getCell(1).text));

      const start = lines.indexOf("Updating existing projects");
      const end = lines.indexOf("General rules");
      expect(start).toBeGreaterThan(-1);
      expect(end).toBeGreaterThan(start);
      expect(lines.slice(start + 1, end).filter((line) => line !== "")).toEqual([
        'Choose "Add new and update existing" in the import dialog to use these rules.',
        "A row is matched to an existing record by its Record ID (the column an export writes). A row without one is matched by Project code, ignoring capitals and extra spaces. A row that matches nothing is added as a new record. If two records have the same Project code, the row is refused.",
        "A blank cell leaves the field alone. Only the cells you fill in are compared with the record, and only the fields that differ are changed.",
        "Type [clear] in a cell to empty a field. Only some fields can be emptied.",
        "Can be emptied with [clear]: Client, Owner, Summary.",
        "Cannot be changed once created: Project code. A row whose cell differs from the record is refused.",
        "Warning: updating overwrites live data and cannot be undone. Nothing detects an edit that someone else makes after your file is read: it is overwritten by what the file says. Check the preview before you import.",
      ]);
      for (const note of (portfolioEntity.notes ?? []).filter((text) => text.startsWith("Updating:"))) {
        expect(lines, note).toContain(note);
      }
      expect(lines).toContain(
        'With "Add new records only", existing projects are skipped, never overwritten. Importing the same file again is safe.',
      );
    },
    TEST_TIMEOUT,
  );
});
