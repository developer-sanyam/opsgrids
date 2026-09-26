/**
 * Reads an .xlsx into rows of `CellInput` keyed by column, and nothing more: no coercion, no
 * network. Everything the file could do to us is decided here: which sheet, how big, which
 * headers, and what a cell is.
 *
 * A formula cell is never evaluated: its stored result is the value, and an error result is an
 * invalid cell. That is the reading half of the formula-injection rule (ADR-0022).
 */

import type * as ExcelJS from "exceljs";

import { InvalidCell, displayValue, headerKey, importableColumns, isBlank, isValidDate } from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import { loadExcel, loadableBytes, readBytes } from "./excel";
import type { ImportColumn } from "./types";

export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_COLUMNS = 60;
export const DEFAULT_MAX_ROWS = 1000;

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
  /** Importable columns only, by `ImportColumn.key`. */
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
}

const RESAVE_HINT = "Open it in Excel and choose Save As → Excel Workbook (.xlsx), then choose the new file.";

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

function errorCell(code: string): InvalidCell {
  return new InvalidCell(code, `contains the error ${code}`);
}

function fromValue(value: ExcelJS.CellValue): CellInput {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  if (value instanceof Date) {
    return isValidDate(value) ? value : new InvalidCell("#DATE", "is not a valid date");
  }
  if ("error" in value) return errorCell(value.error);
  if ("richText" in value) return value.richText.map((run) => run.text).join("");
  if ("hyperlink" in value) {
    return typeof value.text === "string" ? value.text : value.hyperlink.replace(/^mailto:/i, "");
  }
  // A formula: take the result the file stored. Evaluating it would run the author's code.
  const { result } = value;
  if (result === undefined) {
    return new InvalidCell(
      "=",
      "holds a formula with no calculated value. Open the file in Excel, save it and choose it again",
    );
  }
  if (typeof result === "object" && !(result instanceof Date)) return errorCell(result.error);
  return result instanceof Date && !isValidDate(result)
    ? new InvalidCell("#DATE", "is not a valid date")
    : result;
}

function sheetLabel(name: string): string {
  return name.trim().toLowerCase();
}

/** "Import" if there is one, else the first visible sheet that is not a helper sheet. */
function pickSheet(workbook: ExcelJS.Workbook): ExcelJS.Worksheet | undefined {
  const visible = workbook.worksheets.filter((sheet) => sheet.state === "visible");
  return (
    visible.find((sheet) => sheetLabel(sheet.name) === "import") ??
    visible.find((sheet) => !["instructions", "lists"].includes(sheetLabel(sheet.name)))
  );
}

interface HeaderMap {
  mapped: { col: number; column: ImportColumn }[];
  unknown: string[];
  ignored: number;
}

function mapHeaders(sheet: ExcelJS.Worksheet, entity: AnyEntity): HeaderMap {
  const found: { col: number; text: string }[] = [];
  sheet.getRow(1).eachCell({ includeEmpty: false }, (cell, col) => {
    const value = fromValue(cell.value);
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
  const result: HeaderMap = { mapped: [], unknown: [], ignored: 0 };
  for (const { col, text } of found) {
    const key = headerKey(text);
    if (key === "") continue;
    if (seen.has(key)) {
      throw new WorkbookReadError(`Two columns are headed "${text}". Remove or rename one of them.`);
    }
    seen.add(key);
    const column = known.get(key);
    if (column === undefined) result.unknown.push(text);
    else if (column.readOnly === true) result.ignored += 1;
    else result.mapped.push({ col, column });
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
  const [excel, bytes] = await Promise.all([loadExcel(), readBytes(file)]);

  const workbook = new excel.Workbook();
  try {
    await workbook.xlsx.load(loadableBytes(bytes));
  } catch {
    throw new WorkbookReadError(
      `"${file.name}" could not be read as an Excel workbook. If it is password-protected, remove the password and try again.`,
    );
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
      const value = fromValue(row.getCell(col).value);
      cells[column.key] = value;
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
  };
}
