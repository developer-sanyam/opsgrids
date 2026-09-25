/**
 * A client-side "Export selected" for a `DataTable`'s bulk-action bar. Nothing here calls the
 * server — it turns rows already on screen into a CSV the browser downloads, for lists that have
 * no bulk write worth offering (or none the server exposes yet).
 */

/** RFC 4180 quoting: wrap a field in quotes and double any quote it contains, only when needed. */
function csvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Builds a CSV string from a header row and the data rows beneath it. */
export function toCsv(header: string[], rows: string[][]): string {
  return [header, ...rows].map((row) => row.map(csvField).join(",")).join("\n");
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
