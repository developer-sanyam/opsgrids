/**
 * "Add new and update existing" on the CRM's four lists, the way a person uses it, with nothing
 * between them and the wire but a fake gateway: the real `useImportExport` from `@opsgrids/client`,
 * the real `ImportExportButton` dialog from `@opsgrids/ui`, this app's real entity definitions and
 * api client. A workbook is built in memory the way Excel would save it (an export that was edited,
 * or a sheet with a Record ID and one column), the mode is chosen in the dialog, the file is read
 * and previewed against the stored records, and the rows are sent. What is asserted is what a user
 * sees (the Action column, the counts, the result) and exactly what went over the wire.
 *
 * Real timers, as in `importFlow.test.tsx`: the runner spaces requests 350 ms apart, and a run of a
 * few requests takes a second or two.
 */
import { StrictMode } from "react";
import ExcelJS from "exceljs";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useImportExport } from "@opsgrids/client";
import type { ImportColumn, ImportEntity, ImportSummary, SheetValue } from "@opsgrids/client";
import { ImportExportButton } from "@opsgrids/ui";

import { getMySubjectRef } from "@/lib/subject";
import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
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
import { XLSX_TYPE, captureDownloads, readBlob, sheetOf, valuesOf } from "./importFlowKit";
import type { Download } from "./importFlowKit";
import { leadImport } from "./leads";

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

/** The app mounts everything in StrictMode (`main.tsx`): the hook has to come through it intact. */
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

type Answer = (body: unknown) => Reply;

interface Organisation {
  /** What each list endpoint returns, by path. The lookups and the update's own load read these. */
  lists: Record<string, readonly unknown[]>;
  /** The answer to a write, by `METHOD /path`. A write nobody scripted answers with the record it names. */
  answers?: Record<string, Answer>;
}

const isRecordWith = (item: unknown, id: string): boolean =>
  typeof item === "object" && item !== null && "id" in item && item.id === id;

/** The stored record a write to `/customers/comp-1`, `/contacts/cont-1/phone` or `/deals/deal-1/stage` names. */
function recordNamedBy(lists: Organisation["lists"], path: string): unknown {
  const [, collection = "", id = ""] = path.split("/");
  return lists[`/${collection}`]?.find((item) => isRecordWith(item, id));
}

/** A small organisation behind the gateway. A request nobody scripted fails the test. */
function stubOrganisation(organisation: Organisation): Recorded[] {
  return stubApi((request) => {
    const path = request.url.pathname.slice(API.length);
    if (request.method === "GET" && path === "/customers/owners") return { body: OWNERS_WIRE };
    if (request.method === "GET") {
      const items = organisation.lists[path];
      if (items !== undefined) return { body: pageOf(items, 1, 200) };
    }
    const answer = organisation.answers?.[`${request.method} ${path}`];
    if (answer !== undefined) return answer(request.body);
    const stored = recordNamedBy(organisation.lists, path);
    if (request.method !== "GET" && stored !== undefined) return { body: stored };
    throw new Error(`Nobody scripted ${request.method} ${path}`);
  });
}

function problem(status: number, title: string, detail: string, errors?: unknown[]): Reply {
  return { status, body: { title, status, detail, ...(errors !== undefined ? { errors } : {}) } };
}

/** Every write that went out, in order: `PATCH /customers/comp-1` and the JSON that was sent. */
function writesOf(requests: readonly Recorded[]): { request: string; body: unknown }[] {
  return requests
    .filter((r) => r.method !== "GET")
    .map((r) => ({ request: `${r.method} ${r.url.pathname.slice(API.length)}`, body: r.body }));
}

/** How many times a list was asked for a page: the lookups and the update's own load each ask. */
function listReads(requests: readonly Recorded[], path: string): number {
  return requests.filter((r) => r.method === "GET" && r.url.pathname === `${API}${path}`).length;
}

/* ------------------------------ driving the dialog ------------------------------ */

async function openDialog(user: User, noun: string): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: `Import / Export ${noun}` });
}

/** Chooses what the import does with a row that names a record that exists, in the menu or on the preview. */
async function chooseMode(user: User, label: "Add new records only" | "Add new and update existing"): Promise<void> {
  await user.click(screen.getByRole("radio", { name: label }));
}

/** Chooses a file in the hidden input, as picking one in the browser's file dialog would. */
async function chooseFile(user: User, file: File): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error("The dialog has no file input");
  await user.upload(input, file);
  await screen.findByRole("heading", { name: "Review your file" });
}

/** The figure under a label in one of the dialog's count rows ("Updates" and 4). */
function figure(label: string): string {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent ?? "";
}

/** The body rows of a table in the dialog, each as the text of its cells. */
function rowsOf(table: HTMLElement): string[][] {
  return Array.from(table.querySelectorAll("tbody tr"), (tr) =>
    Array.from(tr.querySelectorAll("th, td"), (cell) => cell.textContent ?? ""),
  );
}

/** What the preview says about one sheet row: its Action (a badge, and the reason or the changes), as one string. */
function action(sheetRow: number): string {
  const table = screen.getByRole("region", { name: /^Rows found in / });
  const cells = rowsOf(table).find((row) => row[0] === String(sheetRow));
  if (cells === undefined) throw new Error(`The preview does not list row ${sheetRow}`);
  return cells[1] ?? "";
}

/** The import button says what it will do ("Update 3, add 2"): the rows it sends are the numbers in it. */
async function runImport(user: User, rows: number): Promise<void> {
  const button = screen.getByRole("button", { name: /^(Update|Add) \d/ });
  const sends = [...(button.textContent ?? "").matchAll(/\d[\d,]*/g)].reduce((sum, [count]) => sum + Number(count.replace(/,/g, "")), 0);
  expect(sends).toBe(rows);
  await user.click(button);
  await screen.findByRole("heading", { name: "Import finished" }, { timeout: 15_000 });
}

/** A sheet as a user leaves it after editing an export: only the columns named, in that order. */
async function sheetOfColumns(
  entity: { columns: readonly ImportColumn[] },
  keys: readonly string[],
  rows: readonly (readonly SheetValue[])[],
  name: string,
): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(keys.map((key) => present(entity.columns.find((column) => column.key === key), `column ${key}`).header));
  for (const row of rows) sheet.addRow([...row]);
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], name, { type: XLSX_TYPE });
}

