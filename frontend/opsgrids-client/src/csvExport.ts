/**
 * "Export selected" as a CSV, built and downloaded entirely in the browser — no endpoint, no
 * write, so it is safe on any selection regardless of permission scope (the rows themselves
 * already came from a scoped list request).
 *
 * Every cell goes through `csvCell`, so a value an anonymous visitor typed cannot run as a
 * spreadsheet formula. Lines end in CRLF (RFC 4180).
 */

import { csvCell } from "./csv";

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => string;
}

const LINE_END = "\r\n";

/** Builds a CSV string with a header row, one line per `row`, columns read by accessor. */
export function toCsv<T>(rows: readonly T[], columns: readonly CsvColumn<T>[]): string {
  const lines = [columns.map((c) => csvCell(c.header)).join(",")];
  for (const row of rows) {
    lines.push(columns.map((c) => csvCell(c.value(row))).join(","));
  }
  return lines.join(LINE_END);
}

/** Builds a CSV string from a header row and rows that are already arrays of strings. */
export function toCsvGrid(header: readonly string[], rows: readonly (readonly string[])[]): string {
  return [header, ...rows].map((line) => line.map(csvCell).join(",")).join(LINE_END);
}

/** Triggers a browser download of `content` as `filename`. */
export function downloadCsvFile(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Builds the CSV and starts the download in one call — what a table's "Export selected" wants. */
export function downloadCsv<T>(filename: string, rows: readonly T[], columns: readonly CsvColumn<T>[]): void {
  downloadCsvFile(filename, toCsv(rows, columns));
}

/** `downloadCsv` for a header row and string rows. */
export function downloadCsvGrid(
  filename: string,
  header: readonly string[],
  rows: readonly (readonly string[])[],
): void {
  downloadCsvFile(filename, toCsvGrid(header, rows));
}
