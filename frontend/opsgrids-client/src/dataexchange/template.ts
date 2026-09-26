/**
 * The sample sheet: built from the same column definitions the importer reads, so the two cannot
 * drift (ADR-0022 §6). Three sheets: Import (headers, example rows, dropdowns), Instructions
 * (every column, then the entity's rules, then the general ones) and Lists (the values that are
 * valid for this organisation right now).
 */

import type * as ExcelJS from "exceljs";

import { displayValue, importableColumns, isValidDate, utcMidnight } from "./cells";
import type { AnyEntity } from "./cells";
import { loadExcel, moneyNumberFormat, styleHeaderRow, validationsOf, widthFor, workbookToBlob } from "./excel";
import { currencyExponent } from "./money";
import { substituteSampleRows } from "./sample";
import { DEFAULT_MAX_ROWS } from "./workbook";
import type { ImportColumn, LookupSet, SheetValue } from "./types";

/** Excel refuses an inline list longer than this. */
const INLINE_LIST_LIMIT = 255;
const TEXT_FORMAT = "@";
const DATE_FORMAT = "yyyy-mm-dd";

export function sampleFileName(entity: Pick<AnyEntity, "fileStem">): string {
  return `${entity.fileStem}-sample.xlsx`;
}

function columnLetter(index: number): string {
  let letters = "";
  for (let n = index; n > 0; n = Math.floor((n - 1) / 26)) {
    letters = String.fromCharCode(65 + ((n - 1) % 26)) + letters;
  }
  return letters;
}

/** Formats that keep a leading zero, a leading + or an ISO date as the user typed it. */
function dataFormat(column: ImportColumn): string | null {
  switch (column.kind) {
    case "text":
    case "phone":
    case "enum":
    case "email":
      return TEXT_FORMAT;
    case "date":
      return DATE_FORMAT;
    default:
      return null;
  }
}

function isBlankSample(value: SheetValue | undefined): value is null | undefined {
  return value === null || value === undefined || (typeof value === "string" && value.trim() === "");
}

/** How a sample value is written: numbers as numbers, dates as dates, everything else as text. */
function sampleCell(column: ImportColumn, value: SheetValue | undefined): string | number | Date | null {
  if (isBlankSample(value)) return null;
  const shown = displayValue(value);
  switch (column.kind) {
    case "integer":
    case "decimal":
    case "money":
      if (typeof value === "number") return value;
      return /^-?\d+(?:\.\d+)?$/.test(shown) ? Number(shown) : shown;
    case "date":
      if (value instanceof Date) return isValidDate(value) ? value : shown;
      return utcMidnight(shown) ?? shown;
    case "boolean":
      return typeof value === "boolean" ? (value ? "Yes" : "No") : shown;
    case "enum":
      return (
        (column.options ?? []).find((option) => option.value.toLowerCase() === shown.toLowerCase())?.label ??
        shown
      );
    default:
      return shown;
  }
}

interface ListColumn {
  header: string;
  labels: readonly string[];
}

interface ListPlan {
  columns: ListColumn[];
  /** Lists column (1-based) by lookup name, for lookups. */
  byLookup: Map<string, number>;
  /** Lists column (1-based) by column key, for enums that needed a range. */
  byColumn: Map<string, number>;
}

