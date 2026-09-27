/**
 * Reads an .xlsx into rows of `CellInput` keyed by column, and nothing more: no coercion, no
 * network. Everything the file could do to us is decided here: how big it is (packed, then
 * unpacked, before the spreadsheet library is even loaded), which sheet, which headers, and how
 * many rows. What a cell is lives in `cellValue.ts`.
 *
 * Hidden rows and columns are read like any others: hiding is presentation, and the user chose
 * this file.
 */

import type * as ExcelJS from "exceljs";

import { BASELINE_SHEET, readBaseline } from "./baseline";
import type { Baseline } from "./baseline";
import { InvalidCell, displayValue, headerKey, importableColumns, isBlank } from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import { fromExcelValue, readCell } from "./cellValue";
import { loadExcel, loadableBytes, readBytes } from "./excel";
import type { ImportColumn } from "./types";
import { inspectArchive } from "./zipGuard";

export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_COLUMNS = 60;
export const DEFAULT_MAX_ROWS = 1000;
/** Rows an export writes at most: also the most records a baseline may hold, since the file that
 *  comes back may have had most of its rows deleted but keeps the record of them all. */
export const DEFAULT_EXPORT_MAX_ROWS = 10_000;

/** The file cannot be imported at all; the message is for the dialog's error state. */
export class WorkbookReadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkbookReadError";
  }
}

export interface SheetRow {
  /** The row's number in the sheet (the header is row 1, so data starts at 2). */
  row: number;
  /** Importable columns by `ImportColumn.key`, and the entity's `recordIdKey` column when the
   *  header row has it (an export's own id, read only to recognise a row that came from one). */
  cells: Readonly<Record<string, CellInput>>;
}

export interface ParsedSheet {
  sheetName: string;
  /** The importable columns the header row carries, in sheet order. */
  columns: readonly ImportColumn[];
  /** Data rows with at least one value; blank rows are skipped but keep their sheet numbers. */
  rows: readonly SheetRow[];
  /** Headers that match no column of the entity (ignored). */
  unknownHeaders: readonly string[];
  /** Headers of required columns the file lacks. */
  missingRequired: readonly string[];
  /** What the export this file came from wrote in every editable cell, when the file still has it. */
  baseline: Baseline;
}

const RESAVE_HINT = "Open it in Excel and choose Save As → Excel Workbook (.xlsx), then choose the new file.";

function unreadable(file: File): WorkbookReadError {
  return new WorkbookReadError(
    `"${file.name}" could not be read as an Excel workbook. If it is password-protected, remove the password and try again.`,
  );
}

function tooLargeUnpacked(): WorkbookReadError {
  return new WorkbookReadError(
    "This file is much larger when unpacked than an import can safely read. Save it again in Excel without images or extra data, or split it into smaller files.",
  );
}

function checkFile(file: File): void {
  if (!/\.xlsx$/i.test(file.name)) {
    throw new WorkbookReadError(`"${file.name}" is not an .xlsx file. ${RESAVE_HINT}`);
  }
  if (file.size === 0) throw new WorkbookReadError(`"${file.name}" is empty.`);
  if (file.size > MAX_FILE_BYTES) {
    const megabytes = (file.size / (1024 * 1024)).toFixed(1);
    throw new WorkbookReadError(
      `"${file.name}" is ${megabytes} MB and the limit is 5 MB. Remove unused rows and columns, or split the file.`,
    );
  }
}

function sheetLabel(name: string): string {
  return name.trim().toLowerCase();
}

/** "Import" if there is one, else the first visible sheet that is not a helper sheet. */
function pickSheet(workbook: ExcelJS.Workbook): ExcelJS.Worksheet | undefined {
  const visible = workbook.worksheets.filter((sheet) => sheet.state === "visible");
  return (
    visible.find((sheet) => sheetLabel(sheet.name) === "import") ??
    visible.find((sheet) => !["instructions", "lists", sheetLabel(BASELINE_SHEET)].includes(sheetLabel(sheet.name)))
  );
}

interface HeaderMap {
  mapped: { col: number; column: ImportColumn }[];
  /** The column holding an export's record id, when the entity names one and the file has it. */
  recordId: { col: number; key: string } | null;
  unknown: string[];
  ignored: number;
}

