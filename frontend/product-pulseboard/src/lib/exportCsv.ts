/**
 * A minimal, dependency-free CSV export for a bulk selection — quoting only what needs it, no
 * library pulled in for one function. The browser downloads it as a file; nothing is sent
 * anywhere, so this needs no permission beyond the read the list itself already required.
 */

import { csvCell } from "@opsgrids/client";
export function toCsv(headers: string[], rows: string[][]): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
