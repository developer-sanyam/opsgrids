/**
 * The export baseline (ADR-0022 addendum): what an export wrote in every editable cell, kept in a
 * very hidden sheet of the same workbook, so that an update can tell the cells the user CHANGED
 * from the cells they merely still have.
 *
 * Without it an old export used as an update sheet puts other people's changes back: an export
 * fills every cell, so a cell nobody touched still holds Monday's value, and if a colleague changed
 * that field on Tuesday it reads as an edit and is sent again. With it, a row's cell that equals
 * what the export wrote is untouched and treated as blank ("leave the field alone").
 *
 * The baseline is security-neutral. It can only make the import IGNORE a cell the user can see in
 * their own sheet; it can never add a value, name a record, or widen what may be written. A
 * baseline that is missing, damaged, oversized or another list's is ignored and the file behaves as
 * it always did, with a notice.
 *
 * The sheet: row 1 is the marker (`opsgrids-baseline`, format, the entity's id, the export time),
 * row 2 the column keys after a `recordId` header, and each further row one exported record, its
 * id and the value written under each column.
 */

import type * as ExcelJS from "exceljs";

import { InvalidCell, displayValue, importableColumns, isBlank } from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import { readCell } from "./cellValue";
import type { ImportColumn } from "./types";

export const BASELINE_SHEET = "OpsGrids baseline";
export const BASELINE_MARKER = "opsgrids-baseline";
export const BASELINE_FORMAT = 1;
/** Header of the first column: the record's id. The others are `ImportColumn.key`s. */
const RECORD_HEADER = "recordId";

/** What the sheet holds for one cell: its `cellToken`, or null when it was blank. */
export type BaselineRecord = ReadonlyMap<string, string | null>;

export type Baseline =
  /** The file has no baseline sheet (a sample sheet, or a round trip through an app that drops it). */
  | { kind: "absent" }
  /** There is one, but it is not this list's, or is damaged or too big: it is ignored. */
  | { kind: "unusable" }
  | { kind: "found"; records: ReadonlyMap<string, BaselineRecord> };

export const NO_BASELINE: Baseline = { kind: "absent" };

/** Whether an export of this list carries a baseline: only a list that can be updated, and only when a row can name its record. */
export function hasBaseline(entity: AnyEntity): boolean {
  return entity.update !== undefined && entity.recordIdKey !== undefined;
}

/**
 * A cell as the baseline compares it, the same way for what an export wrote and for what a file
 * holds when it comes back: a number by its plain digits, a date as ISO, a boolean as Yes/No, text
 * trimmed with every line ending one, so an unedited cell always equals its baseline whatever
 * Excel did to its type. `null` is blank. `undefined` is a cell that holds an error, which equals
 * nothing.
 */
export function cellToken(cell: CellInput | undefined): string | null | undefined {
  if (cell instanceof InvalidCell) return undefined;
  if (isBlank(cell)) return null;
  return displayValue(cell).replace(/\r\n?/g, "\n");
}

/** Whether a row's cell is exactly what the export wrote in it. A column the baseline does not know is never untouched. */
export function isUntouched(baseline: BaselineRecord, key: string, cell: CellInput | undefined): boolean {
  const written = baseline.get(key);
  return written !== undefined && cellToken(cell) === written;
}

/**
 * A row's cells with every untouched one made blank, which is how an update treats a blank cell:
 * leave the field alone. Done before the cell is judged for anything (a column that cannot be
 * updated, a lookup label, its type), so a stale untouched cell is never sent, never refused as
 * ambiguous and never looked up. An edited cell, and `[clear]`, stay as they are.
 */
export function withoutUntouched(
  cells: Readonly<Record<string, CellInput>>,
  baseline: BaselineRecord,
): Record<string, CellInput> {
  const kept: Record<string, CellInput> = {};
  for (const [key, cell] of Object.entries(cells)) kept[key] = isUntouched(baseline, key, cell) ? null : cell;
  return kept;
}

