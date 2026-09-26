/**
 * The export workbook: what the list endpoint returned, written so that Excel cannot turn it into
 * something else. It carries the importable columns and the read-only ones, and leaves out the
 * import-only ones (the list has nothing to put in them).
 *
 * Every string is written as a string cell, never a formula, and the text-like columns carry the
 * text format so that editing a cell (F2, Enter) keeps `=HYPERLINK(...)`, `+1` or `@SUM` as the
 * text it is. Masked values stay masked: this module writes what `toRow` returns and nothing else.
 */

import { isValidDate, isoDate } from "./cells";
import type { AnyEntity } from "./cells";
import { clipCell, loadExcel, moneyNumberFormat, styleHeaderRow, widthFor, workbookToBlob } from "./excel";
import { currencyExponent, plainNumberString } from "./money";
import type { ImportColumn, LookupSet, SheetValue } from "./types";

const TEXT_FORMAT = "@";
const DEFAULT_MONEY_PLACES = 2;

/** `companies-2026-09-26.xlsx`, on the user's calendar day. */
export function exportFileName(entity: Pick<AnyEntity, "fileStem">, date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${entity.fileStem}-${date.getFullYear()}-${month}-${day}.xlsx`;
}

const NUMERIC_KINDS: ReadonlySet<ImportColumn["kind"]> = new Set(["integer", "decimal", "money"]);

function exportValue(column: ImportColumn, value: SheetValue | undefined): string | number | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value instanceof Date) {
    if (!isValidDate(value)) return null;
    return column.kind === "date" ? isoDate(value) : value.toISOString();
  }
  const numeric = NUMERIC_KINDS.has(column.kind);
  if (typeof value === "number") {
    return numeric ? value : (plainNumberString(value) ?? String(value));
  }
  const text = value.trim();
  if (text === "") return null;
  if (numeric && /^-?\d+(?:\.\d+)?$/.test(text)) return Number(text);
  return clipCell(value);
}

function columnFormat(column: ImportColumn): string | null {
  if (column.kind === "money") return moneyNumberFormat(DEFAULT_MONEY_PLACES);
  return NUMERIC_KINDS.has(column.kind) ? null : TEXT_FORMAT;
}

/**
 * The export as a downloadable .xlsx: one sheet, "Export", with the import columns and the
 * read-only ones. `items` are the list endpoint's rows; the entity's `toRow` shapes each one.
 */
export async function buildExportWorkbook(
  entity: AnyEntity,
  items: readonly unknown[],
  lookups: LookupSet,
): Promise<Blob> {
  const source = entity.export;
  if (source === undefined) throw new Error(`${entity.noun} cannot be exported`);

  const excel = await loadExcel();
  const workbook = new excel.Workbook();
  workbook.creator = "OpsGrids";
  const sheet = workbook.addWorksheet("Export", { views: [{ state: "frozen", ySplit: 1 }] });

  // The list never returns an import-only value, so its column could only ever be empty.
  const columns = entity.columns.filter((column) => column.importOnly !== true);
  const widest = columns.map((column) => column.header.length);
  columns.forEach((column, index) => {
    const format = columnFormat(column);
    if (format !== null) sheet.getColumn(index + 1).style = { numFmt: format };
  });

  styleHeaderRow(sheet.addRow(columns.map((column) => column.header)));

  for (const item of items) {
    const shaped = source.toRow(item, lookups);
    const values = columns.map((column, index) => {
      const value = exportValue(column, shaped[column.key]);
      widest[index] = Math.max(widest[index] ?? 0, typeof value === "string" ? value.length : String(value ?? "").length);
      return value;
    });
    const row = sheet.addRow(values);
    columns.forEach((column, index) => {
      if (column.kind !== "money" || typeof values[index] !== "number") return;
      const currency = String(shaped[column.currencyKey ?? "currency"] ?? column.defaultCurrency ?? "INR");
      const places = currencyExponent(currency);
      if (places !== DEFAULT_MONEY_PLACES) row.getCell(index + 1).numFmt = moneyNumberFormat(places);
    });
  }

  columns.forEach((_, index) => {
    sheet.getColumn(index + 1).width = widthFor(widest[index] ?? 0);
  });
  if (columns.length > 0) {
    sheet.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: columns.length } };
  }
  return workbookToBlob(workbook);
}
