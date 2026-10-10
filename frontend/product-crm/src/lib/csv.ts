/**
 * "Export selected" as a CSV, built and downloaded entirely in the browser — no endpoint, no
 * write, so it is safe on any selection regardless of permission scope (the rows themselves
 * already came from a scoped list request).
 */

import { csvCell } from "@opsgrids/client";

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => string;
}

/** Builds a CSV string with a header row, one line per `row`. */
export function toCsv<T>(rows: readonly T[], columns: readonly CsvColumn<T>[]): string {
  const lines = [columns.map((c) => csvCell(c.header)).join(",")];
  for (const row of rows) {
    lines.push(columns.map((c) => csvCell(c.value(row))).join(","));
  }
  return lines.join("\r\n");
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