/** What the engine says about an owner nobody has, when updating: its sentence, then the update hint. */
const OWNER_UPDATE_HINT = "Use the email of an active sales rep, see the Lists sheet. Leave blank to keep the current owner.";
const OWNER_ADD_HINT = "Use the email of an active sales rep, see the Lists sheet. Leave blank to assign it to yourself.";
/** What the engine says about a Record ID no company has (an OWN-scope caller may not see it either). */
const UNKNOWN_COMPANY_ID =
  "That Company ID is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see.";
const notAnOwner = (email: string, hint: string): string =>
  `'${email}' is not one of the accepted values for Owner email (see the Lists sheet). ${hint}`;

/* ---------------------------------- companies ---------------------------------- */

const NORTHWIND = companyWire("comp-1", "Northwind Logistics", {
  owner_id: "own-1",
  domain: "northwind.co",
  industry: "Logistics",
  city: "Mumbai",
  status: "customer",
  annual_revenue_minor: 8400000050,
});
const COBALT = companyWire("comp-2", "Cobalt Financial", { owner_id: "own-2", industry: "Financial Services", city: "Bengaluru" });
// A yen company: 500 minor units are 500 yen, and an amount typed for it is read in yen, not rupees.
const KOBO = companyWire("comp-3", "Kobo Trading", { currency: "JPY", annual_revenue_minor: 500, city: "Osaka" });
const AOKI = companyWire("comp-4", "Aoki Freight", { city: "Nagoya" });
const SOLSTICE = companyWire("comp-5", "Solstice Energy", { city: "Ahmedabad" });
const DUNE = companyWire("comp-6", "Dune Dairy", { city: "Jaipur", annual_revenue_minor: 1000 });
const ORGANISATION = [NORTHWIND, COBALT, KOBO, AOKI, SOLSTICE, DUNE];

