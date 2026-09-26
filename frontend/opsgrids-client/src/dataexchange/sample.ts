/**
 * The example rows of the sample sheet, and recognising them again on import. A user who leaves
 * them in the file should not create three fictional records, and should not be told three rows
 * are wrong either: an unchanged sample row is skipped without a message.
 *
 * The examples an entity ships name things (a warehouse, a department, an owner) that may not
 * exist in a given organisation. The sample sheet therefore swaps such a name for the first one
 * the organisation does have; the importer accepts a row that matches either version, because it
 * cannot know which sheet the file was built from.
 *
 * Rows are compared column by column after both sides are reduced to a comparable form, so a
 * sample written as `"1499.50"` still matches the number Excel hands back, and an enum written by
 * value matches the label the dropdown put in the cell.
 */

import { InvalidCell, displayValue, importableColumns } from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import type { ImportColumn, LookupSet, SheetValue } from "./types";

const YES = new Set(["yes", "y", "true", "1"]);
const NO = new Set(["no", "n", "false", "0"]);

/**
 * The entity's sample rows as the sample sheet writes them: where a lookup column's example is
 * not one the organisation has, the first label of the lookup that actually resolves stands in
 * for it (never an ambiguous one: it would be refused on the way back in). A lookup with nothing
 * usable in it leaves the example alone, and a blank example stays blank.
 */
export function substituteSampleRows(entity: AnyEntity, lookups: LookupSet): Record<string, SheetValue>[] {
  const lookupColumns = importableColumns(entity).filter((column) => column.lookup !== undefined);
  return entity.sampleRows.map((sample) => {
    const row: Record<string, SheetValue> = { ...sample };
    for (const column of lookupColumns) {
      const lookup = column.lookup === undefined ? undefined : lookups[column.lookup];
      const example = displayValue(sample[column.key]);
      if (lookup === undefined || example === "" || lookup.resolve(example) !== undefined) continue;
      const standIn = lookup.labels.find((label) => lookup.resolve(label) !== undefined);
      if (standIn !== undefined) row[column.key] = standIn;
    }
    return row;
  });
}

function comparable(column: ImportColumn, cell: CellInput | undefined): string {
  if (cell instanceof InvalidCell) return `\u0000${cell.shown}`;
  const shown = displayValue(cell);
  const lower = shown.toLowerCase();
  switch (column.kind) {
    case "integer":
    case "decimal":
    case "money":
      return /^-?\d*\.\d+$/.test(shown) ? shown.replace(/0+$/, "").replace(/\.$/, "") : shown;
    case "boolean":
      if (YES.has(lower)) return "yes";
      return NO.has(lower) ? "no" : lower;
    case "enum": {
      const match = (column.options ?? []).find(
        (option) => option.label.toLowerCase() === lower || option.value.toLowerCase() === lower,
      );
      return match === undefined ? lower : match.value.toLowerCase();
    }
    default:
      return lower;
  }
}

/**
 * A test for "is this row an unchanged example": every importable column the file has equals the
 * same column of one sample row, either as the entity ships it or as the sample sheet wrote it for
 * this organisation. Only the columns the file has are compared. A user who deletes an optional
 * column and leaves the examples in has not turned them into records, and a column that is not in
 * the file cannot be told apart from one that was cleared. Reordering columns, or adding one the
 * entity does not know, changes nothing; editing or clearing a cell of a column that is there makes
 * the row a real one.
 */
export function sampleMatcher(
  entity: AnyEntity,
  lookups: LookupSet,
): (cells: Readonly<Record<string, CellInput>>) => boolean {
  const columns = importableColumns(entity);
  const candidates = [...entity.sampleRows, ...substituteSampleRows(entity, lookups)];
  return (cells) => {
    const present = columns.filter((column) => Object.hasOwn(cells, column.key));
    if (present.length === 0) return false;
    return candidates.some((sample) =>
      present.every((column) => comparable(column, cells[column.key]) === comparable(column, sample[column.key])),
    );
  };
}
