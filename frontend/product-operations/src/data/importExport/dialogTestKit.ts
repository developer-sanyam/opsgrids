/**
 * Test support for driving the REAL Import / Export dialog (`ImportExportButton` from
 * `@opsgrids/ui`, wired to the real hook by `DialogHarness`) the way a person does. It fills the
 * gaps jsdom leaves in the browser (reading a `Blob`, object URLs, a download link's click), builds
 * `.xlsx` files in memory with exceljs, reads back the files the engine hands to the browser as
 * downloads, and gives the tests the few queries the dialog needs.
 */
import * as ExcelJS from "exceljs";
import { screen, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";

export const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** A file the engine handed to the browser to save. */
export interface Download {
  fileName: string;
  blob: Blob;
}

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(reader.result);
      else reject(new Error("The blob could not be read"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("The blob could not be read"));
    reader.readAsArrayBuffer(blob);
  });
}

/**
 * Fills what jsdom lacks and a browser has, once per test file (each file gets its own jsdom):
 * `Blob.arrayBuffer` (so `File` reads its bytes), `URL.createObjectURL` and `revokeObjectURL`, and
 * an anchor's `click`, which in jsdom would try to navigate. A click on a download link records the
 * file it would have saved instead. Returns that list; the caller empties it between tests.
 */
export function installBrowserGaps(): Download[] {
  const downloads: Download[] = [];
  const blobs = new Map<string, Blob>();

  Object.defineProperty(Blob.prototype, "arrayBuffer", {
    configurable: true,
    writable: true,
    value(this: Blob): Promise<ArrayBuffer> {
      return readBlob(this);
    },
  });
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: (blob: Blob): string => {
      const url = `blob:opsgrids-test/${blobs.size + 1}`;
      blobs.set(url, blob);
      return url;
    },
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: (): void => undefined,
  });
  Object.defineProperty(HTMLAnchorElement.prototype, "click", {
    configurable: true,
    writable: true,
    value(this: HTMLAnchorElement): void {
      const blob = blobs.get(this.href);
      if (blob !== undefined) downloads.push({ fileName: this.download, blob });
    },
  });
  return downloads;
}

/* ------------------------------ workbooks ------------------------------ */

/** A workbook whose "Import" sheet holds `rows` under `headers`, as the file a person uploads. */
export async function workbookFile(
  fileName: string,
  headers: readonly string[],
  rows: readonly (readonly ExcelJS.CellValue[])[],
): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...headers]);
  for (const row of rows) sheet.addRow([...row]);
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], fileName, { type: XLSX_MIME });
}

/** A file the engine produced, read back with exceljs. */
export async function readDownload(download: Download): Promise<ExcelJS.Workbook> {
  const workbook = new ExcelJS.Workbook();
  const bytes = new Uint8Array(await download.blob.arrayBuffer());
  // exceljs declares `load` over Node's `Buffer`; at runtime it takes any byte array.
  await workbook.xlsx.load(bytes as unknown as Parameters<ExcelJS.Xlsx["load"]>[0]);
  return workbook;
}

/**
 * The downloaded file as a person would take it to a new sheet: the same workbook without one
 * column. Used to hand an export back with its Record ID left behind, which is what a row that is
 * meant to be added again looks like.
 */
export async function fileWithoutColumn(download: Download, sheetName: string, header: string): Promise<File> {
  const workbook = await readDownload(download);
  const sheet = sheetOf(workbook, sheetName);
  const index = headerRow(sheet).indexOf(header);
  if (index === -1) throw new Error(`The sheet "${sheetName}" has no "${header}" column`);
  sheet.spliceColumns(index + 1, 1);
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], download.fileName, { type: XLSX_MIME });
}

export function sheetOf(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`The workbook has no sheet "${name}"`);
  return sheet;
}

/** What a cell shows, row by row, blank cells left out: the sheet as a person reads it. */
export function textRows(sheet: ExcelJS.Worksheet): string[][] {
  const rows: string[][] = [];
  sheet.eachRow((row) => {
    const cells: string[] = [];
    row.eachCell({ includeEmpty: false }, (cell) => cells.push(cell.text));
    rows.push(cells);
  });
  return rows;
}

/** The header row of a sheet, one string per column, empty columns included. */
export function headerRow(sheet: ExcelJS.Worksheet): string[] {
  const headers: string[] = [];
  sheet.getRow(1).eachCell({ includeEmpty: true }, (cell) => headers.push(cell.text));
  return headers;
}

/** What one column of a sheet shows from the header down, blank cells left out. */
export function columnTexts(sheet: ExcelJS.Worksheet, column: number): string[] {
  const texts: string[] = [];
  for (let row = 1; row <= sheet.rowCount; row += 1) {
    const text = sheet.getRow(row).getCell(column).text;
    if (text !== "") texts.push(text);
  }
  return texts;
}

/** 1-based column number to its letter: 1 is A, 27 is AA. */
export function columnLetter(index: number): string {
  let letters = "";
  for (let n = index; n > 0; n = Math.floor((n - 1) / 26)) {
    letters = String.fromCharCode(65 + ((n - 1) % 26)) + letters;
  }
  return letters;
}

/**
 * The drop-down on a column's first data cell. The file stores one range per column; exceljs
 * expands it to a key per cell when it reads the file back. Its type declarations omit the
 * property, hence the narrow cast.
 */
export function validationOn(sheet: ExcelJS.Worksheet, letter: string): ExcelJS.DataValidation | undefined {
  const { model } = (sheet as unknown as { dataValidations: { model: Record<string, ExcelJS.DataValidation | undefined> } })
    .dataValidations;
  return model[`${letter}2`];
}

/** Every formula cell in the workbook, as `Sheet!A1: formula`. Empty means none were written. */
export function formulaCells(workbook: ExcelJS.Workbook): string[] {
  const found: string[] = [];
  workbook.eachSheet((sheet) => {
    sheet.eachRow((row) => {
      row.eachCell({ includeEmpty: false }, (cell) => {
        if (cell.type === ExcelJS.ValueType.Formula) found.push(`${sheet.name}!${cell.address}: ${cell.text}`);
      });
    });
  });
  return found;
}

/* ------------------------------ the dialog ------------------------------ */

/** Clicks the page's Import / Export button and waits for the dialog's menu. */
export async function openDialog(user: UserEvent, noun: string): Promise<HTMLElement> {
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  return screen.findByRole("dialog", { name: `Import / Export ${noun}` });
}

/** Hands a file to the dialog's hidden `.xlsx` input, as choosing or dropping one does. */
export async function uploadFile(user: UserEvent, file: File): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error("The dialog has no file input");
  await user.upload(input, file);
}

/** The figure under a label in one of the dialog's counts ("Ready to import" → "5"). The counts are
 *  a description list: a status badge in a table can carry the same word ("Failed"). */
export function stat(dialog: HTMLElement, label: string): string {
  const term = Array.from(dialog.querySelectorAll("dt")).find((candidate) => candidate.textContent === label);
  if (term === undefined) throw new Error(`The dialog has no count called "${label}"`);
  return term.nextElementSibling?.textContent ?? "";
}

/** The row of a table whose text contains `text`. */
export function rowContaining(table: HTMLElement, text: string): HTMLElement {
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) => candidate.textContent?.includes(text) === true);
  if (row === undefined) throw new Error(`No table row contains "${text}"`);
  return row;
}

/** The JSON a request carried, as an object. */
export function jsonBody(init: RequestInit): Record<string, unknown> {
  const parsed: unknown = JSON.parse(String(init.body));
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("The request body is not a JSON object");
  }
  return Object.fromEntries(Object.entries(parsed));
}
