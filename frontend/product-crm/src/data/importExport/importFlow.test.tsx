/**
 * The path a person takes through Import / Export on the CRM's four lists, with nothing between
 * them and the wire but a fake gateway: the real `useImportExport` from `@opsgrids/client`, the
 * real `ImportExportButton` dialog from `@opsgrids/ui`, this app's real entity definitions and api
 * client. A workbook is built in memory the way Excel would save it, chosen in the dialog, read,
 * previewed and sent row by row; the result and the exported and sample workbooks are read back
 * with exceljs.
 *
 * The runner spaces its requests 350 ms apart (the gateway's rate limit), so a run of a few rows
 * takes a second or two of real time. Real timers are used on purpose: faking them would skip the
 * very pacing the dialog reports progress against.
 */
import { StrictMode } from "react";
import type { ReactElement } from "react";
import ExcelJS from "exceljs";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider, useImportExport } from "@opsgrids/client";
import type { ImportEntity, ImportSummary, SheetValue } from "@opsgrids/client";
import { ImportExportButton } from "@opsgrids/ui";

import { DirectoryContext, EMPTY_DIRECTORY } from "@/data/directoryContext";
import { getMySubjectRef } from "@/lib/subject";
import { CompaniesPage } from "@/routes/Companies";
import { ContactsPage } from "@/routes/Contacts";
import { DealsPage } from "@/routes/Deals";
import { LeadsPage } from "@/routes/Leads";
import { companyImport } from "./companies";
import type { CompanyFilters } from "./companies";
import { contactImport } from "./contacts";
import type { ContactFilters } from "./contacts";
import { dealImport } from "./deals";
import type { DealFilters } from "./deals";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
  dealWire,
  leadWire,
  pageOf,
  present,
  stubApi,
} from "./entityTestKit";
import type { Recorded, Reply } from "./entityTestKit";
import { XLSX_TYPE, captureDownloads, readBlob, sheetFile, sheetOf, validationAt, valuesOf } from "./importFlowKit";
import type { Download } from "./importFlowKit";
import { leadImport } from "./leads";
import type { LeadFilters } from "./leads";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

/* ------------------------------ the page under test ------------------------------ */

interface HarnessProps<Payload, Item, Filters> {
  entity: ImportEntity<Payload, Item, Filters>;
  filters: Filters;
  onImported: (summary: ImportSummary) => void;
}

/** What a list screen does: the hook for its entity, handed to the button, with Import and Export
 *  both offered (whether they are is the screen's own test, in `screens.test.tsx`). */
function Screen<Payload, Item, Filters>({ entity, filters, onImported }: HarnessProps<Payload, Item, Filters>) {
  const dx = useImportExport(entity, { filters, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

/** The app mounts everything in StrictMode (`main.tsx`), which renders twice and runs every effect,
 *  its cleanup and the effect again: the hook has to come through that with its state intact. */
function Harness<Payload, Item, Filters>(props: HarnessProps<Payload, Item, Filters>) {
  return (
    <StrictMode>
      <Screen {...props} />
    </StrictMode>
  );
}

type User = ReturnType<typeof userEvent.setup>;

let downloads: Download[];
let releaseDownloads: () => void;
const onImported = vi.fn<(summary: ImportSummary) => void>();

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
  ({ downloads, release: releaseDownloads } = captureDownloads());
  onImported.mockReset();
});

afterEach(() => {
  releaseDownloads();
  vi.unstubAllGlobals();
});

/* ------------------------------- the fake gateway ------------------------------- */

const API = "/api/v1";

/** The companies and contacts the sample rows are written for. */
const COMPANIES = [companyWire("comp-1", "Northwind Logistics"), companyWire("comp-2", "Cobalt Financial")];
const CONTACTS = [
  contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
  contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
];

type Answer = (body: Record<string, unknown>) => Reply;

interface Scenario {
  /** What each list endpoint returns, by path. The lookups read these too. */
  lists?: Record<string, readonly unknown[]>;
  /** The answer to a create, by path and by the JSON body that was sent. */
  creates?: Record<string, Answer>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A small organisation behind the gateway. A request nobody scripted fails the test. */
function stubOrganisation(scenario: Scenario = {}): Recorded[] {
  const lists: Record<string, readonly unknown[]> = {
    [`${API}/customers`]: COMPANIES,
    [`${API}/contacts`]: CONTACTS,
    ...scenario.lists,
  };
  return stubApi((request) => {
    const path = request.url.pathname;
    if (request.method === "GET" && path === `${API}/customers/owners`) return { body: OWNERS_WIRE };
    const items = lists[path];
    if (request.method === "GET" && items !== undefined) return { body: pageOf(items, 1, 200) };
    const answer = scenario.creates?.[path];
    if (request.method === "POST" && answer !== undefined && isRecord(request.body)) return answer(request.body);
    throw new Error(`Nobody scripted ${request.method} ${path}`);
  });
}

function problem(status: number, title: string, detail: string, errors?: unknown[]): Reply {
  return { status, body: { title, status, detail, ...(errors !== undefined ? { errors } : {}) } };
}

/** Answers every create with a new record named after the one sent, except for the names the
 *  scenario has an answer for. Ids run `<prefix>-1`, `<prefix>-2` in the order the rows arrive. */
function creating(
  prefix: string,
  nameKey: string,
  wire: (id: string, name: string) => unknown,
  special: Record<string, Reply> = {},
): Answer {
  let sequence = 0;
  return (body) => {
    const name = String(body[nameKey]);
    const answer = special[name];
    if (answer !== undefined) return answer;
    sequence += 1;
    return { status: 201, body: wire(`${prefix}-${sequence}`, name) };
  };
}

function bodiesPosted(requests: readonly Recorded[], path: string): unknown[] {
  return requests.filter((r) => r.method === "POST" && r.url.pathname === `${API}${path}`).map((r) => r.body);
}

function listRequest(requests: readonly Recorded[], path: string): URL {
  const found = requests.filter((r) => r.method === "GET" && r.url.pathname === `${API}${path}`);
  expect(found, `requests to ${path}`).toHaveLength(1);
  return present(found[0], `a request to ${path}`).url;
}

/* ------------------------------ driving the dialog ------------------------------ */

async function openDialog(user: User, noun: string): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: `Import / Export ${noun}` });
}

