/**
 * The seam to the spreadsheet library. `exceljs` is about 250 KB gzip, so it is only ever loaded by
 * `await import("exceljs")` inside an async function here: importing `@opsgrids/client` never
 * pulls it in, and Vite splits it into its own chunk that downloads when someone opens the dialog.
 */

import type * as ExcelJS from "exceljs";

export type Excel = typeof ExcelJS;

export const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** Excel refuses a cell longer than this. */
export const MAX_CELL_CHARACTERS = 32_767;

function isExcel(value: unknown): value is Excel {
  return (
    typeof value === "object" &&
    value !== null &&
    "Workbook" in value &&
    typeof value.Workbook === "function"
  );
}

/**
 * The library, whichever way the runtime hands it over: Node's ESM-over-CommonJS interop and
 * Vite's browser bundle expose the exports either on the namespace or on its `default`.
 */
export async function loadExcel(): Promise<Excel> {
  const loaded: unknown = await import("exceljs");
  if (isExcel(loaded)) return loaded;
  if (typeof loaded === "object" && loaded !== null && "default" in loaded && isExcel(loaded.default)) {
    return loaded.default;
  }
  throw new Error("The spreadsheet library did not load");
}

/** A workbook's bytes as a downloadable file. */
export async function workbookToBlob(workbook: ExcelJS.Workbook): Promise<Blob> {
  const bytes = await workbook.xlsx.writeBuffer();
  return new Blob([bytes], { type: XLSX_MIME });
}

/** The file's bytes. jsdom's `Blob` has no `arrayBuffer()`, so fall back to `FileReader`. */
export function readBytes(file: Blob): Promise<Uint8Array> {
  if (typeof file.arrayBuffer === "function") {
    return file.arrayBuffer().then((buffer) => new Uint8Array(buffer));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(new Uint8Array(reader.result));
      else reject(new Error("The file could not be read"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("The file could not be read"));
    reader.readAsArrayBuffer(file);
  });
}

/**
 * exceljs declares `load` over Node's `Buffer`. At runtime it hands the bytes to JSZip, which
 * takes any byte array, and a browser has nothing but `Uint8Array`.
 */
export function loadableBytes(bytes: Uint8Array): Parameters<ExcelJS.Xlsx["load"]>[0] {
  return bytes as unknown as Parameters<ExcelJS.Xlsx["load"]>[0];
}

interface SheetValidations {
  model: Record<string, ExcelJS.DataValidation | undefined>;
  add(address: string, validation: ExcelJS.DataValidation): unknown;
}

/**
 * A worksheet's data validations keyed by range (`"C2:C1001"`). exceljs implements this and its
 * type declarations omit it; the alternative, one validation per cell, writes a thousand entries
 * per dropdown column.
 */
export function validationsOf(sheet: ExcelJS.Worksheet): SheetValidations {
  return (sheet as unknown as { dataValidations: SheetValidations }).dataValidations;
}

/** Excel's own theme grey (white darkened 5%): no colour value of ours, and it follows the theme. */
const HEADER_TINT: Partial<ExcelJS.Color> & { tint: number } = { theme: 0, tint: -0.05 };

export function styleHeaderRow(row: ExcelJS.Row): void {
  row.font = { bold: true };
  row.fill = { type: "pattern", pattern: "solid", fgColor: HEADER_TINT };
  row.alignment = { vertical: "middle", wrapText: true };
}

/** A number format that shows a currency's own decimal places: `#,##0.00`, and `#,##0` for JPY. */
export function moneyNumberFormat(places: number): string {
  return places > 0 ? `#,##0.${"0".repeat(places)}` : "#,##0";
}

/** A column width in characters that fits `longest` without becoming a wall. */
export function widthFor(longest: number): number {
  return Math.min(Math.max(longest + 2, 12), 48);
}

export function clipCell(text: string): string {
  return text.length > MAX_CELL_CHARACTERS ? `${text.slice(0, MAX_CELL_CHARACTERS - 1)}…` : text;
}