describe("companies: update from Excel", () => {
  const KEYS = ["id", "name", "industry", "city", "annualRevenue", "currency"] as const;

  it("previews what each row will do, sends only what changed, and reports every kind of outcome", async () => {
    const requests = stubOrganisation({
      lists: { "/customers": ORGANISATION },
      answers: {
        "PATCH /customers/comp-5": () => problem(409, "Conflict", "A company with that name already exists"),
        "POST /customers": () => ({ status: 201, body: companyWire("comp-new-1", "Brand New Co") }),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "companies");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        companyImport,
        KEYS,
        [
          // 2: by Record ID; only the City differs, the other cells repeat what is stored.
          ["comp-1", "Northwind Logistics", "Logistics", "Pune", null, null],
          // 3: by name (ignoring capitals, as the server does), no Record ID; the industry is emptied.
          [null, "cobalt FINANCIAL", "[clear]", null, null, null],
          // 4: a yen company, an amount and no currency: the amount is read in yen, no currency is sent.
          ["comp-3", null, null, null, 800, null],
          // 5: nothing differs from what is stored.
          ["comp-4", "Aoki Freight", null, "Nagoya", null, null],
          // 6: a name nobody has, no Record ID: a new company.
          [null, "Brand New Co", null, "Kyoto", null, null],
          // 7: a rename onto another company's name: the server will refuse it.
          ["comp-5", "Cobalt Financial", null, null, null, null],
          // 8: a Record ID no company has.
          ["comp-404", null, null, "Delhi", null, null],
          // 9: a revenue cannot be emptied, only changed.
          ["comp-6", null, null, null, "[clear]", null],
          // 10: the same company as row 2.
          ["comp-1", null, null, "Thane", null, null],
        ],
        "companies-update.xlsx",
      ),
    );

    expect(figure("Rows in file")).toBe("9");
    expect(figure("New records")).toBe("1");
    expect(figure("Updates")).toBe("4");
    expect(figure("No change")).toBe("1");
    expect(figure("Need fixing")).toBe("3");
    expect(screen.getByText("5 rows ready to import (4 to update, 1 new), 1 unchanged, 3 need fixing.")).toBeTruthy();
    expect(action(2)).toBe("UpdateChanges: City");
    expect(action(3)).toBe("UpdateChanges: Industry");
    expect(action(4)).toBe("UpdateChanges: Annual revenue");
    expect(action(5)).toBe("No change");
    expect(action(6)).toBe("New");
    expect(action(7)).toBe("UpdateChanges: Company name");
    expect(action(8)).toBe(`Needs fixing${UNKNOWN_COMPANY_ID}`);
    expect(action(9)).toBe("Needs fixingAnnual revenue cannot be cleared");
    expect(action(10)).toBe("Needs fixingThis row updates the same record as row 2. Keep only one of the two rows.");
    // Nothing has been sent: the preview only read.
    expect(writesOf(requests)).toEqual([]);

    await runImport(user, 5);

    // Exactly what went over the wire, in sheet order: the changed keys and nothing else, the
    // documented empty value, the amount in the stored currency with no currency key, and the
    // new company as a create.
    expect(writesOf(requests)).toEqual([
      { request: "PATCH /customers/comp-1", body: { city: "Pune" } },
      { request: "PATCH /customers/comp-2", body: { industry: "" } },
      { request: "PATCH /customers/comp-3", body: { annual_revenue_minor: 800 } },
      {
        request: "POST /customers",
        body: {
          name: "Brand New Co",
          owner_id: "own-1",
          domain: "",
          industry: "",
          size_band: "",
          city: "Kyoto",
          country: "",
          status: "prospect",
          annual_revenue_minor: 0,
          currency: "INR",
        },
      },
      { request: "PATCH /customers/comp-5", body: { name: "Cobalt Financial" } },
    ]);

    // The result: a rename collision is a failure with the server's sentence, never a skip.
    expect(screen.getByText("3 updated, 1 created, 1 unchanged, 4 failed.")).toBeTruthy();
    expect(figure("Created")).toBe("1");
    expect(figure("Updated")).toBe("3");
    expect(figure("Unchanged")).toBe("1");
    expect(figure("Failed")).toBe("1");
    expect(figure("Invalid")).toBe("3");
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not imported" }))).toEqual([
      // Row, item, the stored record the row was matched to, result, details.
      ["7", "Cobalt Financial", "Solstice Energy", "Failed", "A company with that name already exists"],
      ["8", "—Empty", "—Empty", "Invalid", "That Company ID is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see."],
      ["9", "Dune Dairy", "Dune Dairy", "Invalid", "Annual revenue cannot be cleared"],
      ["10", "—Empty", "Northwind Logistics", "Invalid", "This row updates the same record as row 2. Keep only one of the two rows."],
    ]);

    // The screen is told once, so it can reload its list.
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    const summary = present(onImported.mock.calls[0]?.[0], "the summary");
    expect(summary).toMatchObject({ created: 1, updated: 3, unchanged: 1, skipped: 0, failed: 1, invalid: 3, cancelled: false });
    expect(summary.outcomes.map((o) => [o.row, o.status, o.changes])).toEqual([
      [2, "updated", ["City"]],
      [3, "updated", ["Industry"]],
      [4, "updated", ["Annual revenue"]],
      [5, "unchanged", undefined],
      [6, "created", undefined],
      [7, "failed", ["Company name"]],
      [8, "invalid", undefined],
      [9, "invalid", undefined],
      [10, "invalid", undefined],
    ]);
  }, 30_000);

  it("accepts a sheet of only a Record ID and the column to change, and leaves every other field alone", async () => {
    const requests = stubOrganisation({ lists: { "/customers": ORGANISATION } });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "companies");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(user, await sheetOfColumns(companyImport, ["id", "city"], [["comp-1", "Pune"], ["comp-3", "Kobe"]], "cities.xlsx"));

    // Company name is required to add a company, but a row that updates one needs no such cell.
    expect(screen.queryByText("Your file is missing required columns, so nothing can be imported.")).toBeNull();
    expect(screen.getByText("2 rows ready to import (2 to update).")).toBeTruthy();
    expect(figure("Updates")).toBe("2");
    expect(action(2)).toBe("UpdateChanges: City");
    expect(action(3)).toBe("UpdateChanges: City");

    await runImport(user, 2);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /customers/comp-1", body: { city: "Pune" } },
      { request: "PATCH /customers/comp-3", body: { city: "Kobe" } },
    ]);
    expect(screen.getByText("Everything imported. 2 updated.")).toBeTruthy();
    expect(figure("Updated")).toBe("2");
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: 0, updated: 2, unchanged: 0, failed: 0 });
  }, 20_000);

  it("checks a previewed file again in the other mode from the cells it already read, and loads the records once", async () => {
    const requests = stubOrganisation({ lists: { "/customers": ORGANISATION } });
    const reads = vi.spyOn(FileReader.prototype, "readAsArrayBuffer");
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "companies");

    // Adding only, which is what the dialog starts in: every row names a record, and says how to update it.
    await chooseFile(user, await sheetOfColumns(companyImport, ["id", "city"], [["comp-1", "Pune"], ["comp-3", "Kobe"]], "cities.xlsx"));
    expect(screen.getByText("Your file is missing required columns, so nothing can be imported.")).toBeTruthy();
    const refused =
      "Needs fixingThis row has a Company ID, so it came from an export and is already in the system. " +
      "Clear the Company ID cell to add it as a new record. Switch to Add new and update existing to update it.";
    expect(action(2)).toBe(refused);
    expect(listReads(requests, "/customers")).toBe(0);
    expect(reads).toHaveBeenCalledTimes(1);

    // Switching on the preview: the same file, now matched against the stored companies.
    await chooseMode(user, "Add new and update existing");
    await screen.findByText("2 rows ready to import (2 to update).");
    expect(action(2)).toBe("UpdateChanges: City");
    expect(listReads(requests, "/customers")).toBe(1);
    expect(reads).toHaveBeenCalledTimes(1);

    // And back, and forward again: nothing is read or fetched a second time.
    await chooseMode(user, "Add new records only");
    await screen.findByText("Your file is missing required columns, so nothing can be imported.");
    await chooseMode(user, "Add new and update existing");
    await screen.findByText("2 rows ready to import (2 to update).");
    expect(listReads(requests, "/customers")).toBe(1);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(writesOf(requests)).toEqual([]);

    await runImport(user, 2);
    expect(writesOf(requests).map((w) => w.request)).toEqual(["PATCH /customers/comp-1", "PATCH /customers/comp-3"]);
  }, 20_000);

  it("keeps a company with the rep who has left, and refuses to give one to them or to nobody", async () => {
    const requests = stubOrganisation({
      lists: {
        "/customers": [
          companyWire("comp-1", "Northwind Logistics", { owner_id: "own-3", city: "Mumbai" }),
          companyWire("comp-2", "Cobalt Financial", { owner_id: "own-2" }),
          companyWire("comp-4", "Aoki Freight", { owner_id: "own-2" }),
        ],
      },
      answers: { "POST /customers": () => ({ status: 201, body: companyWire("comp-new-1", "Newbie Co") }) },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "companies");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        companyImport,
        ["id", "name", "owner", "city"],
        [
          // 2: an export writes the rep who has left by email; repeating them is not a reassignment.
          ["comp-1", null, "former@example.com", "Pune"],
          // 3: giving a company to a rep who has left.
          ["comp-2", null, "former@example.com", null],
          // 4: an owner nobody has: the hint for a row that updates says blank keeps the owner.
          ["comp-4", null, "nobody@example.com", null],
          // 5: a new company with an owner nobody has: the hint for adding says blank assigns it to you.
          [null, "Newbie Co", "nobody@example.com", null],
        ],
        "owners.xlsx",
      ),
    );

    expect(action(2)).toBe("UpdateChanges: City");
    expect(action(3)).toBe(`Needs fixing${notAnOwner("former@example.com", OWNER_UPDATE_HINT)}`);
    expect(action(4)).toBe(`Needs fixing${notAnOwner("nobody@example.com", OWNER_UPDATE_HINT)}`);
    expect(action(5)).toBe(`Needs fixing${notAnOwner("nobody@example.com", OWNER_ADD_HINT)}`);

    await runImport(user, 1);
    expect(writesOf(requests)).toEqual([{ request: "PATCH /customers/comp-1", body: { city: "Pune" } }]);
    expect(screen.getByText("1 updated, 3 failed.")).toBeTruthy();
  }, 20_000);
});