/** The columns whose cells a baseline records: the ones an import reads and an export writes. */
export function baselineColumns(entity: AnyEntity): ImportColumn[] {
  return importableColumns(entity).filter((column) => column.importOnly !== true);
}

export interface ExportedRecord {
  /** The record's id, as the export wrote it in the Record ID column. */
  id: string;
  /** What the export wrote under each of `baselineColumns`, in that order (blank as null). */
  cells: readonly (string | number | null)[];
}

/** Adds the baseline sheet, very hidden so that no menu of the spreadsheet program offers it. */
export function addBaselineSheet(
  workbook: ExcelJS.Workbook,
  entity: AnyEntity,
  records: readonly ExportedRecord[],
  exportedAt: Date,
): void {
  const sheet = workbook.addWorksheet(BASELINE_SHEET, { state: "veryHidden" });
  sheet.addRow([BASELINE_MARKER, BASELINE_FORMAT, entity.id, exportedAt.toISOString()]);
  sheet.addRow([RECORD_HEADER, ...baselineColumns(entity).map((column) => column.key)]);
  for (const record of records) sheet.addRow([record.id, ...record.cells]);
}

function textOf(cell: CellInput): string | null {
  return typeof cell === "string" ? cell : null;
}

/**
 * The baseline sheet's records, or null when the sheet is not a usable baseline of THIS list:
 * a different marker, format or list, a header that repeats or is empty, a record with no id or
 * two with the same id, more records than an export writes, more columns than a sheet may have.
 * A column key this version of the list no longer has is skipped; it cannot matter.
 */
function parse(sheet: ExcelJS.Worksheet, entity: AnyEntity, maxRecords: number, maxColumns: number): Map<string, BaselineRecord> | null {
  const marker = sheet.getRow(1);
  if (textOf(readCell(marker.getCell(1))) !== BASELINE_MARKER) return null;
  if (readCell(marker.getCell(2)) !== BASELINE_FORMAT) return null;
  if (textOf(readCell(marker.getCell(3))) !== entity.id) return null;

  const header = sheet.getRow(2);
  if (textOf(readCell(header.getCell(1))) !== RECORD_HEADER) return null;
  const known = new Set(baselineColumns(entity).map((column) => column.key));
  const seen = new Set<string>();
  const tracked: { col: number; key: string }[] = [];
  let malformed = false;
  header.eachCell({ includeEmpty: false }, (cell, col) => {
    if (col === 1 || malformed) return;
    const key = textOf(readCell(cell));
    if (key === null || key === "" || seen.has(key) || col > maxColumns + 1) {
      malformed = true;
      return;
    }
    seen.add(key);
    if (known.has(key)) tracked.push({ col, key });
  });
  if (malformed) return null;

  const records = new Map<string, BaselineRecord>();
  sheet.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (rowNumber <= 2 || malformed) return;
    const id = textOf(readCell(row.getCell(1)))?.trim() ?? "";
    if (id === "" || records.has(id) || records.size >= maxRecords) {
      malformed = true;
      return;
    }
    const written = new Map<string, string | null>();
    for (const { col, key } of tracked) {
      const token = cellToken(readCell(row.getCell(col)));
      if (token !== undefined) written.set(key, token);
    }
    records.set(id, written);
  });
  return malformed ? null : records;
}

/**
 * Finds and validates the baseline in a loaded workbook. It never throws and never reads more than
 * `maxRecords` records or `maxColumns` columns: whatever is wrong with the sheet, the answer is
 * "unusable" and the import carries on without it.
 */
export function readBaseline(
  workbook: ExcelJS.Workbook,
  entity: AnyEntity,
  limits: { maxRecords: number; maxColumns: number },
): Baseline {
  if (!hasBaseline(entity)) return NO_BASELINE;
  const wanted = BASELINE_SHEET.toLowerCase();
  const sheet = workbook.worksheets.find((candidate) => candidate.name.trim().toLowerCase() === wanted);
  if (sheet === undefined) return NO_BASELINE;
  const records = parse(sheet, entity, limits.maxRecords, limits.maxColumns);
  return records === null ? { kind: "unusable" } : { kind: "found", records };
}
