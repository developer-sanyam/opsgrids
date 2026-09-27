/**
 * The export baseline (ADR-0022 addendum): a record, in a very hidden sheet of the same workbook, of
 * what an export wrote in every editable cell, so that an update can tell the cells the user CHANGED
 * from the cells they merely still have.
 *
 * Without it an old export used as an update sheet puts other people's changes back: an export
 * fills every cell, so a cell nobody touched still holds Monday's value, and if a colleague changed
 * that field on Tuesday it reads as an edit and is sent again. With it, a row's cell that equals
 * what the export wrote is untouched and treated as blank ("leave the field alone").
 *
 * The sheet holds no value, only salted digests (`baselineHash.ts`): all the import needs is whether
 * a cell equals what was exported, and a user who deletes a sensitive column, or its rows, before
 * sharing the file must not leave it behind in a hidden sheet. What it does hold in the clear is the
 * record ids, which are opaque.
 *
 * The baseline is security-neutral. It can only make the import IGNORE a cell the user can see in
 * their own sheet; it can never add a value, name a record, or widen what may be written. A
 * baseline that is missing, damaged, oversized, another list's or of another format is ignored and
 * the file behaves as it always did, with a notice.
 *
 * The sheet: row 1 is the marker (`opsgrids-baseline`, format 2, the entity's id, the export time,
 * the file's salt), row 2 a `recordId` header and the column keys as a JSON list, and each further
 * row one exported record: its id, then ONE cell holding the digests of every column joined in the
 * order of that list. One cell per record and not one per column keeps a 10 000 x 20 export from
 * writing, and an import from reading, 200 000 cells of a sheet nobody looks at.
 */

import type * as ExcelJS from "exceljs";

import { DIGEST_LENGTH, DIGEST_PATTERN, SALT_PATTERN, digestAll, hashingAvailable, newSalt } from "./baselineHash";
import type { CellHashInput } from "./baselineHash";
import { InvalidCell, displayValue, importableColumns, isBlank } from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import { readCell } from "./cellValue";
import type { ImportColumn } from "./types";

export const BASELINE_SHEET = "OpsGrids baseline";
export const BASELINE_MARKER = "opsgrids-baseline";
/** Format 1 held the values themselves in the clear; a file of that format is not used. */
export const BASELINE_FORMAT = 2;
/** Header of the first column: the record's id. The others are `ImportColumn.key`s. */
const RECORD_HEADER = "recordId";

/**
 * What an update-mode preview needs to know of the baseline, per row of the file: the columns whose
 * cell is still what the export wrote. A row that names a record the baseline covers has an entry
 * (possibly empty); a row that does not has none.
 */
export type Baseline =
  /** The file has no baseline sheet (a sample sheet, or a round trip through an app that drops it). */
  | { kind: "absent" }
  /** There is one, but it is not this list's, or is damaged, too big or of another format: it is ignored. */
  | { kind: "unusable" }
  | { kind: "found"; untouched: ReadonlyMap<number, ReadonlySet<string>> };

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

/**
 * A row's cells with every untouched one made blank, which is how an update treats a blank cell:
 * leave the field alone. Done before the cell is judged for anything (a column that cannot be
 * updated, a lookup label, its type), so a stale untouched cell is never sent, never refused as
 * ambiguous and never looked up. An edited cell, and `[clear]`, stay as they are.
 */
export function withoutUntouched(
  cells: Readonly<Record<string, CellInput>>,
  untouched: ReadonlySet<string>,
): Record<string, CellInput> {
  const kept: Record<string, CellInput> = {};
  for (const [key, cell] of Object.entries(cells)) kept[key] = untouched.has(key) ? null : cell;
  return kept;
}

/** The columns whose cells a baseline records: the ones an import reads and an export writes. */
export function baselineColumns(entity: AnyEntity): ImportColumn[] {
  return importableColumns(entity).filter((column) => column.importOnly !== true);
}

// -------------------------------------------------------------------------------------- writing

export interface ExportedRecord {
  /** The record's id, as the export wrote it in the Record ID column. */
  id: string;
  /** What the export wrote under each of `baselineColumns`, in that order (blank as null). */
  cells: readonly (string | number | null)[];
}

/**
 * Whether an export of this list carries a baseline in this browser context: the list must be able
 * to use one and the context must be able to hash. `crypto.subtle` needs HTTPS (or localhost); an
 * export from anywhere else is written without a baseline, as it was before, and the import says so.
 */
export function canWriteBaseline(entity: AnyEntity): boolean {
  return hasBaseline(entity) && hashingAvailable();
}

/** Adds the baseline sheet, very hidden so that no menu of the spreadsheet program offers it. */
export async function addBaselineSheet(
  workbook: ExcelJS.Workbook,
  entity: AnyEntity,
  records: readonly ExportedRecord[],
  exportedAt: Date,
): Promise<void> {
  const keys = baselineColumns(entity).map((column) => column.key);
  const salt = newSalt();
  const inputs: CellHashInput[] = records.flatMap((record) =>
    keys.map((key, index) => ({ key, recordId: record.id, token: cellToken(record.cells[index] ?? null) ?? null })),
  );
  const digests = await digestAll(salt, inputs);

  const sheet = workbook.addWorksheet(BASELINE_SHEET, { state: "veryHidden" });
  sheet.addRow([BASELINE_MARKER, BASELINE_FORMAT, entity.id, exportedAt.toISOString(), salt]);
  sheet.addRow([RECORD_HEADER, JSON.stringify(keys)]);
  records.forEach((record, index) => {
    sheet.addRow([record.id, digests.slice(index * keys.length, (index + 1) * keys.length).join("")]);
  });
}

// -------------------------------------------------------------------------------------- reading