/* ------------------------------- the sample sheet ------------------------------- */

describe("the sample sheet's update rules", () => {
  async function instructionLines<Payload, Item, Filters>(
    entity: ImportEntity<Payload, Item, Filters>,
    filters: Filters,
  ): Promise<unknown[]> {
    stubOrganisation({ lists: { "/customers": ORGANISATION, "/contacts": [] } });
    const user = userEvent.setup();
    render(<Harness entity={entity} filters={filters} onImported={onImported} />);
    await openDialog(user, entity.noun);
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    await waitFor(() => expect(downloads).toHaveLength(1), { timeout: 5_000 });
    const workbook = await readBlob(present(downloads[0], "the download").blob);
    return valuesOf(sheetOf(workbook, "Instructions")).map((row) => row[0]);
  }

  it("tells a user of companies which fields [clear] empties, and that none is fixed", async () => {
    const lines = await instructionLines(companyImport, { search: "", status: "all" });
    expect(lines).toContain("Updating existing companies");
    expect(lines).toContain("Can be emptied with [clear]: Domain, Industry, Headcount, City, Country.");
    expect(lines.some((line) => typeof line === "string" && line.startsWith("Cannot be changed once created:"))).toBe(false);
    // The engine words how a row is matched; the list only says the key is the name and, being a
    // company, that capitals do not count (the server compares company names that way).
    const matching = lines.find((line) => typeof line === "string" && line.startsWith("A row is matched to an existing record by its Company ID"));
    expect(matching).toMatch(/A row without one is matched by Company name, ignoring capitals/);
    expect(matching).toMatch(/A row that matches nothing is added as a new record\./);
  });

  it("tells a user of leads that email and phone cannot be changed and that a lead is found by its ID alone", async () => {
    const lines = await instructionLines(leadImport, { status: "all" });
    expect(lines).toContain("Can be emptied with [clear]: Company, Notes.");
    expect(lines).toContain(
      "Not changed by an update, leave blank: Email, Phone. A row with a value there is refused.",
    );
    expect(lines).toContain(
      "A row is matched to an existing record by its Lead ID (the column an export writes). A row without one is added as a new record.",
    );
  });
});

/* ---------------------------------- contacts ---------------------------------- */

const COMPANIES = [companyWire("comp-1", "Northwind Logistics"), companyWire("comp-2", "Cobalt Financial")];

describe("contacts: update from Excel", () => {
  const CONTACTS = [
    contactWire("cont-1", "asha.rao@northwind.co", "comp-1", { full_name: "Asha Rao", title: "VP Operations" }),
    contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2", { full_name: "Daniel Okafor", title: "Head of Procurement" }),
    contactWire("cont-3", "Priya.Nair@northwind.co", "comp-1", { full_name: "Priya Nair", title: "Analyst" }),
    contactWire("cont-4", "lena.vogel@northwind.co", "comp-1", { full_name: "Lena Vogel", title: "Buyer" }),
    contactWire("cont-5", "sam.khan@cobaltfin.com", "comp-2", { full_name: "Sam Khan" }),
    contactWire("cont-6", "tara.mehta@northwind.co", "comp-1", { full_name: "Tara Mehta" }),
    contactWire("cont-7", "chief@northwind.co", "comp-1", { full_name: "Chidi Eze", title: "Analyst" }),
  ];

  it("sends the changed fields, then the phone on its own route, and reports a phone the server refuses", async () => {
    const requests = stubOrganisation({
      lists: { "/customers": COMPANIES, "/contacts": CONTACTS },
      answers: {
        "PUT /contacts/cont-7/phone": () =>
          problem(422, "Unprocessable Entity", "Request validation failed", [
            { field: "raw_phone", code: "invalid_phone", message: "That is not a valid phone number with a country code" },
          ]),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={contactImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "contacts");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        contactImport,
        ["id", "email", "title", "phone"],
        [
          // 2: by Record ID; the title changes and a number is written.
          ["cont-1", null, "COO", "+91 98765 43210"],
          // 3: by email, no Record ID; only a number, so no PATCH at all.
          [null, "daniel.okafor@cobaltfin.com", null, "+234 802 345 6789"],
          // 4: by email exactly as it is on file (the server compares emails exactly); nothing differs.
          [null, "Priya.Nair@northwind.co", "Analyst", null],
          // 5: the title is emptied.
          ["cont-4", null, "[clear]", null],
          // 6: an address that differs only in capitals from another contact's.
          ["cont-5", "priya.nair@northwind.co", null, null],
          // 7: a number cannot be removed.
          ["cont-6", null, null, "[clear]"],
          // 8: the title changes and the number is refused by the server, after the title was saved.
          ["cont-7", null, "Chief of Staff", "+44 20 7946 0958"],
        ],
        "contacts-update.xlsx",
      ),
    );

    expect(figure("Rows in file")).toBe("7");
    expect(figure("Updates")).toBe("4");
    expect(figure("No change")).toBe("1");
    expect(figure("Need fixing")).toBe("2");
    expect(action(2)).toBe("UpdateChanges: Title, Phone");
    expect(action(3)).toBe("UpdateChanges: Phone");
    expect(action(4)).toBe("No change");
    expect(action(5)).toBe("UpdateChanges: Title");
    expect(action(6)).toBe(
      "Needs fixingA contact with this email is already on file as Priya.Nair@northwind.co; addresses that differ only in capitals are treated as the same person.",
    );
    expect(action(7)).toBe("Needs fixingPhone cannot be cleared: a saved number can be replaced, not removed");
    expect(action(8)).toBe("UpdateChanges: Title, Phone");

    await runImport(user, 4);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /contacts/cont-1", body: { title: "COO" } },
      { request: "PUT /contacts/cont-1/phone", body: { raw_phone: "+919876543210" } },
      { request: "PUT /contacts/cont-2/phone", body: { raw_phone: "+2348023456789" } },
      { request: "PATCH /contacts/cont-4", body: { title: "" } },
      { request: "PATCH /contacts/cont-7", body: { title: "Chief of Staff" } },
      { request: "PUT /contacts/cont-7/phone", body: { raw_phone: "+442079460958" } },
    ]);

    expect(screen.getByText("3 updated, 1 unchanged, 3 failed.")).toBeTruthy();
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not imported" }))).toEqual([
      [
        "6",
        "sam.khan@cobaltfin.com",
        "sam.khan@cobaltfin.com",
        "Invalid",
        "A contact with this email is already on file as Priya.Nair@northwind.co; addresses that differ only in capitals are treated as the same person.",
      ],
      [
        "7",
        "tara.mehta@northwind.co",
        "tara.mehta@northwind.co",
        "Invalid",
        "Phone cannot be cleared: a saved number can be replaced, not removed",
      ],
      [
        "8",
        "chief@northwind.co",
        "chief@northwind.co",
        "Failed",
        "Request validation failed (raw_phone: That is not a valid phone number with a country code) Saved: Title. Not saved: Phone.",
      ],
    ]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: 0, updated: 3, unchanged: 1, failed: 1, invalid: 2 });
  }, 30_000);
});

