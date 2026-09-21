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
 */

function splitLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (inQuotes) {
      if (char === '"' && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        current += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      cells.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  cells.push(current);
  return cells.map((cell) => cell.trim());
}

export function parseContactCsv(text: string): ContactImportRow[] {
  const lines = text
    .split(/\r\n|\n|\r/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
  if (lines.length === 0) return [];

  const firstLine = lines[0];
  const first = splitLine(firstLine ?? "");
  const hasHeader = (first[0] ?? "").toLowerCase() === "email";
  const dataLines = hasHeader ? lines.slice(1) : lines;

  return dataLines.map((line) => {
    const cells = splitLine(line);
    return { email: cells[0] ?? "", name: cells[1] ?? "" };
  });
}
