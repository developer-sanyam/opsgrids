/**
 * Updating assets from Excel end to end, the way a person meets it: the real `useImportExport` hook
 * from `@opsgrids/client` behind the real `ImportExportButton` dialog from `@opsgrids/ui`, with only
 * the gateway stubbed. A workbook is built in memory, "Add new and update existing" is chosen, the
 * file is previewed row by row, imported, and the requests the gateway saw are read back.
 *
 * The stubbed gateway answers `PATCH /itsm/assets/{tag}` the way the service does: an empty body is
 * a 422, the tag in the body is a 422, a null on a required column is a 409 and on the cost
 * currency a 500. A run that sends any of those fails here instead of passing by luck.
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

import type { Asset } from "@/api/types";

import { assetsEntity } from "./assets";
import type { AssetFilters } from "./assets";

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

function asset(over: Partial<Asset>): Asset {
  return {
    tag: "LT-0000",
    name: "Something",
    type: "laptop",
    status: "in_use",
    owner: null,
    location: "",
    serial: "",
    purchased_at: null,
    warranty_expires_at: null,
    cost_minor: 0,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

/** The register, served two to a page whatever `size` the caller asks for. Text that looks like a formula is data here. */
const REGISTER: Asset[] = [
  asset({
    tag: "LT-0001",
    name: "=1+1",
    owner: "+91 98765 43210",
    location: "@SUM(A1:A9)",
    serial: "-2+3",
    purchased_at: "2025-08-14",
    cost_minor: 8450000,
    cost_currency: "INR",
  }),
  asset({ tag: "MN-0002", name: "Dell U2723QE monitor", type: "monitor", cost_minor: 1250, cost_currency: "KWD" }),
  asset({
    tag: "SW-0003",
    name: "Design licences",
    type: "license",
    status: "retired",
    owner: "IT Operations",
    cost_minor: 250000,
    cost_currency: "JPY",
  }),
  asset({ tag: "SRV-0004", name: "Rack server", type: "server", status: "in_stock", location: "Mumbai DC" }),
];
const PER_PAGE = 2;

/** What `AssetUpdate` accepts, and what it answers a null on. */
const PATCHABLE = new Set([
  "name",
  "type",
  "status",
  "owner",
  "location",
  "serial",
  "purchased_at",
  "warranty_expires_at",
  "cost_minor",
  "cost_currency",
]);
const NOT_NULLABLE = new Set(["name", "type", "status", "location", "serial", "cost_minor"]);

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

function patched(tag: string, body: Record<string, unknown>, refuse: Readonly<Record<string, Refusal>>): Response {
  const refusal = refuse[tag];
  if (refusal !== undefined) return problem(refusal.status, refusal.detail);
  const found = REGISTER.find((candidate) => candidate.tag === tag);
  if (found === undefined) return problem(404, "That asset is not part of this organization");
  const keys = Object.keys(body);
  if (keys.length === 0) return problem(422, "Provide at least one field to update");
  const unknown = keys.find((key) => !PATCHABLE.has(key));
  if (unknown !== undefined) return problem(422, `Extra inputs are not permitted (${unknown})`);
  for (const key of keys) {
    if (body[key] !== null) continue;
    if (key === "cost_currency") return problem(500, "Internal Server Error");
    if (NOT_NULLABLE.has(key)) return problem(409, "That conflicts with something that already exists");
  }
  return ok(200, { ...found, ...body });
}