/** Chooses a file in the hidden input, as picking one in the browser's file dialog would. */
async function chooseFile(user: User, file: File): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error("The dialog has no file input");
  await user.upload(input, file);
  await screen.findByRole("heading", { name: "Review your file" });
}

/** The figure under a label in one of the dialog's count rows ("Ready to import" and 5). */
function figure(label: string): string {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent ?? "";
}

/** The body rows of a table in the dialog, each as the text of its cells. */
function rowsOf(table: HTMLElement): string[][] {
  return Array.from(table.querySelectorAll("tbody tr"), (tr) =>
    Array.from(tr.querySelectorAll("th, td"), (cell) => cell.textContent ?? ""),
  );
}

/** What the preview says about one sheet row: its badge and the reason, as one string. */
function previewCheck(sheetRow: number): string {
  const table = screen.getByRole("region", { name: /^Rows found in / });
  const cells = rowsOf(table).find((row) => row[0] === String(sheetRow));
  if (cells === undefined) throw new Error(`The preview does not list row ${sheetRow}`);
  return cells[1] ?? "";
}

async function runImport(user: User, rows: number): Promise<void> {
  await user.click(screen.getByRole("button", { name: `Import ${rows} ${rows === 1 ? "row" : "rows"}` }));
  await screen.findByRole("heading", { name: "Import finished" }, { timeout: 10_000 });
}

/** Waits for the page to offer its one download: a workbook takes as long to build as it takes. */
async function theDownload(): Promise<Download> {
  await waitFor(() => expect(downloads).toHaveLength(1), { timeout: 5_000 });
  return present(downloads[0], "the download");
}

async function exportList(user: User): Promise<Download> {
  await user.click(screen.getByRole("button", { name: "Export to Excel" }));
  return theDownload();
}

/** A file just exported can be edited and imported again: every one of its rows passes the preview. */
async function expectImportableAgain(user: User, file: Download, rows: number): Promise<void> {
  await chooseFile(user, new File([file.blob], file.name, { type: XLSX_TYPE }));
  expect(figure("Rows in file")).toBe(String(rows));
  expect(figure("Ready to import")).toBe(String(rows));
  expect(figure("Need fixing")).toBe("0");
  expect(figure("Sample rows skipped")).toBe("0");
}

async function downloadedSheet(download: Download, name: string): Promise<ExcelJS.Worksheet> {
  return sheetOf(await readBlob(download.blob), name);
}

function sampleRow(entity: { sampleRows: readonly Record<string, SheetValue>[] }, index: number) {
  return present(entity.sampleRows[index], `sample row ${index}`);
}

/** No cell of a workbook is a formula: a name that starts with `=` is text, whatever it looks like. */
function expectNoFormulas(workbook: ExcelJS.Workbook): void {
  for (const sheet of workbook.worksheets) {
    sheet.eachRow((row) => {
      row.eachCell({ includeEmpty: false }, (cell) => {
        expect(cell.type, `${sheet.name}!${cell.address}`).not.toBe(ExcelJS.ValueType.Formula);
      });
    });
  }
}

const OWNER_NOBODY = "'nobody@example.com' is not in the list of Owner emails (see the Lists sheet)";
const XLSX_NAME = /^[a-z]+-\d{4}-\d{2}-\d{2}\.xlsx$/;

/* ---------------------------------- companies ---------------------------------- */

const COMPANY_FILTERS: CompanyFilters = { search: "north", status: "customer" };

