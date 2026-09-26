/**
 * What `importFlow.test.tsx` needs from the outside world, and jsdom does not have: a place for the
 * downloads the engine hands to the browser (`URL.createObjectURL` and a click on an anchor), and
 * reading and writing .xlsx files in memory with exceljs, the way Excel would hand them over.
 */
import ExcelJS from "exceljs";
import { vi } from "vitest";

import type { ImportColumn, SheetValue } from "@opsgrids/client";

export const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export interface Download {
  name: string;
  blob: Blob;
}

/**
 * Records every file the page offers as a download, in order. jsdom has no `URL.createObjectURL`
 * and would try to navigate on a click on an anchor with a `blob:` address, so both are replaced.
 * Call `release` when the test is over.
 */
export function captureDownloads(): { downloads: Download[]; release: () => void } {
  const downloads: Download[] = [];
  const blobs = new Map<string, Blob>();
  URL.createObjectURL = (blob) => {
    const url = `blob:crm/${blobs.size + 1}`;
    if (blob instanceof Blob) blobs.set(url, blob);
    return url;
  };
  URL.revokeObjectURL = () => undefined;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function record(this: HTMLAnchorElement) {
    const blob = blobs.get(this.href);
    if (blob === undefined) throw new Error(`A download of ${this.href} has no file behind it`);
    downloads.push({ name: this.download, blob });
  });
  return {
    downloads,
    release: () => {
      Reflect.deleteProperty(URL, "createObjectURL");
      Reflect.deleteProperty(URL, "revokeObjectURL");
    },
  };
}

/** A blob's bytes. jsdom's `Blob` has no `arrayBuffer()`. */
function bytesOf(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(new Uint8Array(reader.result));
      else reject(new Error("The blob could not be read"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("The blob could not be read"));
    reader.readAsArrayBuffer(blob);
  });
}

/** A downloaded workbook, read back with exceljs so a test can look at its cells. */
export async function readBlob(blob: Blob): Promise<ExcelJS.Workbook> {
  const workbook = new ExcelJS.Workbook();
  // exceljs declares `load` over Node's `Buffer`; at runtime any byte array does.
  await workbook.xlsx.load((await bytesOf(blob)) as unknown as Parameters<ExcelJS.Xlsx["load"]>[0]);
  return workbook;
}

/** One sheet of a workbook; a test that names a sheet that is not there fails loudly. */
export function sheetOf(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`The workbook has no sheet "${name}"`);
  return sheet;
}

/**
 * The drop-down on a cell of a sheet. exceljs reads a range's validation back onto every cell of
 * it, and its type declarations leave the whole facility out.
 */
export function validationAt(sheet: ExcelJS.Worksheet, address: string): ExcelJS.DataValidation | undefined {
  const holder = sheet as unknown as { dataValidations: { model: Record<string, ExcelJS.DataValidation | undefined> } };
  return holder.dataValidations.model[address];
}

/** Every used row of a sheet as the values its cells hold, so a test can compare whole rows. */
export function valuesOf(sheet: ExcelJS.Worksheet): ExcelJS.CellValue[][] {
  const rows: ExcelJS.CellValue[][] = [];
  sheet.eachRow((row) => {
    const values: ExcelJS.CellValue[] = [];
    row.eachCell({ includeEmpty: true }, (cell) => values.push(cell.value));
    rows.push(values);
  });
  return rows;
}

/**
 * The same export with one column's cells emptied (the header stays), as a user would leave it
 * after selecting the column in Excel and clearing its contents.
 */
export async function withColumnCleared(blob: Blob, sheetName: string, header: string, name: string): Promise<File> {
  const workbook = await readBlob(blob);
  const sheet = sheetOf(workbook, sheetName);
  const [headers = []] = valuesOf(sheet);
  const column = headers.indexOf(header) + 1;
  if (column === 0) throw new Error(`The sheet has no column headed "${header}"`);
  sheet.getColumn(column).eachCell({ includeEmpty: false }, (cell, row) => {
    if (row > 1) cell.value = null;
  });
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], name, { type: XLSX_TYPE });
}

/**
 * A workbook file the way a user would have filled in the sample sheet: an "Import" sheet whose
 * header row is every one of the entity's own headers (required ones marked with an asterisk),
 * then one row per entry of `rows`, keyed by column key. A key that is no column of the entity
 * becomes an extra column headed by the key itself, which the importer reports as ignored.
 */
export async function sheetFile(
  columns: readonly ImportColumn[],
  rows: readonly Readonly<Record<string, SheetValue>>[],
  name: string,
): Promise<File> {
  const known = new Set(columns.map((column) => column.key));
  const extras = [...new Set(rows.flatMap((row) => Object.keys(row)).filter((key) => !known.has(key)))];

  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...columns.map((column) => (column.required === true ? `${column.header} *` : column.header)), ...extras]);
  for (const row of rows) {
    sheet.addRow([...columns.map((column) => row[column.key] ?? null), ...extras.map((key) => row[key] ?? null)]);
  }
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], name, { type: XLSX_TYPE });
}
