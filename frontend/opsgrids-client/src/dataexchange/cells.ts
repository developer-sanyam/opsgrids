/**
 * What the engine handles between the spreadsheet library and a column: a cell that could not be
 * read as a value, the display string a preview shows, and the few helpers every module shares.
 */

import { plainNumberString } from "./money";
import type { ImportColumn, ImportEntity, SheetValue } from "./types";

/** An entity of any payload, item and filter type: what the engine's pure functions accept. */
export type AnyEntity = ImportEntity<unknown, unknown, unknown>;

/**
 * A cell that has no usable value: a `#DIV/0!` result, a formula nobody calculated. It reaches
 * the column as an error sentence instead of vanishing into a blank.
 */
export class InvalidCell {
  /** What the cell shows ("#DIV/0!"), for the preview. */
  readonly shown: string;
  /** The tail of a sentence about the cell: "contains the error #DIV/0!". */
  readonly reason: string;

  constructor(shown: string, reason: string) {
    this.shown = shown;
    this.reason = reason;
  }
}

export type CellInput = SheetValue | InvalidCell;

export function importableColumns(entity: Pick<AnyEntity, "columns">): ImportColumn[] {
  return entity.columns.filter((column) => column.readOnly !== true);
}

/**
 * What the list calls the column an export writes each record's id in ("Company ID"): the words a
 * sheet's header row uses, so every sentence that tells someone to fill in or clear it says the
 * same thing as the sheet. "Record ID" only for a list that has no such column.
 */
export function recordIdHeader(entity: Pick<AnyEntity, "columns" | "recordIdKey">): string {
  return entity.columns.find((column) => column.key === entity.recordIdKey)?.header ?? "Record ID";
}

export function isValidDate(value: Date): boolean {
  return !Number.isNaN(value.getTime());
}

/** `YYYY-MM-DD` from the UTC parts: Excel dates carry no zone, and exceljs reads them as UTC. */
export function isoDate(value: Date): string {
  const year = String(value.getUTCFullYear()).padStart(4, "0");
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function isBlank(cell: CellInput | undefined): boolean {
  return cell === null || cell === undefined || (typeof cell === "string" && cell.trim() === "");
}

/** What the user typed, trimmed, for the preview table and the sample-row comparison. */
export function displayValue(cell: CellInput | undefined): string {
  if (cell === null || cell === undefined) return "";
  if (cell instanceof InvalidCell) return cell.shown;
  if (typeof cell === "string") return cell.trim();
  if (typeof cell === "number") return plainNumberString(cell) ?? String(cell);
  if (typeof cell === "boolean") return cell ? "Yes" : "No";
  return isValidDate(cell) ? isoDate(cell) : "";
}

/** A header as the sheet is matched against: no case, no spaces, no trailing asterisk. */
export function headerKey(header: string): string {
  return header.replace(/\s+/g, "").replace(/\*+$/, "").toLowerCase();
}

/** The date as Excel would hold it: midnight UTC of the calendar day. */
export function utcMidnight(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  const real =
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  return real ? date : null;
}