describe("companies: import from Excel", () => {
  const ROWS: Record<string, SheetValue>[] = [
    // Row 2: every cell filled in. The Company ID column is read-only and the last one is unknown.
    {
      name: "Solstice Energy",
      owner: "Grace@Example.com",
      domain: "solstice.example",
      industry: "Energy",
      sizeBand: "51-200",
      city: "Pune",
      country: "India",
      status: "Customer",
      annualRevenue: 84000000.5,
      currency: "INR",
      id: "left-over-from-an-export",
      "Internal note": "Chased by phone",
    },
    // Row 3: a name and nothing else.
    { name: "Kestrel Foods" },
    // Row 4: a currency with no decimal places, written in lower case.
    { name: "Tokyo Tools KK", annualRevenue: 1200000, currency: "jpy" },
    // Row 5: the name is missing.
    { city: "Pune" },
    // Row 6: an owner nobody has.
    { name: "Nimbus Retail", owner: "nobody@example.com" },
    // Row 7: the sample sheet's own example, untouched.
    sampleRow(companyImport, 0),
    // Row 8: the server already has it.
    { name: "Existing Traders" },
    // Row 9: the server refuses it.
    { name: "Restricted Ltd", domain: "https://restricted.example" },
  ];

  it("previews the rows, sends the valid ones with names turned into ids, and reports every outcome", async () => {
    const requests = stubOrganisation({
      creates: {
        [`${API}/customers`]: creating("comp-new", "name", companyWire, {
          "Existing Traders": problem(409, "Conflict", "A company with that name already exists"),
          "Restricted Ltd": problem(422, "Unprocessable Entity", "Request validation failed", [
            { field: "domain", code: "invalid", message: "Domain must not include a scheme" },
          ]),
        }),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={COMPANY_FILTERS} onImported={onImported} />);
    await openDialog(user, "companies");

    await chooseFile(user, await sheetFile(companyImport.columns, ROWS, "companies.xlsx"));

    // The preview: counts, the reason for every row that will not go, and what is ignored.
    expect(figure("Rows in file")).toBe("8");
    expect(figure("Ready to import")).toBe("5");
    expect(figure("Need fixing")).toBe("2");
    expect(figure("Sample rows skipped")).toBe("1");
    expect(screen.getByText("These columns are ignored: Internal note.")).toBeTruthy();
    expect(previewCheck(2)).toBe("Ready");
    expect(previewCheck(5)).toBe("Needs fixingCompany name is required");
    expect(previewCheck(6)).toBe(`Needs fixing${OWNER_NOBODY}`);
    expect(previewCheck(7)).toBe("Sample rowAn unchanged example from the sample sheet. It is skipped.");
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(0);

    await runImport(user, 5);

    // What went over the wire, row by row, in sheet order: the owner as an id, the caller as the
    // owner when the cell is blank, money as integer minor units of the row's own currency.
    expect(bodiesPosted(requests, "/customers")).toEqual([
      {
        name: "Solstice Energy",
        owner_id: "own-2",
        domain: "solstice.example",
        industry: "Energy",
        size_band: "51-200",
        city: "Pune",
        country: "India",
        status: "customer",
        annual_revenue_minor: 8400000050,
        currency: "INR",
      },
      {
        name: "Kestrel Foods",
        owner_id: "own-1",
        domain: "",
        industry: "",
        size_band: "",
        city: "",
        country: "",
        status: "prospect",
        annual_revenue_minor: 0,
        currency: "INR",
      },
      {
        name: "Tokyo Tools KK",
        owner_id: "own-1",
        domain: "",
        industry: "",
        size_band: "",
        city: "",
        country: "",
        status: "prospect",
        annual_revenue_minor: 1200000,
        currency: "JPY",
      },
      {
        name: "Existing Traders",
        owner_id: "own-1",
        domain: "",
        industry: "",
        size_band: "",
        city: "",
        country: "",
        status: "prospect",
        annual_revenue_minor: 0,
        currency: "INR",
      },
      {
        name: "Restricted Ltd",
        owner_id: "own-1",
        domain: "https://restricted.example",
        industry: "",
        size_band: "",
        city: "",
        country: "",
        status: "prospect",
        annual_revenue_minor: 0,
        currency: "INR",
      },
    ]);

    // The result: the sentence, the four counts, and each row that was not created with its reason.
    expect(screen.getByText("3 rows were not imported. 3 companies were created.")).toBeTruthy();
    expect(figure("Created")).toBe("3");
    expect(figure("Skipped, already exist")).toBe("1");
    expect(figure("Failed")).toBe("1");
    expect(figure("Invalid")).toBe("2");
    const notCreated = rowsOf(screen.getByRole("region", { name: "Rows that were not created" }));
    expect(notCreated).toEqual([
      ["5", "—Empty", "Invalid", "Company name is required"],
      ["6", "Nimbus Retail", "Invalid", OWNER_NOBODY],
      ["8", "Existing Traders", "Skipped", "A company with that name already exists"],
      ["9", "Restricted Ltd", "Failed", "Request validation failed (domain: Domain must not include a scheme)"],
    ]);

    // The screen is told once, so it can reload its list.
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    const summary = present(onImported.mock.calls[0]?.[0], "the summary");
    expect(summary).toMatchObject({ created: 3, skipped: 1, failed: 1, invalid: 2, cancelled: false });
    expect(summary.outcomes.map((o) => [o.row, o.status, o.id])).toEqual([
      [2, "created", "comp-new-1"],
      [3, "created", "comp-new-2"],
      [4, "created", "comp-new-3"],
      [5, "invalid", undefined],
      [6, "invalid", undefined],
      [8, "skipped", undefined],
      [9, "failed", undefined],
    ]);

    // The result report keeps all of it, without the untouched sample row.
    await user.click(screen.getByRole("button", { name: "Download result report" }));
    const download = await theDownload();
    expect(download.name).toBe("companies-import-result.xlsx");
    const report = await downloadedSheet(download, "Result");
    expect(valuesOf(report)).toEqual([
      ["Row", "Company name", "Status", "Message"],
      [2, "Solstice Energy", "Created", null],
      [3, "Kestrel Foods", "Created", null],
      [4, "Tokyo Tools KK", "Created", null],
      [5, null, "Invalid", "Company name is required"],
      [6, "Nimbus Retail", "Invalid", OWNER_NOBODY],
      [8, "Existing Traders", "Skipped", "A company with that name already exists"],
      [9, "Restricted Ltd", "Failed", "Request validation failed (domain: Domain must not include a scheme)"],
    ]);

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onImported).toHaveBeenCalledTimes(1);
  }, 20_000);

  it("stops after the row in flight when cancelled, keeps what was created, and still tells the screen", async () => {
    const requests = stubOrganisation({
      creates: { [`${API}/customers`]: creating("comp-new", "name", companyWire) },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={COMPANY_FILTERS} onImported={onImported} />);
    await openDialog(user, "companies");
    await chooseFile(
      user,
      await sheetFile(
        companyImport.columns,
        [{ name: "Alder Works" }, { name: "Birch Bakery" }, { name: "Cedar Cabs" }, { name: "Dune Dairy" }],
        "companies.xlsx",
      ),
    );

    await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
    await screen.findByText("1 of 4 rows sent");
    await user.click(screen.getByRole("button", { name: "Cancel import" }));
    await screen.findByRole("heading", { name: "Import cancelled" }, { timeout: 10_000 });

    const sent = bodiesPosted(requests, "/customers");
    expect(sent.length).toBeGreaterThanOrEqual(1);
    expect(sent.length).toBeLessThan(4);
    expect(sent[0]).toMatchObject({ name: "Alder Works" });
    expect(screen.getByText(/ (was|were) created before it stopped\. The remaining rows were not sent\.$/)).toBeTruthy();
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: sent.length, cancelled: true });
  }, 20_000);
});