/* ------------------------------------ leads ------------------------------------ */

describe("leads: update from Excel", () => {
  const LEADS = [
    leadWire("lead-1", "Meera Iyer", { company: "Halcyon Textiles", owner_id: "own-2", notes: "Asked for a demo.", status: "working" }),
    leadWire("lead-2", "Tomas Berg", {
      owner_id: "own-1",
      status: "converted",
      converted_company_id: "comp-1",
      converted_deal_id: "deal-1",
      converted_at: "2026-09-25T09:00:00Z",
    }),
    leadWire("lead-3", "Ines Costa", { owner_id: null }),
    leadWire("lead-4", "Omar Haddad", { company: "Haddad Trading" }),
  ];

  it("finds a lead by its Lead ID only, never touches its email or phone, and keeps a converted lead's owner", async () => {
    const requests = stubOrganisation({
      lists: { "/leads": LEADS },
      answers: { "POST /leads": () => ({ status: 201, body: leadWire("lead-new-1", "Kiran Bose") }) },
    });
    const user = userEvent.setup();
    render(<Harness entity={leadImport} filters={{ status: "all" }} onImported={onImported} />);
    await openDialog(user, "leads");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        leadImport,
        ["id", "fullName", "company", "owner", "notes", "email"],
        [
          // 2: the company is emptied and the notes rewritten.
          ["lead-1", null, "[clear]", null, "Called back", null],
          // 3: an owner for a lead that has none.
          ["lead-3", null, null, "sofia@example.com", null, null],
          // 4: another owner for a converted lead.
          ["lead-2", null, null, "Grace@Example.com", null, null],
          // 5: a converted lead keeps its name, company and notes editable.
          ["lead-2", "Tomas Berg-Holm", null, null, null, null],
          // 6: an email cannot be updated, whatever it says.
          ["lead-4", null, null, null, null, "omar@haddadtrading.example"],
          // 7: no Lead ID: a new lead, as when adding.
          [null, "Kiran Bose", "Bose Foods", null, "Met at the expo", null],
        ],
        "leads-update.xlsx",
      ),
    );

    expect(figure("Rows in file")).toBe("6");
    expect(figure("New records")).toBe("1");
    expect(figure("Updates")).toBe("3");
    expect(figure("Need fixing")).toBe("2");
    expect(action(2)).toBe("UpdateChanges: Company, Notes");
    expect(action(3)).toBe("UpdateChanges: Owner email");
    expect(action(4)).toBe(
      "Needs fixingA converted lead cannot be reassigned. Its Full name, Company and Notes can still be changed.",
    );
    expect(action(5)).toBe("UpdateChanges: Full name");
    expect(action(6)).toMatch(/^Needs fixingEmail cannot be changed/);
    expect(action(7)).toBe("New");

    await runImport(user, 4);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /leads/lead-1", body: { company: null, notes: "Called back" } },
      { request: "PATCH /leads/lead-3", body: { owner_id: "own-1" } },
      { request: "PATCH /leads/lead-2", body: { full_name: "Tomas Berg-Holm" } },
      {
        request: "POST /leads",
        body: { full_name: "Kiran Bose", company: "Bose Foods", email: null, phone: null, source: "import", notes: "Met at the expo" },
      },
    ]);
    expect(screen.getByText("3 updated, 1 created, 2 failed.")).toBeTruthy();
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
  }, 30_000);

  it("treats a lead that points at the company it became as converted, though its status does not say so", async () => {
    // The server refuses an owner on a lead with `converted_company_id` set, not only one with the status.
    const requests = stubOrganisation({
      lists: {
        "/leads": [
          leadWire("lead-5", "Nadia Rahman", { status: "qualified", owner_id: "own-1", converted_company_id: "comp-1" }),
          leadWire("lead-6", "Osei Mensah", { status: "qualified", owner_id: "own-1", converted_company_id: "comp-1" }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={leadImport} filters={{ status: "all" }} onImported={onImported} />);
    await openDialog(user, "leads");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        leadImport,
        ["id", "fullName", "owner"],
        [
          ["lead-5", null, "Grace@Example.com"],
          ["lead-6", "Osei Mensah-Boateng", null],
        ],
        "leads-converted.xlsx",
      ),
    );

    expect(action(2)).toBe(
      "Needs fixingA converted lead cannot be reassigned. Its Full name, Company and Notes can still be changed.",
    );
    expect(action(3)).toBe("UpdateChanges: Full name");

    await runImport(user, 1);

    expect(writesOf(requests)).toEqual([{ request: "PATCH /leads/lead-6", body: { full_name: "Osei Mensah-Boateng" } }]);
  }, 20_000);
});

/* ------------------------------------ deals ------------------------------------ */