function mapHeaders(sheet: ExcelJS.Worksheet, entity: AnyEntity): HeaderMap {
  const found: { col: number; text: string }[] = [];
  sheet.getRow(1).eachCell({ includeEmpty: false }, (cell, col) => {
    const value = fromExcelValue(cell.value);
    const text = value instanceof InvalidCell ? "" : displayValue(value);
    if (text !== "") found.push({ col, text });
  });
  if (found.length === 0) {
    throw new WorkbookReadError(
      "The first row of the sheet must hold the column headers. Start from the sample sheet.",
    );
  }
  const widest = found.reduce((most, header) => Math.max(most, header.col), 0);
  if (widest > MAX_COLUMNS) {
    throw new WorkbookReadError(
      `The sheet uses ${widest} columns and the limit is ${MAX_COLUMNS}. Remove the columns the sample sheet does not have.`,
    );
  }

  const known = new Map(entity.columns.map((column) => [headerKey(column.header), column]));
  const seen = new Set<string>();
  const result: HeaderMap = { mapped: [], recordId: null, unknown: [], ignored: 0 };
  for (const { col, text } of found) {
    const key = headerKey(text);
    if (key === "") continue;
    if (seen.has(key)) {
      throw new WorkbookReadError(`Two columns are headed "${text}". Remove or rename one of them.`);
    }
    seen.add(key);
    const column = known.get(key);
    if (column === undefined) result.unknown.push(text);
    else if (column.readOnly === true) {
      result.ignored += 1;
      // Read-only columns are ignored, except the one that says a row came out of the system.
      if (column.key === entity.recordIdKey) result.recordId = { col, key: column.key };
    } else result.mapped.push({ col, column });
  }
  return result;
}

/**
 * Reads the workbook the user chose. Throws `WorkbookReadError` (with a sentence for the user)
 * for a wrong type, an oversized file, an unreadable workbook, no usable sheet, headers that
 * match nothing, or more rows than `entity.maxRows`.
 */
export async function readWorkbook(file: File, entity: AnyEntity): Promise<ParsedSheet> {
  checkFile(file);
  const bytes = await readBytes(file);
  // Before the library is loaded, let alone asked to unpack anything.
  const archive = inspectArchive(bytes);
  if (archive === "malformed") throw unreadable(file);
  if (archive === "too-large") throw tooLargeUnpacked();

  const excel = await loadExcel();
  const workbook = new excel.Workbook();
  try {
    await workbook.xlsx.load(loadableBytes(bytes));
  } catch {
    throw unreadable(file);
  }

  const sheet = pickSheet(workbook);
  if (sheet === undefined) {
    throw new WorkbookReadError('The workbook has no sheet to import. Put the rows on a sheet named "Import".');
  }

  const headers = mapHeaders(sheet, entity);
  if (headers.mapped.length === 0 && headers.ignored === 0) {
    throw new WorkbookReadError(
      "None of the headers in the first row match this list's columns. Download the sample sheet and use its header row.",
    );
  }

  const rows: SheetRow[] = [];
  sheet.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (rowNumber === 1) return;
    const cells: Record<string, CellInput> = {};
    let hasValue = false;
    for (const { col, column } of headers.mapped) {
      const value = readCell(row.getCell(col));
      cells[column.key] = value;
      if (!isBlank(value)) hasValue = true;
    }
    if (headers.recordId !== null) {
      const value = readCell(row.getCell(headers.recordId.col));
      cells[headers.recordId.key] = value;
      if (!isBlank(value)) hasValue = true;
    }
    if (hasValue) rows.push({ row: rowNumber, cells });
  });

  const limit = entity.maxRows ?? DEFAULT_MAX_ROWS;
  if (rows.length > limit) {
    throw new WorkbookReadError(
      `The file has ${rows.length} rows and the limit is ${limit} per file. Split it into files of up to ${limit} rows and import them one at a time.`,
    );
  }
  if (rows.length === 0) {
    throw new WorkbookReadError("There are no rows to import below the header row.");
  }

  const present = new Set(headers.mapped.map(({ column }) => column.key));
  return {
    sheetName: sheet.name,
    columns: headers.mapped.map(({ column }) => column),
    rows,
    unknownHeaders: headers.unknown,
    missingRequired: importableColumns(entity)
      .filter((column) => column.required === true && !present.has(column.key))
      .map((column) => column.header),
    baseline: readBaseline(workbook, entity, {
      maxRecords: entity.export?.maxRows ?? DEFAULT_EXPORT_MAX_ROWS,
      maxColumns: MAX_COLUMNS,
    }),
  };
}