function inlineFormula(labels: readonly string[]): string | null {
  if (labels.length === 0 || labels.some((label) => /[",]/.test(label))) return null;
  const joined = labels.join(",");
  return joined.length <= INLINE_LIST_LIMIT ? `"${joined}"` : null;
}

/** Which lists go on the Lists sheet: every lookup, plus any enum too long to sit inline. */
function planLists(columns: readonly ImportColumn[], lookups: LookupSet): ListPlan {
  const plan: ListPlan = { columns: [], byLookup: new Map(), byColumn: new Map() };
  for (const column of columns) {
    if (column.lookup === undefined || plan.byLookup.has(column.lookup)) continue;
    const lookup = lookups[column.lookup];
    if (lookup === undefined) continue;
    plan.columns.push({ header: column.header, labels: lookup.labels });
    plan.byLookup.set(column.lookup, plan.columns.length);
  }
  for (const column of columns) {
    if (column.kind !== "enum") continue;
    const labels = (column.options ?? []).map((option) => option.label);
    if (labels.length > 0 && inlineFormula(labels) === null) {
      plan.columns.push({ header: column.header, labels });
      plan.byColumn.set(column.key, plan.columns.length);
    }
  }
  return plan;
}

function listFormula(labels: readonly string[], listColumn: number | undefined): string | null {
  const inline = inlineFormula(labels);
  if (inline !== null) return inline;
  if (listColumn === undefined || labels.length === 0) return null;
  const letter = columnLetter(listColumn);
  return `Lists!$${letter}$2:$${letter}$${labels.length + 1}`;
}

function formulaFor(column: ImportColumn, lookups: LookupSet, plan: ListPlan): string | null {
  if (column.kind === "boolean") return '"Yes,No"';
  if (column.kind === "enum") {
    const labels = (column.options ?? []).map((option) => option.label);
    return listFormula(labels, plan.byColumn.get(column.key));
  }
  if (column.lookup !== undefined) {
    const lookup = lookups[column.lookup];
    return lookup === undefined ? null : listFormula(lookup.labels, plan.byLookup.get(column.lookup));
  }
  return null;
}

function boundsNote(column: ImportColumn): string {
  const { min, max } = column;
  if (min !== undefined && max !== undefined) return `, from ${min} to ${max}`;
  if (min !== undefined) return `, at least ${min}`;
  if (max !== undefined) return `, at most ${max}`;
  return "";
}

function formatNote(entity: AnyEntity, column: ImportColumn): string {
  switch (column.kind) {
    case "text": {
      if (column.lookup !== undefined) return "One of the names on the Lists sheet";
      const length = column.maxLength !== undefined ? `, up to ${column.maxLength} characters` : "";
      const hint = column.patternHint !== undefined ? `. ${column.patternHint}` : "";
      return `Text${length}${hint}`;
    }
    case "integer":
      return `Whole number${boundsNote(column)}`;
    case "decimal":
      return `Number${boundsNote(column)}`;
    case "money": {
      const holder = entity.columns.find((other) => other.key === (column.currencyKey ?? "currency"));
      const currency =
        holder !== undefined
          ? `currency in the "${holder.header}" column`
          : `currency ${column.defaultCurrency ?? "INR"}`;
      return `Amount in major units (1499.50, not 149950); ${currency}${boundsNote(column)}`;
    }
    case "date":
      return "Date written YYYY-MM-DD, e.g. 2026-03-04";
    case "boolean":
      return "Yes or No";
    case "enum":
      return "One of the allowed values";
    case "email":
      return "Email address";
    case "phone":
      return "Phone number starting with + and the country code, e.g. +919876543210";
  }
}

function allowedValues(column: ImportColumn): string {
  if (column.kind === "boolean") return "Yes, No";
  if (column.kind === "enum") return (column.options ?? []).map((option) => option.label).join(", ");
  if (column.lookup !== undefined) return `The "${column.header}" list on the Lists sheet`;
  return "";
}

function generalRules(entity: AnyEntity, columns: readonly ImportColumn[]): string[] {
  const has = (kind: ImportColumn["kind"]): boolean => columns.some((column) => column.kind === kind);
  const rules = [
    `Up to ${entity.maxRows ?? DEFAULT_MAX_ROWS} rows per file.`,
    `Columns marked * are required.`,
    `Existing ${entity.noun} are skipped, never overwritten. Importing the same file again is safe.`,
    "The example rows on the Import sheet are ignored while they are unchanged. Delete them or replace them with your own.",
  ];
  if (has("date")) rules.push("Write dates as YYYY-MM-DD, e.g. 2026-03-04.");
  if (has("money")) {
    rules.push("Write money in major units (1499.50, not 149950) with the currency in the currency column.");
  }
  if (has("phone")) rules.push("Write phone numbers with the country code, starting with +.");
  rules.push("Do not rename the headers on the Import sheet.");
  return rules;
}

function writeInstructions(
  sheet: ExcelJS.Worksheet,
  entity: AnyEntity,
  columns: readonly ImportColumn[],
  samples: readonly Record<string, SheetValue>[],
): void {
  const widths = [26, 10, 44, 40, 28, 60];
  widths.forEach((width, index) => {
    sheet.getColumn(index + 1).width = width;
  });

  const heading = sheet.addRow([`How to fill in the ${entity.noun} sheet`]);
  heading.font = { bold: true, size: 14 };
  sheet.addRow([]);
  styleHeaderRow(sheet.addRow(["Column", "Required", "Format", "Allowed values", "Example", "What to enter"]));

  for (const column of columns) {
    const example = samples.map((sample) => displayValue(sample[column.key])).find((shown) => shown !== "");
    const row = sheet.addRow([
      column.header,
      column.required === true ? "Yes" : "No",
      formatNote(entity, column),
      allowedValues(column),
      example ?? "",
      column.help,
    ]);
    row.alignment = { vertical: "top", wrapText: true };
  }

  const paragraph = (title: string, lines: readonly string[]): void => {
    sheet.addRow([]);
    sheet.addRow([title]).font = { bold: true };
    for (const line of lines) sheet.addRow([line]);
  };
  if (entity.notes !== undefined && entity.notes.length > 0) paragraph("Notes", entity.notes);
  paragraph("General rules", generalRules(entity, columns));
}

function writeLists(sheet: ExcelJS.Worksheet, plan: ListPlan): void {
  plan.columns.forEach((list, index) => {
    const column = sheet.getColumn(index + 1);
    column.width = widthFor(Math.max(list.header.length, ...list.labels.map((label) => label.length)));
    const header = sheet.getCell(1, index + 1);
    header.value = list.header;
    list.labels.forEach((label, row) => {
      const cell = sheet.getCell(row + 2, index + 1);
      cell.value = label;
      cell.numFmt = TEXT_FORMAT;
    });
  });
  styleHeaderRow(sheet.getRow(1));
}

/** The sample workbook for an entity, as a downloadable .xlsx file. */
export async function buildSampleWorkbook(entity: AnyEntity, lookups: LookupSet): Promise<Blob> {
  const excel = await loadExcel();
  const workbook = new excel.Workbook();
  workbook.creator = "OpsGrids";

  const columns = importableColumns(entity);
  const plan = planLists(columns, lookups);
  const samples = substituteSampleRows(entity, lookups);
  const lastRow = (entity.maxRows ?? DEFAULT_MAX_ROWS) + 1;

  const sheet = workbook.addWorksheet("Import", { views: [{ state: "frozen", ySplit: 1 }] });
  styleHeaderRow(sheet.getRow(1));

  columns.forEach((column, index) => {
    const position = index + 1;
    const letter = columnLetter(position);
    const header = sheet.getCell(1, position);
    header.value = column.required === true ? `${column.header} *` : column.header;

    const format = dataFormat(column);
    if (format !== null) {
      for (let row = 2; row <= lastRow; row += 1) sheet.getCell(row, position).numFmt = format;
    }

    const formula = formulaFor(column, lookups, plan);
    if (formula !== null) {
      validationsOf(sheet).add(`${letter}2:${letter}${lastRow}`, {
        type: "list",
        allowBlank: true,
        formulae: [formula],
        showErrorMessage: true,
        errorStyle: "warning",
        errorTitle: "Not in the list",
        error: "Pick a value from the drop-down list.",
      });
    }

    const shown = samples.map((sample) => displayValue(sample[column.key]).length);
    sheet.getColumn(position).width = widthFor(Math.max(header.text.length, ...shown));
  });

  samples.forEach((sample, offset) => {
    columns.forEach((column, index) => {
      const value = sampleCell(column, sample[column.key]);
      if (value === null) return;
      const cell = sheet.getCell(offset + 2, index + 1);
      cell.value = value;
      if (column.kind === "money") {
        const holder = String(sample[column.currencyKey ?? "currency"] ?? column.defaultCurrency ?? "INR");
        cell.numFmt = moneyNumberFormat(currencyExponent(holder));
      }
    });
  });

  writeInstructions(workbook.addWorksheet("Instructions"), entity, columns, samples);
  if (plan.columns.length > 0) writeLists(workbook.addWorksheet("Lists"), plan);

  return workbookToBlob(workbook);
}