describe("deals: update from Excel", () => {
  const CONTACTS = [
    contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
    contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
  ];
  const DEALS = [
    dealWire("deal-1", "Northwind fleet renewal", {
      company_id: "comp-1",
      primary_contact_id: "cont-1",
      stage: "proposal",
      probability: 55,
      value_minor: 320000000,
      source: "Referral",
    }),
    dealWire("deal-2", "Cobalt treasury platform", { company_id: "comp-2", primary_contact_id: "cont-2", stage: "lead", probability: 20 }),
    dealWire("deal-3", "Harbor expansion", { company_id: "comp-1", stage: "closed_won", probability: 100 }),
    dealWire("deal-4", "Delta rollout", { company_id: "comp-1", stage: "qualified", probability: 40 }),
    // A yen deal: 500 minor units are 500 yen.
    dealWire("deal-5", "Kobe pilot", { company_id: "comp-1", currency: "JPY", value_minor: 500 }),
    dealWire("deal-6", "Umbra analytics", { company_id: "comp-2", primary_contact_id: "cont-2" }),
    dealWire("deal-7", "Sigma sale", { company_id: "comp-1", primary_contact_id: "cont-1" }),
  ];
  const KEYS = ["id", "company", "contact", "value", "currency", "stage", "probability", "source"] as const;

  it("sends the changed fields, then the stage move, repeating a probability the row changes, and nothing for what cannot move", async () => {
    const requests = stubOrganisation({ lists: { "/customers": COMPANIES, "/contacts": CONTACTS, "/deals": DEALS } });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        dealImport,
        KEYS,
        [
          // 2: a value, a source and a stage: the PATCH, then the stage move alone. The row gives no
          // probability, so the server resets it to Negotiation's usual 70, and the preview says so.
          ["deal-1", null, null, 450000, null, "Negotiation", null, "Outbound"],
          // 3: a stage and a different probability, nothing else: only the stage move, carrying the
          // probability, so the move does not reset it.
          ["deal-2", null, null, null, null, "Qualified", 45, null],
          // 4: a probability alone: a PATCH, no stage move.
          ["deal-4", null, null, null, null, null, 70, null],
          // 5: a closed deal cannot move.
          ["deal-3", null, null, null, null, "Lead", null, null],
          // 6: a yen deal, an amount and no currency: read in yen, no currency is sent.
          ["deal-5", null, null, 800, null, null, null, null],
          // 7: another company while keeping a contact who works at the old one.
          ["deal-6", "Northwind Logistics", null, null, null, null, null, null],
          // 8: the primary contact is removed.
          ["deal-7", null, "[clear]", null, null, null, null, null],
        ],
        "deals-update.xlsx",
      ),
    );

    expect(figure("Rows in file")).toBe("7");
    expect(figure("Updates")).toBe("5");
    expect(figure("Need fixing")).toBe("2");
    expect(action(2)).toBe("UpdateChanges: Deal value, Source, Stage, Probability (reset to 70%)");
    expect(action(3)).toBe("UpdateChanges: Stage, Probability (%)");
    expect(action(4)).toBe("UpdateChanges: Probability (%)");
    expect(action(5)).toBe("Needs fixingThis deal is Closed Won, and a closed deal cannot be moved to another stage.");
    expect(action(6)).toBe("UpdateChanges: Deal value");
    expect(action(7)).toBe(
      "Needs fixingdaniel.okafor@cobaltfin.com is the deal's primary contact but is not a contact of Northwind Logistics. " +
        "Name one of that company's contacts in Primary contact email, or type [clear] to remove the primary contact.",
    );
    expect(action(8)).toBe("UpdateChanges: Primary contact email");

    await runImport(user, 5);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /deals/deal-1", body: { value_minor: 45000000, source: "Outbound" } },
      { request: "POST /deals/deal-1/stage", body: { stage: "negotiation" } },
      { request: "POST /deals/deal-2/stage", body: { stage: "qualified", probability: 45 } },
      { request: "PATCH /deals/deal-4", body: { probability: 70 } },
      { request: "PATCH /deals/deal-5", body: { value_minor: 800 } },
      { request: "PATCH /deals/deal-7", body: { primary_contact_id: null } },
    ]);
    expect(screen.getByText("5 updated, 2 failed.")).toBeTruthy();
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: 0, updated: 5, unchanged: 0, failed: 0, invalid: 2 });
  }, 30_000);

  it("reports a stage move the server refuses after the PATCH before it was saved", async () => {
    const requests = stubOrganisation({
      lists: { "/customers": COMPANIES, "/contacts": CONTACTS, "/deals": DEALS },
      answers: {
        "POST /deals/deal-1/stage": () => problem(409, "Conflict", "A deal in 'proposal' cannot move to 'lead'"),
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(dealImport, KEYS, [["deal-1", null, null, null, null, "Lead", null, "Outbound"]], "deal.xlsx"),
    );
    expect(action(2)).toBe("UpdateChanges: Source, Stage, Probability (reset to 20%)");

    await runImport(user, 1);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /deals/deal-1", body: { source: "Outbound" } },
      { request: "POST /deals/deal-1/stage", body: { stage: "lead" } },
    ]);
    expect(figure("Failed")).toBe("1");
    expect(figure("Updated")).toBe("0");
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not imported" }))).toEqual([
      [
        "2",
        "Northwind fleet renewal",
        "Northwind fleet renewal",
        "Failed",
        "A deal in 'proposal' cannot move to 'lead' Saved: Source. Not saved: Stage, Probability (reset to 20%).",
      ],
    ]);
  }, 20_000);

  it("says what a stage move does to the probability, and that closing a deal cannot be undone", async () => {
    const requests = stubOrganisation({ lists: { "/customers": COMPANIES, "/contacts": CONTACTS, "/deals": DEALS } });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        dealImport,
        KEYS,
        [
          // 2: an export's own probability cell (55, as on file) beside a new stage: not a choice, so
          // the move resets it, and the preview says to what.
          ["deal-1", null, null, null, null, "Lead", 55, null],
          // 3: a deal is closed; the probability it ends at is said too.
          ["deal-6", null, null, null, null, "Closed Won", null, null],
          // 4: closing as lost with the figure lost always has: accepted, and the move is not asked for one.
          ["deal-2", null, null, null, null, "Closed Lost", 0, null],
          // 5: closing with a figure closing never gives is refused, before anything is sent.
          ["deal-7", null, null, null, null, "Closed Lost", 30, null],
          // 6: a value and a probability, no stage move: the fields, then the probability.
          ["deal-4", null, null, 450000, null, null, 70, null],
        ],
        "deals-stage.xlsx",
      ),
    );

    expect(figure("Updates")).toBe("4");
    expect(figure("Need fixing")).toBe("1");
    expect(action(2)).toBe("UpdateChanges: Stage, Probability (reset to 20%)");
    expect(action(3)).toBe("UpdateChanges: Stage (closes the deal), Probability (reset to 100%)");
    expect(action(4)).toBe("UpdateChanges: Stage (closes the deal), Probability (reset to 0%)");
    expect(action(5)).toBe(
      "Needs fixingClosed Lost always sets Probability (%) to 0, so it cannot be 30 in the same row. Leave the cell as it is, or type 0.",
    );
    expect(action(6)).toBe("UpdateChanges: Deal value, Probability (%)");
    expect(writesOf(requests)).toEqual([]);

    await runImport(user, 4);

    expect(writesOf(requests)).toEqual([
      // Nothing repeats the probability on file to keep it: the server's reset is what the row gets.
      { request: "POST /deals/deal-1/stage", body: { stage: "lead" } },
      // Closing never carries a probability: the server pins 100 (won) or 0 (lost).
      { request: "POST /deals/deal-6/stage", body: { stage: "closed_won" } },
      { request: "POST /deals/deal-2/stage", body: { stage: "closed_lost" } },
      { request: "PATCH /deals/deal-4", body: { value_minor: 45000000 } },
      { request: "PATCH /deals/deal-4", body: { probability: 70 } },
    ]);
    expect(screen.getByText("4 updated, 1 failed.")).toBeTruthy();
    expect(rowsOf(screen.getByRole("region", { name: "Rows that were not imported" }))).toEqual([
      [
        "5",
        "Sigma sale",
        "Invalid",
        "Closed Lost always sets Probability (%) to 0, so it cannot be 30 in the same row. Leave the cell as it is, or type 0.",
      ],
    ]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(present(onImported.mock.calls[0]?.[0], "the summary").outcomes.map((o) => [o.row, o.status, o.changes])).toEqual([
      [2, "updated", ["Stage", "Probability (reset to 20%)"]],
      [3, "updated", ["Stage (closes the deal)", "Probability (reset to 100%)"]],
      [4, "updated", ["Stage (closes the deal)", "Probability (reset to 0%)"]],
      [5, "invalid", undefined],
      [6, "updated", ["Deal value", "Probability (%)"]],
    ]);
  }, 30_000);

  it("refuses to leave a deal with a contact nobody can check when it moves to another company, unless the row names or removes one", async () => {
    // The deals' contact is not among the contacts this caller can see (a colleague's, or deleted).
    const hidden = { company_id: "comp-1", primary_contact_id: "cont-hidden" };
    const requests = stubOrganisation({
      lists: {
        "/customers": COMPANIES,
        "/contacts": CONTACTS,
        "/deals": [
          dealWire("deal-1", "Orion", hidden),
          dealWire("deal-2", "Pegasus", hidden),
          dealWire("deal-3", "Quasar", hidden),
          dealWire("deal-4", "Rigel", hidden),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        dealImport,
        KEYS,
        [
          // 2: another company, the contact left as it is: it cannot be judged.
          ["deal-1", "Cobalt Financial", null, null, null, null, null, null],
          // 3: another company and one of its contacts.
          ["deal-2", "Cobalt Financial", "daniel.okafor@cobaltfin.com", null, null, null, null, null],
          // 4: another company and the contact removed.
          ["deal-3", "Cobalt Financial", "[clear]", null, null, null, null, null],
          // 5: no other company: the contact is not judged.
          ["deal-4", null, null, 450000, null, null, null, null],
        ],
        "deals-hidden-contact.xlsx",
      ),
    );

    expect(action(2)).toBe(
      "Needs fixingThe deal's primary contact is not among the contacts you can see, so it cannot be checked against Cobalt Financial. " +
        "Name one of that company's contacts in Primary contact email, or type [clear] to remove the primary contact.",
    );
    expect(action(3)).toBe("UpdateChanges: Company, Primary contact email");
    expect(action(4)).toBe("UpdateChanges: Company, Primary contact email");
    expect(action(5)).toBe("UpdateChanges: Deal value");

    await runImport(user, 3);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /deals/deal-2", body: { company_id: "comp-2", primary_contact_id: "cont-2" } },
      { request: "PATCH /deals/deal-3", body: { company_id: "comp-2", primary_contact_id: null } },
      { request: "PATCH /deals/deal-4", body: { value_minor: 45000000 } },
    ]);
    expect(screen.getByText("3 updated, 1 failed.")).toBeTruthy();
  }, 30_000);

  it("accepts the contact a deal was exported with even when another contact's address differs from it only in capitals", async () => {
    const requests = stubOrganisation({
      lists: {
        "/customers": COMPANIES,
        // The server keeps these two apart: it compares emails exactly.
        "/contacts": [
          contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
          contactWire("cont-9", "Asha.Rao@northwind.co", "comp-1"),
        ],
        "/deals": [
          dealWire("deal-1", "Orion", { company_id: "comp-1", primary_contact_id: "cont-1" }),
          dealWire("deal-2", "Pegasus", { company_id: "comp-1", primary_contact_id: "cont-9" }),
          dealWire("deal-3", "Quasar", { company_id: "comp-1", primary_contact_id: "cont-1" }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        dealImport,
        KEYS,
        [
          // 2 and 3: each deal's own contact, spelled as the export writes it, beside a new value.
          ["deal-1", null, "asha.rao@northwind.co", 450000, null, null, null, null],
          ["deal-2", null, "Asha.Rao@northwind.co", 450000, null, null, null, null],
          // 4: the other twin's exact spelling: that contact is named, so it is the deal's new contact.
          ["deal-3", null, "Asha.Rao@northwind.co", null, null, null, null, null],
        ],
        "deals-twin-contacts.xlsx",
      ),
    );

    expect(action(2)).toBe("UpdateChanges: Deal value");
    expect(action(3)).toBe("UpdateChanges: Deal value");
    expect(action(4)).toBe("UpdateChanges: Primary contact email");

    await runImport(user, 3);

    expect(writesOf(requests)).toEqual([
      { request: "PATCH /deals/deal-1", body: { value_minor: 45000000 } },
      { request: "PATCH /deals/deal-2", body: { value_minor: 45000000 } },
      { request: "PATCH /deals/deal-3", body: { primary_contact_id: "cont-9" } },
    ]);
  }, 30_000);

  it("still refuses a spelling of that address that neither contact has, as ambiguous", async () => {
    stubOrganisation({
      lists: {
        "/customers": COMPANIES,
        "/contacts": [
          contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
          contactWire("cont-9", "Asha.Rao@northwind.co", "comp-1"),
        ],
        "/deals": [dealWire("deal-1", "Orion", { company_id: "comp-1", primary_contact_id: "cont-1" })],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(dealImport, KEYS, [["deal-1", null, "ASHA.RAO@northwind.co", null, null, null, null, null]], "deals-ambiguous.xlsx"),
    );
    expect(action(2)).toMatch(
      /^Needs fixing'ASHA\.RAO@northwind\.co' matches more than one of the accepted values for Primary contact email, so it cannot be chosen\./,
    );
  }, 20_000);
});

/* ------------------- how a row finds its record, and how the report names it ------------------- */

/**
 * The key rules the lists declare (`UpdateSpec.keyMatch`) and the words each list gives for a stored
 * record (`UpdateSpec.recordLabel`), through the engine that applies them: company names match
 * ignoring capitals, as the server does, and every other key exactly.
 */
describe("update from Excel: how a row finds its record, and how the report names it", () => {
  const summaryOf = (): ImportSummary => present(onImported.mock.calls[0]?.[0], "the summary");
  const recordsOf = (summary: ImportSummary): [number, string | undefined][] =>
    summary.outcomes.map((outcome) => [outcome.row, outcome.record]);

  it("finds a company by its name in other capitals, and treats a name that differs in its spaces as a new company", async () => {
    const requests = stubOrganisation({
      lists: { "/customers": ORGANISATION },
      answers: { "POST /customers": () => ({ status: 201, body: companyWire("comp-new-1", "Cobalt  Financial") }) },
    });
    const user = userEvent.setup();
    render(<Harness entity={companyImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "companies");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        companyImport,
        ["name", "city"],
        [
          // 2: the same company in other capitals: the server sees one company, so this is it.
          ["NORTHWIND LOGISTICS", "Pune"],
          // 3: a run of spaces the server keeps: another company to it.
          ["Cobalt  Financial", "Delhi"],
        ],
        "companies-names.xlsx",
      ),
    );

    expect(action(2)).toBe("UpdateChanges: City");
    expect(action(3)).toBe("New");

    await runImport(user, 2);

    expect(writesOf(requests).map((write) => write.request)).toEqual(["PATCH /customers/comp-1", "POST /customers"]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    // The row that found its company says which company it changed, by name.
    expect(recordsOf(summaryOf())).toEqual([
      [2, "Northwind Logistics"],
      [3, undefined],
    ]);
  }, 20_000);

  it("refuses a deal name in other capitals, naming the deal it resembles, and finds the exact one", async () => {
    const requests = stubOrganisation({
      lists: {
        "/customers": COMPANIES,
        "/contacts": [],
        "/deals": [dealWire("deal-1", "Northwind fleet renewal"), dealWire("deal-2", "Cobalt treasury platform")],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={dealImport} filters={{ search: "", stage: "all", ownerId: "all" }} onImported={onImported} />);
    await openDialog(user, "deals");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        dealImport,
        ["name", "value"],
        [
          // 2: exactly as stored.
          ["Northwind fleet renewal", 450000],
          // 3: other capitals: the server counts a different deal, so the row is refused, not matched.
          ["COBALT treasury platform", 450000],
        ],
        "deals-names.xlsx",
      ),
    );

    expect(action(2)).toBe("UpdateChanges: Deal value");
    expect(action(3)).toBe(
      "Needs fixingA record 'Cobalt treasury platform' exists that differs from 'COBALT treasury platform' only in capitals or spacing. " +
        "Use its exact spelling, or its Deal ID.",
    );

    await runImport(user, 1);

    expect(writesOf(requests)).toEqual([{ request: "PATCH /deals/deal-1", body: { value_minor: 45000000 } }]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(recordsOf(summaryOf())).toEqual([
      [2, "Northwind fleet renewal"],
      [3, undefined],
    ]);
  }, 20_000);

  it("refuses a contact email in other capitals, as another contact to the server, and names the contact by name", async () => {
    const requests = stubOrganisation({
      lists: {
        "/customers": COMPANIES,
        "/contacts": [
          contactWire("cont-1", "Asha.Rao@northwind.co", "comp-1", { full_name: "Asha Rao", title: "VP" }),
          contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2", { full_name: "Daniel Okafor" }),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={contactImport} filters={{ search: "", status: "all" }} onImported={onImported} />);
    await openDialog(user, "contacts");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(
        contactImport,
        ["email", "title"],
        [
          // 2: exactly as stored.
          ["Asha.Rao@northwind.co", "COO"],
          // 3: other capitals.
          ["daniel.OKAFOR@cobaltfin.com", "Head"],
        ],
        "contacts-emails.xlsx",
      ),
    );

    expect(action(2)).toBe("UpdateChanges: Title");
    expect(action(3)).toMatch(/^Needs fixing/);

    await runImport(user, 1);

    expect(writesOf(requests)).toEqual([{ request: "PATCH /contacts/cont-1", body: { title: "COO" } }]);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    // A contact is named by their name, never their phone.
    expect(recordsOf(summaryOf())).toEqual([
      [2, "Asha Rao"],
      [3, undefined],
    ]);
  }, 20_000);

  it("names a lead by their name and company, never by the email or phone the wire only masks", async () => {
    stubOrganisation({
      lists: {
        "/leads": [
          leadWire("lead-1", "Meera Iyer", { company: "Halcyon Textiles", email: "m•••@h•••.in", phone: "+•• ••••• ••345" }),
          leadWire("lead-2", "Tomas Berg"),
        ],
      },
    });
    const user = userEvent.setup();
    render(<Harness entity={leadImport} filters={{ status: "all" }} onImported={onImported} />);
    await openDialog(user, "leads");
    await chooseMode(user, "Add new and update existing");
    await chooseFile(
      user,
      await sheetOfColumns(leadImport, ["id", "notes"], [["lead-1", "Called"], ["lead-2", "Emailed"]], "leads-notes.xlsx"),
    );
    await runImport(user, 2);

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(recordsOf(summaryOf())).toEqual([
      [2, "Meera Iyer (Halcyon Textiles)"],
      [3, "Tomas Berg"],
    ]);
  }, 20_000);
});
