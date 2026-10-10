/**
 * Reading an import file and writing an export file in the browser (inventory Phase 9,
 * ADR-0027). The server only ever sees JSON rows of strings: this module turns a CSV or XLSX
 * into them, and the export's rows back into a CSV that can never be a formula.
 *
 * Nothing here parses a number. Every cell is the text the person sees, so `82.5` stays `"82.5"`
 * and a SKU with a leading zero keeps it; the server validates and refuses what is not a
 * decimal string.
 */

import type { ImportCells, ImportColumn } from "@/api/scan";

/** The server's cap on rows per import (`MAX_IMPORT_ROWS`). */
export const MAX_IMPORT_ROWS = 5000;
/** A spreadsheet bigger than this is refused before it is read into memory. */
export const MAX_FILE_BYTES = 5 * 1024 * 1024;

export class DataFileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DataFileError";
  }
}

/** `File.text()` / `arrayBuffer()` where they exist, `FileReader` where they do not (jsdom). */
function readFile<T extends string | ArrayBuffer>(file: File, as: "text" | "buffer"): Promise<T> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new DataFileError("That file could not be read"));
    reader.onload = () => resolve(reader.result as T);
    if (as === "text") reader.readAsText(file);
    else reader.readAsArrayBuffer(file);
  });
}

// ------------------------------------------------------------------ CSV

/** RFC 4180: quoted fields, doubled quotes, CRLF or LF, an optional BOM. */
export function parseCsv(text: string): string[][] {
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let touched = false;
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i] as string;
    if (quoted) {
      if (ch === '"') {
        if (source[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"' && cell === "") {
      quoted = true;
      touched = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
      touched = true;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && source[i + 1] === "\n") i += 1;
      row.push(cell);
      if (touched || row.some((c) => c !== "")) rows.push(row);
      row = [];
      cell = "";
      touched = false;
    } else {
      cell += ch;
      touched = true;
    }
  }
  if (quoted) throw new DataFileError("The file has an unclosed quote");
  if (touched || cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

/** A cell is never a formula: prefix `'` unless it is a plain number (a negative quantity). */
export function guardCell(value: string): string {
  if (value !== "" && "=+-@\t\r".includes(value[0] as string) && !/^-?\d+(\.\d+)?$/.test(value)) {
    return `'${value}`;
  }
  return value;
}

function csvCell(value: string): string {
  const guarded = guardCell(value);
  return /[",\r\n]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
}

export function toCsv(columns: readonly string[], rows: readonly Record<string, string>[]): string {
  const lines = [columns.map(csvCell).join(",")];
  for (const row of rows) lines.push(columns.map((c) => csvCell(row[c] ?? "")).join(","));
  return lines.join("\r\n");
}

/** The sample sheet: the entity's header row and one example row, from the server's columns. */
export function sampleCsv(columns: readonly ImportColumn[]): string {
  const example: Record<string, string> = {};
  for (const c of columns) example[c.name] = c.example;
  return toCsv(
    columns.map((c) => c.name),
    [example],
  );
}

export function downloadText(filename: string, content: string, mime = "text/csv"): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8;` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ------------------------------------------------------------------ XLSX

type CellValue = unknown;

/** What a spreadsheet cell shows: the cached result of a formula (never evaluated), a date as
 *  `YYYY-MM-DD`, everything else as its text. */
function cellText(value: CellValue, shown: string): string {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value === "object" && value !== null && "result" in value) {
    const result = (value as { result: unknown }).result;
    if (result instanceof Date) return result.toISOString().slice(0, 10);
    return result === null || result === undefined ? "" : String(result);
  }
  return shown;
}

/** The first sheet of an .xlsx as rows of text. `exceljs` loads only when someone imports one. */
export async function readXlsx(file: File): Promise<string[][]> {
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  try {
    await workbook.xlsx.load(await readFile<ArrayBuffer>(file, "buffer"));
  } catch {
    throw new DataFileError("That file could not be read as an Excel workbook");
  }
  const sheet = workbook.worksheets[0];
  if (!sheet) throw new DataFileError("The workbook has no sheets");
  const rows: string[][] = [];
  sheet.eachRow({ includeEmpty: false }, (row) => {
    const cells: string[] = [];
    const count = Math.min(row.cellCount, 60);
    for (let c = 1; c <= count; c += 1) {
      const cell = row.getCell(c);
      cells.push(cellText(cell.value as CellValue, cell.text ?? ""));
    }
    rows.push(cells);
  });
  return rows;
}

// ------------------------------------------------------------------ rows

export function normaliseHeader(header: string): string {
  return header
    .trim()
    .replace(/\*+$/, "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
}

export interface ReadRows {
  rows: ImportCells[];
  /** Columns in the file the importer does not know (ignored, and named so nobody wonders). */
  ignored: string[];
  /** Required columns the file does not have. */
  missingRequired: string[];
}

/** Maps a table (header row first) onto the entity's columns. Blank rows are dropped. */
export function rowsFromTable(
  table: readonly (readonly string[])[],
  columns: readonly ImportColumn[],
): ReadRows {
  const header = table[0];
  if (!header) throw new DataFileError("The file is empty");
  const known = new Map(columns.map((c) => [c.name, c]));
  const indexOf = new Map<string, number>();
  const ignored: string[] = [];
  header.forEach((raw, index) => {
    const name = normaliseHeader(raw);
    if (name === "") return;
    if (known.has(name) && !indexOf.has(name)) indexOf.set(name, index);
    else if (!known.has(name)) ignored.push(raw.trim());
  });
  const missingRequired = columns
    .filter((c) => c.required && !indexOf.has(c.name))
    .map((c) => c.name);
  const rows: ImportCells[] = [];
  for (const line of table.slice(1)) {
    if (line.every((c) => c.trim() === "")) continue;
    const cells: ImportCells = {};
    for (const [name, index] of indexOf) {
      const value = (line[index] ?? "").trim();
      if (value !== "") cells[name] = value;
    }
    rows.push(cells);
  }
  return { rows, ignored, missingRequired };
}

/** CSV or XLSX, by name; anything else is refused up front. */
export async function readImportFile(
  file: File,
  columns: readonly ImportColumn[],
): Promise<ReadRows> {
  if (file.size > MAX_FILE_BYTES) {
    throw new DataFileError("That file is over 5 MB; split it and import the parts");
  }
  const lower = file.name.toLowerCase();
  let table: string[][];
  if (lower.endsWith(".csv") || lower.endsWith(".txt")) table = parseCsv(await readFile<string>(file, "text"));
  else if (lower.endsWith(".xlsx")) table = await readXlsx(file);
  else throw new DataFileError("Import a .csv or .xlsx file");
  const read = rowsFromTable(table, columns);
  if (read.rows.length === 0) throw new DataFileError("The file has a header but no rows");
  if (read.rows.length > MAX_IMPORT_ROWS) {
    throw new DataFileError(
      `The file has ${read.rows.length} rows; one import takes at most ${MAX_IMPORT_ROWS}`,
    );
  }
  return read;
}