describe("companies: export to Excel", () => {
  it("pages the list with the screen's filters and writes every cell as the value it is, never a formula", async () => {
    const requests = stubOrganisation({
      lists: {
        [`${API}/customers`]: [
          companyWire("comp-1", "Northwind Logistics", {
            domain: "northwind.co",
            industry: "Logistics",
            size_band: "501-1000",
            city: "Mumbai",
            country: "India",
            status: "customer",
            owner_id: "own-2",
            annual_revenue_minor: 8400000050,
            created_at: "2024-02-11T09:00:00Z",
          }),
          companyWire("comp-2", '=HYPERLINK("http://evil.example","open me")', {
            domain: "-1+1",
            industry: "@SUM(A1:A9)",
            city: "+91 22 5555 0100",
            owner_id: "own-1",
          }),
          companyWire("comp-3", "Tokyo Tools KK", { currency: "JPY", annual_revenue_minor: 500, owner_id: "own-gone" }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={COMPANY_FILTERS} onImported={onImported} />);
    await openDialog(user, "companies");

    const file = await exportList(user);

    const list = listRequest(requests, "/customers");
    expect(Object.fromEntries(list.searchParams)).toEqual({ page: "1", size: "200", q: "north", status: "customer" });
    expect(file.name).toMatch(XLSX_NAME);
    expect(file.name.startsWith("companies-")).toBe(true);

    const workbook = await readBlob(file.blob);
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Export"]);
    const sheet = sheetOf(workbook, "Export");
    expect(valuesOf(sheet)).toEqual([
      [
        "Company name",
        "Owner email",
        "Domain",
        "Industry",
        "Headcount",
        "City",
        "Country",
        "Status",
        "Annual revenue",
        "Currency",
        "Company ID",
        "Created",
      ],
      [
        "Northwind Logistics",
        "Grace@Example.com",
        "northwind.co",
        "Logistics",
        "501-1000",
        "Mumbai",
        "India",
        "Customer",
        84000000.5,
        "INR",
        "comp-1",
        "2024-02-11T09:00:00Z",
      ],
      [
        '=HYPERLINK("http://evil.example","open me")',
        "sofia@example.com",
        "-1+1",
        "@SUM(A1:A9)",
        null,
        "+91 22 5555 0100",
        null,
        "Prospect",
        0,
        "INR",
        "comp-2",
        "2026-01-05T09:00:00Z",
      ],
      ["Tokyo Tools KK", null, null, null, null, null, null, "Prospect", 500, "JPY", "comp-3", "2026-01-05T09:00:00Z"],
    ]);
    // Text stays text: a string cell in the text format, so Excel never re-reads it as a formula.
    for (const address of ["A3", "C3", "D3", "F3"]) {
      expect(sheet.getCell(address).type, address).toBe(ExcelJS.ValueType.String);
      expect(sheet.getCell(address).numFmt, address).toBe("@");
    }
    // Money keeps its currency's own decimals.
    expect(sheet.getCell("I2").numFmt).toBe("#,##0.00");
    expect(sheet.getCell("I4").numFmt).toBe("#,##0");
    expectNoFormulas(workbook);
    await expectImportableAgain(user, file, 3);
  });
});

/* ------------------------------------ deals ------------------------------------ */

const DEAL_FILTERS: DealFilters = { search: "renewal", stage: "proposal", ownerId: "own-2" };

describe("deals: import from Excel", () => {
  const ROWS: Record<string, SheetValue>[] = [
    // Row 2: company, contact and owner by name; a value with decimals; the date as Excel holds one.
    {
      name: "Northwind depot expansion",
      company: "northwind logistics",
      contact: "Asha.Rao@Northwind.co",
      owner: "sofia@example.com",
      value: 3200000.5,
      currency: "inr",
      closeDate: new Date(Date.UTC(2026, 11, 15)),
      stage: "Proposal",
      probability: 55,
      source: "Referral",
      id: "left-over-from-an-export",
    },
    // Row 3: a three-decimal currency, the date as typed, everything optional left blank.
    {
      name: "Gulf terminal upgrade",
      company: "Cobalt Financial",
      value: "2500.125",
      currency: "KWD",
      closeDate: "2027-01-31",
      source: "Inbound",
    },
    // Row 4: no value.
    { name: "Silent renewal", company: "Cobalt Financial", closeDate: "2027-02-28" },
    // Row 5: a company nobody has.
    { name: "Ghost deal", company: "Nowhere Ltd", value: 1000, closeDate: "2027-03-31" },
    // Row 6: a contact of another company.
    {
      name: "Wrong contact",
      company: "Northwind Logistics",
      contact: "daniel.okafor@cobaltfin.com",
      value: 1000,
      closeDate: "2027-03-31",
    },
    // Row 7: the sample sheet's own example, untouched.
    sampleRow(dealImport, 1),
    // Row 8: the server already has it.
    { name: "Existing deal", company: "Northwind Logistics", value: 500, closeDate: "2027-04-30" },
    // Row 9: the server refuses an owner this caller may not assign.
    { name: "Assigned elsewhere", company: "Cobalt Financial", owner: "grace@example.com", value: 750, closeDate: "2027-05-31" },
  ];

  it("resolves company, contact and owner from the full lists, and reports each row's outcome", async () => {
    const requests = stubOrganisation({
      creates: {
        [`${API}/deals`]: creating("deal-new", "name", dealWire, {
          "Existing deal": problem(409, "Conflict", "A deal with that name already exists"),
          "Assigned elsewhere": problem(403, "Forbidden", "You can only assign deals you own to yourself"),
        }),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={DEAL_FILTERS} onImported={onImported} />);
    await openDialog(user, "deals");

    await chooseFile(user, await sheetFile(dealImport.columns, ROWS, "deals.xlsx"));

    expect(figure("Rows in file")).toBe("8");
    expect(figure("Ready to import")).toBe("4");
    expect(figure("Need fixing")).toBe("3");
    expect(figure("Sample rows skipped")).toBe("1");
    expect(previewCheck(4)).toBe("Needs fixingDeal value is required");
    expect(previewCheck(5)).toBe("Needs fixing'Nowhere Ltd' is not in the list of Companies (see the Lists sheet)");
    expect(previewCheck(6)).toBe(
      "Needs fixingdaniel.okafor@cobaltfin.com is not a contact of Northwind Logistics. Pick one of that company's contacts.",
    );
    expect(previewCheck(7)).toContain("Sample row");
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(0);

    await runImport(user, 4);

    expect(bodiesPosted(requests, "/deals")).toEqual([
      {
        name: "Northwind depot expansion",
        company_id: "comp-1",
        owner_id: "own-1",
        value_minor: 320000050,
        close_date: "2026-12-15",
        currency: "INR",
        stage: "proposal",
        probability: 55,
        primary_contact_id: "cont-1",
        source: "Referral",
      },
      {
        name: "Gulf terminal upgrade",
        company_id: "comp-2",
        owner_id: "own-1",
        value_minor: 2500125,
        close_date: "2027-01-31",
        currency: "KWD",
        stage: "lead",
        source: "Inbound",
      },
      {
        name: "Existing deal",
        company_id: "comp-1",
        owner_id: "own-1",
        value_minor: 50000,
        close_date: "2027-04-30",
        currency: "INR",
        stage: "lead",
        source: "",
      },
      {
        name: "Assigned elsewhere",
        company_id: "comp-2",
        owner_id: "own-2",
        value_minor: 75000,
        close_date: "2027-05-31",
        currency: "INR",
        stage: "lead",
        source: "",
      },
    ]);

    expect(screen.getByText("4 rows were not imported. 2 deals were created.")).toBeTruthy();
    expect(figure("Created")).toBe("2");
    expect(figure("Skipped, already exist")).toBe("1");
    expect(figure("Failed")).toBe("1");
    expect(figure("Invalid")).toBe("3");
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not created" }))).toEqual([
      ["4", "Silent renewal", "Invalid", "Deal value is required"],
      ["5", "Ghost deal", "Invalid", "'Nowhere Ltd' is not in the list of Companies (see the Lists sheet)"],
      [
        "6",
        "Wrong contact",
        "Invalid",
        "daniel.okafor@cobaltfin.com is not a contact of Northwind Logistics. Pick one of that company's contacts.",
      ],
      ["8", "Existing deal", "Skipped", "A deal with that name already exists"],
      ["9", "Assigned elsewhere", "Failed", "You can only assign deals you own to yourself"],
    ]);

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({
      created: 2,
      skipped: 1,
      failed: 1,
      invalid: 3,
      cancelled: false,
    });
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onImported).toHaveBeenCalledTimes(1);
  }, 20_000);
});

describe("deals: export to Excel", () => {
  it("pages the pipeline with the screen's filters and names companies, contacts and owners", async () => {
    const requests = stubOrganisation({
      lists: {
        [`${API}/deals`]: [
          dealWire("deal-1", "Northwind fleet renewal", {
            company_id: "comp-1",
            primary_contact_id: "cont-1",
            value_minor: 320000000,
            stage: "closed_won",
            owner_id: "own-2",
            probability: 100,
            close_date: "2026-12-15",
            source: "Referral",
            last_activity_at: "2026-09-01T09:00:00Z",
            created_at: "2026-08-01T09:00:00Z",
          }),
          dealWire("deal-2", '=HYPERLINK("http://evil.example","open me")', {
            company_id: "comp-2",
            currency: "KWD",
            value_minor: 150000,
            stage: "proposal",
            source: "+1 555 0100",
            created_at: "2026-08-02T09:00:00Z",
          }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={DEAL_FILTERS} onImported={onImported} />);
    await openDialog(user, "deals");

    const file = await exportList(user);

    const list = listRequest(requests, "/deals");
    expect(Object.fromEntries(list.searchParams)).toEqual({
      page: "1",
      size: "200",
      q: "renewal",
      stage: "proposal",
      owner_id: "own-2",
    });
    expect(file.name).toMatch(XLSX_NAME);
    expect(file.name.startsWith("deals-")).toBe(true);

    const workbook = await readBlob(file.blob);
    const sheet = sheetOf(workbook, "Export");
    expect(valuesOf(sheet)).toEqual([
      [
        "Deal name",
        "Company",
        "Primary contact email",
        "Owner email",
        "Deal value",
        "Currency",
        "Close date",
        "Stage",
        "Probability (%)",
        "Source",
        "Last activity",
        "Deal ID",
        "Created",
      ],
      [
        "Northwind fleet renewal",
        "Northwind Logistics",
        "asha.rao@northwind.co",
        "Grace@Example.com",
        3200000,
        "INR",
        "2026-12-15",
        "Closed Won",
        100,
        "Referral",
        "2026-09-01T09:00:00Z",
        "deal-1",
        "2026-08-01T09:00:00Z",
      ],
      [
        '=HYPERLINK("http://evil.example","open me")',
        "Cobalt Financial",
        null,
        "sofia@example.com",
        150,
        "KWD",
        "2026-12-15",
        "Proposal",
        20,
        "+1 555 0100",
        null,
        "deal-2",
        "2026-08-02T09:00:00Z",
      ],
    ]);
    expect(sheet.getCell("A3").type).toBe(ExcelJS.ValueType.String);
    expect(sheet.getCell("J3").type).toBe(ExcelJS.ValueType.String);
    expect(sheet.getCell("E3").numFmt).toBe("#,##0.000");
    expectNoFormulas(workbook);
    await expectImportableAgain(user, file, 2);
  });
});

/* ------------------------------------ contacts ----------------------------------- */

const CONTACT_FILTERS: ContactFilters = { search: "asha", status: "qualified" };

describe("contacts: import and export", () => {
  it("imports a contact with its company by name and its phone with a country code, and skips one already on file", async () => {
    const requests = stubOrganisation({
      creates: {
        [`${API}/contacts`]: creating("cont-new", "full_name", (id, name) => contactWire(id, "new@example.com", "comp-1", { full_name: name }), {
          "Daniel Okafor": problem(409, "Conflict", "A contact with that email already exists"),
        }),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={contactImport} filters={CONTACT_FILTERS} onImported={onImported} />);
    await openDialog(user, "contacts");

    await chooseFile(
      user,
      await sheetFile(
        contactImport.columns,
        [
          {
            fullName: "Priya Nair",
            email: "Priya.Nair@Northwind.co",
            company: "Northwind Logistics",
            title: "Head of Finance",
            status: "Qualified",
            phone: "+91 98765 43210",
          },
          { fullName: "Daniel Okafor", email: "daniel.okafor@cobaltfin.com", company: "Cobalt Financial" },
        ],
        "contacts.xlsx",
      ),
    );
    expect(figure("Ready to import")).toBe("2");
    await runImport(user, 2);

    expect(bodiesPosted(requests, "/contacts")).toEqual([
      {
        full_name: "Priya Nair",
        email: "priya.nair@northwind.co",
        company_id: "comp-1",
        owner_id: "own-1",
        title: "Head of Finance",
        status: "qualified",
        raw_phone: "+919876543210",
      },
      {
        full_name: "Daniel Okafor",
        email: "daniel.okafor@cobaltfin.com",
        company_id: "comp-2",
        owner_id: "own-1",
        title: "",
        status: "lead",
      },
    ]);
    expect(screen.getByText("Everything imported. 1 contact was created. 1 already existed and was skipped.")).toBeTruthy();
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not created" }))).toEqual([
      ["3", "Daniel Okafor", "Skipped", "A contact with that email already exists"],
    ]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
  }, 20_000);

  it("exports the phone as the list masked it, in a read-only column, and leaves the importable Phone blank", async () => {
    const requests = stubOrganisation({
      lists: {
        [`${API}/contacts`]: [
          contactWire("cont-1", "asha.rao@northwind.co", "comp-1", {
            full_name: "Asha Rao",
            title: "VP Operations",
            status: "qualified",
            owner_id: "own-2",
            phone: "+•• •••• ••210",
            last_activity_at: "2026-09-01T09:00:00Z",
          }),
          contactWire("cont-2", "=cmd@example.com", "comp-2", { full_name: "@Evil Name", phone: "+•• ••••• ••345" }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={contactImport} filters={CONTACT_FILTERS} onImported={onImported} />);
    await openDialog(user, "contacts");

    const file = await exportList(user);

    const list = listRequest(requests, "/contacts");
    expect(Object.fromEntries(list.searchParams)).toEqual({ page: "1", size: "200", q: "asha", status: "qualified" });
    // Only the list is read: no reveal or unmask request of any kind.
    expect(requests.map((r) => `${r.method} ${r.url.pathname}`).sort()).toEqual([
      `GET ${API}/contacts`,
      `GET ${API}/customers`,
      `GET ${API}/customers/owners`,
    ]);

    const workbook = await readBlob(file.blob);
    const sheet = sheetOf(workbook, "Export");
    expect(valuesOf(sheet)).toEqual([
      [
        "Full name",
        "Email",
        "Company",
        "Owner email",
        "Title",
        "Status",
        "Phone",
        "Phone (masked)",
        "Last activity",
        "Contact ID",
        "Created",
      ],
      [
        "Asha Rao",
        "asha.rao@northwind.co",
        "Northwind Logistics",
        "Grace@Example.com",
        "VP Operations",
        "Qualified",
        null,
        "+•• •••• ••210",
        "2026-09-01T09:00:00Z",
        "cont-1",
        "2026-01-06T09:00:00Z",
      ],
      [
        "@Evil Name",
        "=cmd@example.com",
        "Cobalt Financial",
        "sofia@example.com",
        null,
        "Lead",
        null,
        "+•• ••••• ••345",
        null,
        "cont-2",
        "2026-01-06T09:00:00Z",
      ],
    ]);
    expect(sheet.getCell("B3").type).toBe(ExcelJS.ValueType.String);
    expectNoFormulas(workbook);
    await expectImportableAgain(user, file, 2);
  });
});

/* ------------------------------------- leads ------------------------------------- */

const LEAD_FILTERS: LeadFilters = { status: "working" };

describe("leads: import and export", () => {
  it("sends every lead from the source import, and no owner unless the row names one", async () => {
    const requests = stubOrganisation({
      creates: { [`${API}/leads`]: creating("lead-new", "full_name", leadWire) },
    });
    const user = userEvent.setup();
    render(<Harness entity={leadImport} filters={LEAD_FILTERS} onImported={onImported} />);
    await openDialog(user, "leads");

    await chooseFile(
      user,
      await sheetFile(
        leadImport.columns,
        [
          {
            fullName: "Kavya Menon",
            company: "Lotus Mills",
            email: "Kavya@LotusMills.example",
            phone: "+91 90000 12345",
            notes: "Asked for a demo.\nCall after 4pm.",
          },
          { fullName: "Tomas Berg", owner: "grace@example.com", phone: "+47 912 34 567" },
        ],
        "leads.xlsx",
      ),
    );
    await runImport(user, 2);

    // A note keeps its line break; a blank owner is left out so the round-robin rule assigns it.
    expect(bodiesPosted(requests, "/leads")).toEqual([
      {
        full_name: "Kavya Menon",
        company: "Lotus Mills",
        email: "kavya@lotusmills.example",
        phone: "+919000012345",
        source: "import",
        notes: "Asked for a demo.\nCall after 4pm.",
      },
      {
        full_name: "Tomas Berg",
        company: null,
        email: null,
        phone: "+4791234567",
        source: "import",
        owner_id: "own-2",
        notes: "",
      },
    ]);
    expect(screen.getByText("Everything imported. 2 leads were created.")).toBeTruthy();
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: 2, skipped: 0, failed: 0, invalid: 0 });
  }, 20_000);

  it("exports the masked email and phone in read-only columns, and leaves the importable ones blank", async () => {
    const requests = stubOrganisation({
      lists: {
        [`${API}/leads`]: [
          leadWire("lead-1", "Meera Iyer", {
            company: "Halcyon Textiles",
            owner_id: "own-2",
            status: "working",
            source: "form",
            notes: "Asked for a demo.",
          }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={leadImport} filters={LEAD_FILTERS} onImported={onImported} />);
    await openDialog(user, "leads");

    const file = await exportList(user);

    expect(Object.fromEntries(listRequest(requests, "/leads").searchParams)).toEqual({
      page: "1",
      size: "200",
      status: "working",
    });
    const workbook = await readBlob(file.blob);
    expect(valuesOf(sheetOf(workbook, "Export"))).toEqual([
      [
        "Full name",
        "Company",
        "Email",
        "Phone",
        "Owner email",
        "Notes",
        "Status",
        "Source",
        "Email (masked)",
        "Phone (masked)",
        "Lead ID",
        "Captured",
      ],
      [
        "Meera Iyer",
        "Halcyon Textiles",
        null,
        null,
        "Grace@Example.com",
        "Asked for a demo.",
        "Working",
        "Web form",
        "m•••@h•••.in",
        "+•• ••••• ••345",
        "lead-1",
        "2026-09-20T09:00:00Z",
      ],
    ]);
    expectNoFormulas(workbook);
    await expectImportableAgain(user, file, 1);
  });
});

/* --------------------------------- the sample sheet --------------------------------- */

interface TemplateCase {
  name: string;
  entity: ImportEntity<unknown, unknown, unknown>;
  /** Headers of the required columns, which the Import sheet marks with an asterisk. */
  required: string[];
  /** Headers of the columns that carry a drop-down over a list of the organisation's own. */
  lookups: Record<string, string[]>;
  /** Headers of the columns whose drop-down is the fixed set of an enum. */
  enums: string[];
  /** The first example row, cell by cell, as the Import sheet holds it. */
  firstExample: ExcelJS.CellValue[];
}

const TEAM = ["sofia@example.com", "Grace@Example.com"];

const TEMPLATES: TemplateCase[] = [
  {
    name: "companies",
    entity: companyImport,
    required: ["Company name"],
    lookups: { "Owner email": TEAM },
    enums: ["Status"],
    firstExample: ["Northwind Logistics", null, "northwind.co", "Logistics", "501-1000", "Mumbai", "India", "Customer", 84000000, "INR"],
  },
  {
    name: "contacts",
    entity: contactImport,
    required: ["Full name", "Email", "Company"],
    lookups: { Company: ["Northwind Logistics", "Cobalt Financial"], "Owner email": TEAM },
    enums: ["Status"],
    firstExample: ["Asha Rao", "asha.rao@northwind.co", "Northwind Logistics", null, "VP Operations", "Customer", "+91 98765 43210"],
  },
  {
    name: "leads",
    entity: leadImport,
    required: ["Full name"],
    lookups: { "Owner email": TEAM },
    enums: [],
    firstExample: ["Meera Iyer", "Halcyon Textiles", "meera.iyer@halcyontextiles.in", "+91 90000 12345", null, "Asked for a demo after the Chennai trade show."],
  },
  {
    name: "deals",
    entity: dealImport,
    required: ["Deal name", "Company", "Deal value", "Close date"],
    lookups: {
      Company: ["Northwind Logistics", "Cobalt Financial"],
      "Primary contact email": ["asha.rao@northwind.co", "daniel.okafor@cobaltfin.com"],
      "Owner email": TEAM,
    },
    enums: ["Stage"],
    firstExample: ["Northwind fleet renewal", "Northwind Logistics", null, null, 3200000, "INR", new Date(Date.UTC(2026, 11, 15)), "Proposal", 55, "Referral"],
  },
];

describe.each(TEMPLATES)("$name: the sample sheet", ({ name, entity, required, lookups, enums, firstExample }) => {
  const importable = entity.columns.filter((column) => column.readOnly !== true);

  /** Opens the dialog for an organisation and downloads the sample sheet. The dialog stays open. */
  async function downloadSampleFor(lists?: Scenario["lists"]): Promise<{ user: User; file: Download }> {
    stubOrganisation(lists === undefined ? {} : { lists });
    const user = userEvent.setup();
    render(<Harness entity={entity} filters={undefined} onImported={onImported} />);
    await openDialog(user, entity.noun);
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    const file = await theDownload();
    expect(file.name).toBe(`${name}-sample.xlsx`);
    return { user, file };
  }

  async function downloadSample(): Promise<ExcelJS.Workbook> {
    return readBlob((await downloadSampleFor()).file.blob);
  }

  it.each([
    { organisation: "one that holds the companies the examples name", lists: undefined },
    {
      organisation: "one whose companies and contacts are named otherwise",
      lists: {
        [`${API}/customers`]: [companyWire("comp-7", "Acme Traders"), companyWire("comp-8", "Bluebird Foods")],
        [`${API}/contacts`]: [
          contactWire("cont-8", "priya@bluebird.example", "comp-8"),
          contactWire("cont-7", "vikram@acme.example", "comp-7"),
        ],
      },
    },
  ])("reads back as its own example rows and nothing else, for $organisation", async ({ lists }) => {
    const { user, file } = await downloadSampleFor(lists);

    await chooseFile(user, new File([file.blob], file.name, { type: XLSX_TYPE }));

    const examples = String(entity.sampleRows.length);
    expect(figure("Rows in file")).toBe(examples);
    expect(figure("Sample rows skipped")).toBe(examples);
    expect(figure("Ready to import")).toBe("0");
    expect(figure("Need fixing")).toBe("0");
    expect(screen.getByText("Only the sample rows are in this file.")).toBeTruthy();
    const nothingToSend = screen.getByRole("button", { name: "Import 0 rows" });
    expect(nothingToSend instanceof HTMLButtonElement && nothingToSend.disabled).toBe(true);
  });

  it("has an Import sheet headed by the importable columns, with the required ones marked *", async () => {
    const sheet = sheetOf(await downloadSample(), "Import");
    const [headers = []] = valuesOf(sheet);
    expect(headers).toEqual(importable.map((column) => (required.includes(column.header) ? `${column.header} *` : column.header)));
    expect(headers.filter((header) => String(header).endsWith("*")).map((header) => String(header).replace(/ \*$/, ""))).toEqual(
      required,
    );
    // Read-only columns (ids, timestamps, masked values) are exported, never asked for.
    for (const column of entity.columns.filter((c) => c.readOnly === true)) {
      expect(headers).not.toContain(column.header);
    }
  });

  it("holds the example rows, the first as the entity ships it", async () => {
    const sheet = sheetOf(await downloadSample(), "Import");
    const rows = valuesOf(sheet);
    expect(rows).toHaveLength(entity.sampleRows.length + 1);
    expect(rows[1]).toEqual(firstExample);
  });

  it("has an Instructions sheet with every column, whether it is required, and every note of the entity", async () => {
    const sheet = sheetOf(await downloadSample(), "Instructions");
    const rows = valuesOf(sheet);
    const first = rows.map((row) => row[0]);

    expect(first[0]).toBe(`How to fill in the ${entity.noun} sheet`);
    const table = rows.findIndex((row) => row[0] === "Column");
    expect(rows[table]).toEqual(["Column", "Required", "Format", "Allowed values", "Example", "What to enter"]);
    const described = rows.slice(table + 1, table + 1 + importable.length);
    expect(described.map((row) => row[0])).toEqual(importable.map((column) => column.header));
    expect(described.map((row) => row[1])).toEqual(importable.map((column) => (column.required === true ? "Yes" : "No")));
    expect(described.map((row) => row[5])).toEqual(importable.map((column) => column.help));

    expect(entity.notes?.length).toBeGreaterThan(0);
    for (const note of entity.notes ?? []) expect(first, note).toContain(note);
    expect(first).toContain("Columns marked * are required.");
  });

  it("puts a drop-down on every enum column and on every column that names something of the organisation's", async () => {
    const workbook = await downloadSample();
    const sheet = sheetOf(workbook, "Import");
    const lists = sheetOf(workbook, "Lists");

    const dropdowns = [...Object.keys(lookups), ...enums];
    importable.forEach((column, index) => {
      const validation = validationAt(sheet, `${String.fromCharCode(65 + index)}2`);
      if (!dropdowns.includes(column.header)) {
        expect(validation, column.header).toBeUndefined();
        return;
      }
      expect(validation?.type, column.header).toBe("list");
      const [formula = ""] = validation?.formulae ?? [];

      const labels = lookups[column.header];
      if (labels === undefined) {
        // An enum: the labels themselves, inline.
        expect(formula, column.header).toBe(`"${(column.options ?? []).map((option) => option.label).join(",")}"`);
        return;
      }
      // A lookup: the organisation's own names (for owners, only the active ones), which a short
      // list carries inline and the Lists sheet holds under the column's own header.
      expect(formula, column.header).toBe(`"${labels.join(",")}"`);
      expect(listUnder(lists, column.header), column.header).toEqual(labels);
    });
  });
});

/** The names under a header of the Lists sheet, down to the first blank cell. */
function listUnder(lists: ExcelJS.Worksheet, header: string): ExcelJS.CellValue[] {
  const [headers = []] = valuesOf(lists);
  const column = headers.indexOf(header) + 1;
  if (column === 0) throw new Error(`The Lists sheet has no column headed "${header}"`);
  const names: ExcelJS.CellValue[] = [];
  for (let row = 2; lists.getCell(row, column).value !== null; row += 1) names.push(lists.getCell(row, column).value);
  return names;
}

describe("deals: the sample sheet for an organisation with many companies", () => {
  it("points the Company drop-down at the Lists sheet, which holds every company", async () => {
    const companies = Array.from({ length: 60 }, (_, i) => companyWire(`comp-${i + 1}`, `Company number ${i + 1} Traders`));
    stubOrganisation({ lists: { [`${API}/customers`]: companies } });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={DEAL_FILTERS} onImported={onImported} />);
    await openDialog(user, "deals");
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    const workbook = await readBlob((await theDownload()).blob);
    const lists = sheetOf(workbook, "Lists");
    const names = companies.map((company) => company.name);
    expect(listUnder(lists, "Company")).toEqual(names);

    // Column B of the Import sheet is Company; 60 names do not fit in an inline list.
    const [formula = ""] = validationAt(sheetOf(workbook, "Import"), "B2")?.formulae ?? [];
    const [headers = []] = valuesOf(lists);
    const letter = String.fromCharCode(65 + headers.indexOf("Company"));
    expect(formula).toBe(`Lists!$${letter}$2:$${letter}$${names.length + 1}`);
  });
});

/* ---------------------------- the list screens, end to end ---------------------------- */

interface ScreenCase {
  screen: string;
  element: ReactElement;
  /** The permission that lets the caller create here, and so import. */
  write: string;
  listPath: string;
  /** What the screen says while its list is empty. */
  emptyTitle: string;
  /** One row to import, and the text the list then shows for it. */
  row: Record<string, SheetValue>;
  shown: string;
  entity: ImportEntity<unknown, unknown, unknown>;
  answer: Answer;
  /** Whether the shared directory of names is reloaded as well. */
  reloadsDirectory: boolean;
}

const SCREENS: ScreenCase[] = [
  {
    screen: "Companies",
    element: <CompaniesPage />,
    write: "crm.book.company.write:OWN",
    listPath: "/customers",
    emptyTitle: "No companies yet",
    row: { name: "Kestrel Foods" },
    shown: "Kestrel Foods",
    entity: companyImport,
    answer: (body) => ({ status: 201, body: companyWire("comp-new", String(body.name)) }),
    reloadsDirectory: true,
  },
  {
    screen: "Contacts",
    element: <ContactsPage />,
    write: "crm.book.contact.write:OWN",
    listPath: "/contacts",
    emptyTitle: "No contacts yet",
    row: { fullName: "Priya Nair", email: "priya.nair@northwind.co", company: "Northwind Logistics" },
    shown: "Priya Nair",
    entity: contactImport,
    answer: (body) => ({
      status: 201,
      body: contactWire("cont-new", String(body.email), "comp-1", { full_name: String(body.full_name) }),
    }),
    reloadsDirectory: true,
  },
  {
    screen: "Leads",
    element: <LeadsPage />,
    write: "crm.lead.lead.write:OWN",
    listPath: "/leads",
    emptyTitle: "No leads yet",
    row: { fullName: "Kavya Menon" },
    shown: "Kavya Menon",
    entity: leadImport,
    answer: (body) => ({ status: 201, body: leadWire("lead-new", String(body.full_name)) }),
    reloadsDirectory: false,
  },
  {
    screen: "Deals",
    element: <DealsPage />,
    write: "crm.pipeline.deal.write:OWN",
    listPath: "/deals",
    emptyTitle: "No matching deals",
    row: { name: "Depot expansion", company: "Northwind Logistics", value: 5000, closeDate: "2027-01-31" },
    shown: "Depot expansion",
    entity: dealImport,
    answer: (body) => ({ status: 201, body: dealWire("deal-new", String(body.name)) }),
    reloadsDirectory: false,
  },
];

describe.each(SCREENS)("the $screen screen", (c) => {
  it("lists what an import created as soon as it finishes, without a refresh", async () => {
    // The gateway's list starts empty and grows with each create, as the real one does.
    const stored: unknown[] = [];
    const reloadDirectory = vi.fn();
    stubOrganisation({
      lists: { [`${API}${c.listPath}`]: stored },
      creates: {
        [`${API}${c.listPath}`]: (body) => {
          const reply = c.answer(body);
          stored.push(reply.body);
          return reply;
        },
      },
    });
    const user = userEvent.setup();
    render(
      <StrictMode>
        <MemoryRouter>
          <PermissionProvider grants={[c.write]}>
            <DirectoryContext.Provider value={{ ...EMPTY_DIRECTORY, status: "success", reload: reloadDirectory }}>
              {c.element}
            </DirectoryContext.Provider>
          </PermissionProvider>
        </MemoryRouter>
      </StrictMode>,
    );
    await screen.findByText(c.emptyTitle);
    expect(screen.queryByText(c.shown)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    await screen.findByRole("dialog", { name: `Import / Export ${c.entity.noun}` });
    await chooseFile(user, await sheetFile(c.entity.columns, [c.row], `${c.entity.fileStem}.xlsx`));
    await runImport(user, 1);

    // Behind the dialog the list has been asked again and now holds the new row.
    await waitFor(() => expect(screen.queryAllByText(c.shown).length).toBeGreaterThan(0), { timeout: 5_000 });
    expect(screen.queryByText(c.emptyTitle)).toBeNull();
    expect(reloadDirectory).toHaveBeenCalledTimes(c.reloadsDirectory ? 1 : 0);
  }, 20_000);
});
