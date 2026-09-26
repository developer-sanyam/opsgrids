/**
 * What one spreadsheet cell holds, as the engine sees it: a `CellInput`. Everything the file could
 * put in a cell besides a plain value is decided here.
 *
 * A formula cell is never evaluated: its stored result is the value, and an error result is an
 * invalid cell. That is the reading half of the formula-injection rule (ADR-0022). A hyperlink
 * cell is its display text and nothing else: the target is where the link goes, which is not what
 * the cell says, and importing it would put an address the user never saw into a record.
 */

import type * as ExcelJS from "exceljs";

import { InvalidCell, isValidDate } from "./cells";
import type { CellInput } from "./cells";
import { plainNumberString } from "./money";

function errorCell(code: string): InvalidCell {
  return new InvalidCell(code, `contains the error ${code}`);
}

function invalidDate(): InvalidCell {
  return new InvalidCell("#DATE", "is not a valid date");
}

/** The value of a cell as the library reports it. A `null` or `undefined` value is a blank cell. */
export function fromExcelValue(value: ExcelJS.CellValue): CellInput {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  if (value instanceof Date) return isValidDate(value) ? value : invalidDate();
  if ("error" in value) return errorCell(value.error);
  if ("richText" in value) return value.richText.map((run) => run.text).join("");
  // Whatever the display is (text, rich text, a number, a formula's result), read it as that.
  if ("hyperlink" in value) return fromExcelValue(value.text);
  const { result } = value;
  if (result === undefined) {
    return new InvalidCell(
      "=",
      "holds a formula with no calculated value. Open the file in Excel, save it and choose it again",
    );
  }
  if (typeof result === "object" && !(result instanceof Date)) return errorCell(result.error);
  return result instanceof Date && !isValidDate(result) ? invalidDate() : result;
}

/** A number format made only of zeros (`00000`) is how Excel shows a code with its leading zeros. */
function zeroPaddedWidth(format: string | undefined): number | null {
  return format !== undefined && /^0+$/.test(format) ? format.length : null;
}

/**
 * One cell. A whole number formatted `00000` displays "00123" but is stored as 123, and reading
 * the stored value would drop the zeros from a SKU or a code. Such a cell is read as the padded
 * text it shows; a number with any other format is left as the number it is.
 */
export function readCell(cell: ExcelJS.Cell): CellInput {
  const value = fromExcelValue(cell.value);
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    const width = zeroPaddedWidth(cell.numFmt);
    if (width !== null) return (plainNumberString(value) ?? String(value)).padStart(width, "0");
  }
  return value;
}