/**
 * A gateway with the register, `PATCH /itsm/assets/{tag}` and `POST /itsm/assets`. A tag in
 * `refuse` answers with that refusal instead of saving.
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

      if (method === "GET" && url.pathname === "/api/v1/itsm/assets") {
        const page = Number(url.searchParams.get("page") ?? "1");
        const items = REGISTER.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: REGISTER.length, page, size: PER_PAGE, pages: Math.ceil(REGISTER.length / PER_PAGE) });
      }
      const target = /^\/api\/v1\/itsm\/assets\/([^/]+)$/.exec(url.pathname)?.[1];
      if (method === "PATCH" && target !== undefined && isRecord(body)) return patched(decodeURIComponent(target), body, refuse);
      if (method === "POST" && url.pathname === "/api/v1/itsm/assets" && isRecord(body)) {
        return ok(201, asset({ tag: String(body.tag), name: String(body.name) }));
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
    lists: () => requests.filter((request) => request.method === "GET"),
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: AssetFilters = { search: "", type: "all", status: "all" };

interface HarnessProps {
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ onImported }: HarnessProps) {
  const dx = useImportExport(assetsEntity, { filters: NO_FILTERS, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export assets" });
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

// ------------------------------------------------------------------------------------ workbooks

/** Every column the entity has, read-only ones included: a file exported earlier has them all. */
const FILE_COLUMNS = assetsEntity.columns;

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)));
  for (const row of rows) sheet.addRow(FILE_COLUMNS.map((c) => row[c.key] ?? null));
  for (const key of ["purchased_at", "warranty_expires_at"]) {
    sheet.getColumn(FILE_COLUMNS.findIndex((c) => c.key === key) + 1).numFmt = "yyyy-mm-dd";
  }
  return new File([await workbook.xlsx.writeBuffer()], "assets.xlsx", { type: XLSX_TYPE });
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

// ------------------------------------------------------------------------------------ updating