function textOf(cell: CellInput): string | null {
  return typeof cell === "string" ? cell : null;
}

/** The part of a sheet row the baseline needs: its number in the sheet and its cells by column key. */
interface BaselineRow {
  row: number;
  cells: Readonly<Record<string, CellInput>>;
}

/** What a baseline sheet holds once it is checked: the file's salt and, per record id, the digest of each column. */
interface BaselineSheet {
  salt: string;
  records: ReadonlyMap<string, ReadonlyMap<string, string>>;
}

/**
 * The baseline sheet's contents, or null when the sheet is not a usable baseline of THIS list: a
 * different marker, format or list, no proper salt, a column list that is not a list of distinct
 * names or is longer than a sheet may be wide, a record with no id or two with the same id, more
 * records than an export writes. A column key this version of the list no longer has is skipped, and
 * so is a record whose digests are missing, the wrong length or not digests (a program that mangled
 * the cell): each can only make cells count as edited.
 */
function parse(sheet: ExcelJS.Worksheet, entity: AnyEntity, maxRecords: number, maxColumns: number): BaselineSheet | null {
  const marker = sheet.getRow(1);
  if (textOf(readCell(marker.getCell(1))) !== BASELINE_MARKER) return null;
  if (readCell(marker.getCell(2)) !== BASELINE_FORMAT) return null;
  if (textOf(readCell(marker.getCell(3))) !== entity.id) return null;
  const salt = textOf(readCell(marker.getCell(5)));
  if (salt === null || !SALT_PATTERN.test(salt)) return null;

  const header = sheet.getRow(2);
  if (textOf(readCell(header.getCell(1))) !== RECORD_HEADER) return null;
  const keys = keyList(textOf(readCell(header.getCell(2))), maxColumns);
  if (keys === null) return null;
  const known = new Set(baselineColumns(entity).map((column) => column.key));

  const records = new Map<string, Map<string, string>>();
  let malformed = false;
  sheet.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (rowNumber <= 2 || malformed) return;
    const id = textOf(readCell(row.getCell(1)))?.trim() ?? "";
    if (id === "" || records.has(id) || records.size >= maxRecords) {
      malformed = true;
      return;
    }
    const written = new Map<string, string>();
    const packed = textOf(readCell(row.getCell(2)));
    // A record whose digests are missing or the wrong length has none to offer: its cells count as edited.
    if (packed !== null && packed.length === keys.length * DIGEST_LENGTH) {
      keys.forEach((key, index) => {
        const digest = packed.slice(index * DIGEST_LENGTH, (index + 1) * DIGEST_LENGTH);
        if (known.has(key) && DIGEST_PATTERN.test(digest)) written.set(key, digest);
      });
    }
    records.set(id, written);
  });
  return malformed ? null : { salt, records };
}

/** The column keys a baseline lists, or null when the cell is not a list of distinct, non-empty names within the column limit. */
function keyList(text: string | null, maxColumns: number): string[] | null {
  if (text === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed) || parsed.length > maxColumns) return null;
  const keys: string[] = [];
  for (const key of parsed as unknown[]) {
    if (typeof key !== "string" || key === "" || keys.includes(key)) return null;
    keys.push(key);
  }
  return keys;
}

/**
 * Which cells of the file are still what the export wrote. Every row that names a record the
 * baseline covers has each of its cells hashed the way the export hashed it and compared: equal is
 * untouched. Only cells the file has are hashed (at most the import's row limit times its column
 * limit), never the whole baseline. A cell that holds an error, a column the baseline does not
 * know, and a digest that is missing or does not match are all "edited", which is what a file without
 * a baseline is judged as.
 */
async function untouchedCells(
  baseline: BaselineSheet,
  entity: AnyEntity,
  rows: readonly BaselineRow[],
): Promise<Map<number, Set<string>>> {
  const { recordIdKey } = entity;
  const untouched = new Map<number, Set<string>>();
  if (recordIdKey === undefined) return untouched;
  const jobs: { row: number; input: CellHashInput; expected: string }[] = [];
  for (const { row, cells } of rows) {
    const recordId = displayValue(cells[recordIdKey]);
    const written = baseline.records.get(recordId);
    if (written === undefined) continue;
    untouched.set(row, new Set());
    for (const [key, cell] of Object.entries(cells)) {
      const expected = written.get(key);
      const token = cellToken(cell);
      if (expected !== undefined && token !== undefined) jobs.push({ row, input: { key, recordId, token }, expected });
    }
  }
  const digests = await digestAll(
    baseline.salt,
    jobs.map((job) => job.input),
  );
  jobs.forEach((job, index) => {
    if (digests[index] === job.expected) untouched.get(job.row)?.add(job.input.key);
  });
  return untouched;
}

/**
 * Finds and validates the baseline in a loaded workbook and says which cells of `rows` are still what
 * the export wrote. It never throws and never reads more than `maxRecords` records or `maxColumns`
 * columns: whatever is wrong with the sheet, the answer is "unusable" and the import carries on
 * without it.
 */
export async function readBaseline(
  workbook: ExcelJS.Workbook,
  entity: AnyEntity,
  rows: readonly BaselineRow[],
  limits: { maxRecords: number; maxColumns: number },
): Promise<Baseline> {
  if (!hasBaseline(entity)) return NO_BASELINE;
  const wanted = BASELINE_SHEET.toLowerCase();
  const sheet = workbook.worksheets.find((candidate) => candidate.name.trim().toLowerCase() === wanted);
  if (sheet === undefined) return NO_BASELINE;
  const baseline = hashingAvailable() ? parse(sheet, entity, limits.maxRecords, limits.maxColumns) : null;
  if (baseline === null) return { kind: "unusable" };
  return { kind: "found", untouched: await untouchedCells(baseline, entity, rows) };
}
