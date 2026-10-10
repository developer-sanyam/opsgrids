import { csvCell } from "@opsgrids/client";
import type { ContactImportRow } from "@/api/types";

/**
 * Turn a pasted spreadsheet into the rows `POST /engage/contacts/import` takes.
 *
 * Deliberately minimal — quoted fields with embedded commas or newlines, nothing more exotic. It
 * exists to get a copy-paste from a spreadsheet into rows, not to be a CSV library; anything a
 * real CSV dialect does that this does not is a row the service reports back as `invalid` rather
 * than a shape this file silently drops.
 *
 * A header row is recognised (`email` as the first cell, case-insensitively) and skipped; without
 * one, columns are read in order as `email, name`. Nothing here validates the address itself —
 * that is the writer's job, per row, in `ContactImportResult`.
 *
 * Rows are read across the whole pasted text, not line by line: a quoted field may itself contain
 * a newline (a spreadsheet cell with a line break, pasted as CSV), and treating every `\n` as a row
 * boundary would split that one row into two.
 */

interface RawRow {
  cells: string[];
  /** The 1-based line the row started on in the original paste — before any header or blank rows
   *  are dropped, and unaffected by a newline embedded inside one of its own quoted fields. */
  line: number;
}

/** Tokenizes the whole paste into rows of raw (untrimmed) cells, tracking the source line each row
 *  started on. A quoted `""` is an escaped quote; a `\r\n`, `\n` or `\r` outside quotes ends a row,
 *  the same one inside quotes is literal text within a cell. */
function parseRows(text: string): RawRow[] {
  const rows: RawRow[] = [];
  let cells: string[] = [];
  let current = "";
  let inQuotes = false;
  let hasContent = false;
  let line = 1;
  let rowStartLine = 1;

  function endCell() {
    cells.push(current);
    current = "";
  }

  function endRow() {
    endCell();
    rows.push({ cells, line: rowStartLine });
    cells = [];
    hasContent = false;
  }

  let i = 0;
  while (i < text.length) {
    const char = text[i];
    if (inQuotes) {
      if (char === '"' && text[i + 1] === '"') {
        current += '"';
        i += 2;
        continue;
      }
      if (char === '"') {
        inQuotes = false;
        i += 1;
        continue;
      }
      if (char === "\n") line += 1;
      current += char;
      i += 1;
      continue;
    }
    if (char === '"') {
      inQuotes = true;
      hasContent = true;
      i += 1;
      continue;
    }
    if (char === ",") {
      endCell();
      hasContent = true;
      i += 1;
      continue;
    }
    if (char === "\r" || char === "\n") {
      endRow();
      i += char === "\r" && text[i + 1] === "\n" ? 2 : 1;
      line += 1;
      rowStartLine = line;
      continue;
    }
    current += char;
    hasContent = true;
    i += 1;
  }
  if (hasContent || current !== "" || cells.length > 0) endRow();

  return rows;
}

function isBlankRow(cells: string[]): boolean {
  return cells.length <= 1 && (cells[0] ?? "").trim() === "";
}

export interface ParsedContactRow {
  email: string;
  name: string;
  /** The 1-based line this row was pasted on — the header row and any blank lines above it have
   *  already been accounted for, so this is the line a person would count to in their editor. */
  line: number;
}

/** Same rows as {@link parseContactCsv}, each carrying the original pasted line number — for
 *  reporting results against what someone actually typed, rather than against position in the
 *  batch sent to the server. */
export function parseContactCsvRows(text: string): ParsedContactRow[] {
  const rows = parseRows(text).filter((row) => !isBlankRow(row.cells));
  if (rows.length === 0) return [];

  const first = rows[0];
  const hasHeader = (first?.cells[0] ?? "").trim().toLowerCase() === "email";
  const dataRows = hasHeader ? rows.slice(1) : rows;

  return dataRows.map((row) => ({
    email: (row.cells[0] ?? "").trim(),
    name: (row.cells[1] ?? "").trim(),
    line: row.line,
  }));
}

export function parseContactCsv(text: string): ContactImportRow[] {
  return parseContactCsvRows(text).map(({ email, name }) => ({ email, name }));
}

/**
 * The other direction: a client-side "Export selected" for a `DataTable`'s bulk-action bar.
 * Nothing here calls the server — it turns rows already on screen into a CSV the browser
 * downloads, for lists that have no bulk write worth offering.
 */

/** Builds a CSV string from a header row and the data rows beneath it. */
export function toCsv(header: string[], rows: string[][]): string {
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
}

/** Triggers a browser download of `content` as `filename` — no library, one anchor click. */
export function downloadCsv(filename: string, header: string[], rows: string[][]): void {
  const blob = new Blob([toCsv(header, rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