describe("updating assets from Excel", () => {
  it(
    "previews what each row will do and patches each asset by its tag with only the fields that changed",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          // 2: found by its tag; two fields differ.
          { tag: "LT-0001", status: "In repair", location: "Pune office" },
          // 3: every cell agrees with the record, the cost in the record's own currency, kwd in lower case.
          {
            tag: "MN-0002",
            name: "Dell U2723QE monitor",
            type: "Monitor",
            status: "In use",
            cost: 1.25,
            cost_currency: "kwd",
          },
          // 4: a cost with no currency: read in yen, the currency of the record, not in rupees.
          { tag: "SW-0003", cost: 300000 },
          // 5: nobody has this tag, so it is added.
          { tag: "NW-0100", name: "Core switch", type: "Network" },
          // 6: the sample sheet's first example, untouched.
          { ...(assetsEntity.sampleRows[0] ?? {}) },
          // 7: dates, one a real Excel date, and an owner.
          {
            tag: "SRV-0004",
            owner: "Data centre team",
            purchased_at: new Date(Date.UTC(2025, 10, 3)),
            warranty_expires_at: "2028-11-02",
          },
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
      expect(within(previewRow(2)).getByText("Changes: Status, Location")).toBeTruthy();
      expect(within(previewRow(3)).getByText("No change")).toBeTruthy();
      expect(within(previewRow(4)).getByText("Changes: Cost")).toBeTruthy();
      expect(within(previewRow(5)).getByText("New")).toBeTruthy();
      expect(within(previewRow(6)).getByText("Sample")).toBeTruthy();
      expect(within(previewRow(7)).getByText("Changes: Owner, Purchase date, Warranty expires")).toBeTruthy();
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Update 3, add 1");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/itsm/assets/LT-0001", body: { status: "in_repair", location: "Pune office" } },
        { method: "PATCH", path: "/api/v1/itsm/assets/SW-0003", body: { cost_minor: 300000 } },
        { method: "POST", path: "/api/v1/itsm/assets", body: { tag: "NW-0100", name: "Core switch", type: "network" } },
        {
          method: "PATCH",
          path: "/api/v1/itsm/assets/SRV-0004",
          body: { owner: "Data centre team", purchased_at: "2025-11-03", warranty_expires_at: "2028-11-02" },
        },
      ]);
      // The tag is the URL's and never the body's, and no request has an empty body.
      for (const write of gateway.writes().filter((request) => request.method === "PATCH")) {
        expect(write.body).not.toHaveProperty("tag");
        expect(Object.keys(write.body as object).length).toBeGreaterThan(0);
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
    "accepts a sheet of only the asset tag and one column, and checks the same file again when the mode changes",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();

      await reviewFor(await partialFile(["Asset tag", "Owner"], [["MN-0002", "Data centre team"]]), user);
      expect(screen.getByText("Your file is missing required columns: Name, Type.")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Import 0 rows" }) as HTMLButtonElement).disabled).toBe(true);

      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      expect(screen.queryByText(/missing required columns/)).toBeNull();
      expect(within(previewRow(2)).getByText("Changes: Owner")).toBeTruthy();

      await user.click(screen.getByRole("radio", { name: ADD_MODE }));
      await waitFor(() => expect(screen.getByText("Your file is missing required columns: Name, Type.")).toBeTruthy());
      expect(gateway.writes()).toEqual([]);

      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await waitFor(() => expect(figure("Updates")).toBe("1"));
      await finishImport(user, "Update 1");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/itsm/assets/MN-0002", body: { owner: "Data centre team" } },
      ]);
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
          // 2, 3, 4: fields that cannot be emptied.
          { tag: "LT-0001", name: "[clear]" },
          { tag: "MN-0002", cost: "[clear]" },
          { tag: "SW-0003", cost_currency: "[clear]" },
          // 5: a currency change with no cost: the amount would be relabelled, not converted.
          { tag: "SRV-0004", cost_currency: "USD" },
          // 6: a type that does not exist.
          { tag: "LT-0001", type: "Toaster" },
          // 7: fine.
          { tag: "LT-0001", status: "Retired" },
          // 8: the same asset as row 7.
          { tag: "LT-0001", owner: "Someone else" },
          // 9: a fraction of a yen: read in the record's own currency, which has no decimals.
          { tag: "SW-0003", cost: "1.5" },
        ]),
        user,
      );

      expect(figure("Rows in file")).toBe("8");
      expect(figure("Updates")).toBe("1");
      expect(figure("Need fixing")).toBe("7");
      for (const refusal of [
        "Name cannot be cleared",
        "Cost cannot be cleared",
        "Cost currency cannot be cleared",
        "Cost currency would change from INR to USD, but changing it does not convert the cost. Give Cost in USD too, or leave Cost currency blank.",
        "'Toaster' is not one of the allowed values for Type: Laptop, Desktop, Server, Monitor, Phone, Network, License",
        "This row updates the same record as row 7. Keep only one of the two rows.",
        "Cost must be a whole number for JPY, which has no decimal places",
      ]) {
        expect(screen.getByText(refusal), refusal).toBeTruthy();
      }
      expect(gateway.writes()).toEqual([]);

      await finishImport(user, "Update 1");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/itsm/assets/LT-0001", body: { status: "retired" } },
      ]);
      expect(screen.getByText("1 updated, 7 failed.")).toBeTruthy();
      expect(screen.getByText("7 rows were not imported. The result report lists every row.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "empties a field with [clear] using the value its endpoint accepts: null for owner and dates, an empty string for text",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: everything the asset has that can be emptied; the warranty date is already empty.
          {
            tag: "LT-0001",
            owner: "[clear]",
            location: "[clear]",
            serial: "[clear]",
            purchased_at: "[clear]",
            warranty_expires_at: "[clear]",
          },
          // 3: nothing there to clear.
          { tag: "SRV-0004", owner: "[clear]", serial: "[clear]" },
        ]),
        user,
      );
      expect(within(previewRow(2)).getByText("Changes: Owner, Location, Serial number, Purchase date")).toBeTruthy();
      expect(within(previewRow(3)).getByText("No change")).toBeTruthy();

      await finishImport(user, "Update 1");

      // The stubbed service answers a null on location or serial with a 409, so passing proves the
      // two text columns were cleared with an empty string.
      expect(gateway.writes()).toEqual([
        {
          method: "PATCH",
          path: "/api/v1/itsm/assets/LT-0001",
          body: { owner: null, location: "", serial: "", purchased_at: null },
        },
      ]);
      expect(screen.getByText("Everything imported. 1 updated, 1 unchanged.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "reads a cost in the record's own currency when the currency cell is blank, and never re-labels it",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(
        await importFile([
          // 2: dinars have three decimals: 1.5 is 1500 fils, not 150.
          { tag: "MN-0002", cost: 1.5 },
          // 3: yen have none: 300000 is 300000, not 30000000.
          { tag: "SW-0003", cost: 300000 },
          // 4: a currency change that restates the cost: both are sent together.
          { tag: "LT-0001", cost: 1200, cost_currency: "usd" },
          // 5: rupees, the record's own currency.
          { tag: "SRV-0004", cost: 500 },
        ]),
        user,
      );
      await finishImport(user, "Update 4");

      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/itsm/assets/MN-0002", body: { cost_minor: 1500 } },
        { method: "PATCH", path: "/api/v1/itsm/assets/SW-0003", body: { cost_minor: 300000 } },
        { method: "PATCH", path: "/api/v1/itsm/assets/LT-0001", body: { cost_minor: 120000, cost_currency: "USD" } },
        { method: "PATCH", path: "/api/v1/itsm/assets/SRV-0004", body: { cost_minor: 50000 } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "reports a 409, a 404 and a 422 from the server as failed rows with its own words, never as skipped",
    async () => {
      const gateway = stubGateway({
        "MN-0002": { status: 409, detail: "That conflicts with something that already exists" },
        "SW-0003": { status: 404, detail: "That asset is not part of this organization" },
        "SRV-0004": { status: 422, detail: "The warranty cannot end before the purchase date." },
      });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          { tag: "LT-0001", status: "Retired" },
          { tag: "MN-0002", status: "Retired" },
          { tag: "SW-0003", status: "In use" },
          { tag: "SRV-0004", status: "In use" },
        ]),
        user,
      );
      await finishImport(user, "Update 4");

      expect(gateway.writes()).toHaveLength(4);
      expect(screen.getByText("1 updated, 3 failed.")).toBeTruthy();
      expect(figure("Updated")).toBe("1");
      expect(figure("Failed")).toBe("3");
      expect(figure("Skipped, already exist")).toBeUndefined();
      const table = within(screen.getByRole("table", { name: "Rows that were not imported" }));
      for (const message of [
        "That conflicts with something that already exists",
        "That asset is not part of this organization",
        "The warranty cannot end before the purchase date.",
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
      stubGateway({ "MN-0002": { status: 409, detail: "That conflicts with something that already exists" } });
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(await importFile([{ tag: "MN-0002", status: "Retired" }]), user);
      await finishImport(user, "Update 1");

      expect(figure("Failed")).toBe("1");
      expect(figure("Updated")).toBe("0");
      expect(onImported).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it(
    "says there is nothing to change when every row already matches, and sends nothing: an empty body would be refused",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openUpdateDialog(onImported);

      await reviewFor(
        await importFile([
          { tag: "SRV-0004", name: "Rack server", type: "Server", status: "In stock", location: "Mumbai DC" },
          { tag: "sw-0003" },
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
    "warns when the file has no asset tag column, since nothing else can name an asset, and adds every row as new",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();

      await reviewFor(await partialFile(["Name", "Owner"], [["Rack server", "Data centre team"]]), user);

      expect(
        screen.getByText("This file has no Asset tag column, so it cannot update existing records; every row would be added as new."),
      ).toBeTruthy();
      expect(figure("Updates")).toBe("0");
      expect(figure("Need fixing")).toBe("1");
      expect(gateway.writes()).toEqual([]);
    },
    TEST_TIMEOUT,
  );

  it(
    "aborts the request in flight when the screen is left, instead of reading the register on",
    async () => {
      const listSignals: AbortSignal[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((input: string, init?: RequestInit) => {
          const url = new URL(input, "http://gateway.test");
          const given = init?.signal;
          if (url.pathname === "/api/v1/itsm/assets" && given instanceof AbortSignal) {
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
      await screen.findByRole("dialog", { name: "Import / Export assets" });
      await user.click(screen.getByRole("radio", { name: UPDATE_MODE }));
      await user.upload(fileInput(), await importFile([{ tag: "LT-0001", status: "In repair" }]));

      await waitFor(() => expect(listSignals.length).toBeGreaterThan(0));
      expect(listSignals.some((given) => given.aborted)).toBe(false);

      view.unmount();
      expect(listSignals.every((given) => given.aborted)).toBe(true);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("updating assets from an exported file", () => {
  async function exportedBlob(user: User): Promise<Blob> {
    await user.click(screen.getByRole("button", { name: "Export to Excel" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    const blob = downloads[0]?.blob;
    if (blob === undefined) throw new Error("Nothing was downloaded");
    return blob;
  }

  it(
    "finds every asset by its tag and changes nothing when the export is sent back as it was",
    async () => {
      const gateway = stubGateway();
      const user = await openUpdateDialog();
      const blob = await exportedBlob(user);

      await reviewFor(new File([blob], "assets-export.xlsx", { type: XLSX_TYPE }), user);

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
      sheet.getCell(3, headers.indexOf("Status") + 1).value = "Retired";
      sheet.getCell(3, headers.indexOf("Location") + 1).value = "Recycling";
      const edited = new File([await workbook.xlsx.writeBuffer()], "assets-edited.xlsx", { type: XLSX_TYPE });

      await reviewFor(edited, user);
      expect(figure("Updates")).toBe("1");
      expect(figure("No change")).toBe("3");
      expect(within(previewRow(3)).getByText("Changes: Status, Location")).toBeTruthy();

      await finishImport(user, "Update 1");
      expect(gateway.writes()).toEqual([
        { method: "PATCH", path: "/api/v1/itsm/assets/MN-0002", body: { status: "retired", location: "Recycling" } },
      ]);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ instructions

describe("the assets Instructions sheet", () => {
  it(
    "explains updating existing assets, from the engine's rules and the entity's own",
    async () => {
      stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const sheet = sheetNamed(await readDownload(downloads[0]), "Instructions");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push(row.getCell(1).text));

      const start = lines.indexOf("Updating existing assets");
      const end = lines.indexOf("General rules");
      expect(start).toBeGreaterThan(-1);
      expect(end).toBeGreaterThan(start);
      // What an update ignores: the read-only columns an export fills and an import never reads.
      const ignored = assetsEntity.columns.filter((column) => column.readOnly === true).map((column) => column.header);
      expect(lines.slice(start + 1, end).filter((line) => line !== "")).toEqual([
        'Choose "Add new and update existing" in the import dialog to use these rules.',
        "A row is matched to an existing record by its Asset tag in its exact spelling (capitals and spaces count; a Asset tag that differs from an existing one only in capitals or spaces is refused, not matched). A row that matches nothing is added as a new record. If two records have the same Asset tag, the row is refused.",
        "A blank cell leaves the field alone. Only the cells you fill in are compared with the record, and only the fields that differ are changed.",
        "Type [clear] in a cell to empty a field. Only some fields can be emptied.",
        "Can be emptied with [clear]: Owner, Location, Serial number, Purchase date, Warranty expires.",
        "Cannot be changed once created: Asset tag. A row whose cell differs from the record is refused.",
        `Ignored when updating: ${ignored.join(", ")}.`,
        "Warning: updating overwrites live data and cannot be undone. In a file exported from this list, cells you did not change since the export are left alone, so someone else's later edit to them stays. Nothing detects that someone else changed a cell you did change after your export: your value replaces theirs. Check the preview before you import.",
      ]);
      for (const note of (assetsEntity.notes ?? []).filter((text) => text.startsWith("Updating:"))) {
        expect(lines, note).toContain(note);
      }
      expect(lines).toContain(
        'With "Add new records only", existing assets are skipped, never overwritten. Importing the same file again is safe.',
      );
    },
    TEST_TIMEOUT,
  );
});
