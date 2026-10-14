/**
 * Reading a parsed sheet row, and comparing it with the record it updates. The helpers every
 * product's import/export spec reaches for, so each product states only what is its own: which
 * column maps to which field.
 */

import { unacceptedLookupValue } from "./coerce";
import { RowProblem } from "./types";
import type {
  ImportColumn,
  ImportContext,
  Lookup,
  LookupSet,
  ParsedRow,
  ParsedValue,
  UpdateField,
} from "./types";

/** A text cell, or null when it was left blank. */
export function textOf(row: ParsedRow, key: string): string | null {
  const value = row[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * A required text cell. The engine refuses a row with a blank required cell before `toPayload`
 * runs; this narrows the type without a cast and keeps a stray blank a row error, not a crash.
 */
export function requiredTextOf(row: ParsedRow, key: string, header: string): string {
  const value = textOf(row, key);
  if (value === null) throw new RowProblem(`${header} is required.`);
  return value;
}

/** A whole-number cell (money is already integer minor units), or null when blank. */
export function numberOf(row: ParsedRow, key: string): number | null {
  const value = row[key];
  return typeof value === "number" ? value : null;
}

/** An enum cell as the option's API value, or null when blank. */
export function optionOf<T extends string>(
  row: ParsedRow,
  key: string,
  options: readonly { value: T }[],
): T | null {
  const value = row[key];
  return options.find((option) => option.value === value)?.value ?? null;
}

/** The sheet text for an API enum value: its label, or the value itself when it has none. */
export function labelOfOption(
  options: readonly { value: string; label: string }[],
  value: string,
): string {
  return options.find((option) => option.value === value)?.label ?? value;
}

/**
 * The value a name in a cell stands for. The engine has already checked every lookup cell against
 * the list, and says how to fix a miss through the column's `lookupHint`, so a name that does not
 * resolve here means the row did not come through the engine's checks; it is still one row's
 * problem and not a crash.
 */
export function resolveReference(lookup: Lookup | undefined, label: string): string {
  const value = lookup?.resolve(label);
  if (value === undefined) throw new RowProblem(`"${label}" is not in the list.`);
  return value;
}

/** A stored text as an update reads it: an empty string is "nothing there", the same as null. */
export function storedText(value: string | null): string | null {
  return value === null || value === "" ? null : value;
}

/**
 * Compares two texts that may run over several lines, whichever line break each was written with
 * and with their ends trimmed. A sheet hands a text back trimmed, while the server keeps it as it
 * was typed, so a summary that only differs in those ways is not a change (and is not written back,
 * with an audit row, for nothing).
 */
export function sameLines(cell: ParsedValue, stored: ParsedValue): boolean {
  const canonical = (text: string): string => text.replace(/\r\n?/g, "\n").trim();
  return typeof cell === "string" && typeof stored === "string" ? canonical(cell) === canonical(stored) : cell === stored;
}

/** A currency code as the API stores it: upper case. */
export function upperCased(value: ParsedValue): ParsedValue {
  return typeof value === "string" ? value.toUpperCase() : value;
}

/** A column of an entity by key, so a spec cannot name a header it does not have. */
export function columnOf(columns: readonly ImportColumn[], key: string): ImportColumn {
  const found = columns.find((column) => column.key === key);
  if (found === undefined) throw new Error(`The entity has no column "${key}"`);
  return found;
}

function listOf(column: ImportColumn, lookups: LookupSet): Lookup | undefined {
  return column.lookup === undefined ? undefined : lookups[column.lookup];
}

/**
 * What an update compares a lookup cell with. Normally the stored id or code: the cell is looked
 * up and the two ids are compared. But an id the list cannot find again from its own label (two
 * entries carry that label, or the list does not hold the id at all: a member who has left) can
 * only be compared as text, or an export sent back untouched would refuse every such row for a cell
 * nobody changed. `written` is the text an export puts in the cell for this record when that is not
 * the list's own label for the id (a project's own owner name).
 */
export function storedReference(lookup: Lookup | undefined, id: string | null, written?: string): string | null {
  if (id === null) return null;
  const text = (written ?? lookup?.labelOf(id) ?? "").trim();
  return text === "" || lookup?.resolve(text) === id ? id : text;
}

/**
 * `UpdateField.resolve` for a lookup column: the id or code a label stands for. A label the list
 * cannot resolve is handed on as typed, so the diff can still recognise it as the record's own
 * (`storedReference`); `refuseUnlistedReferences` refuses it once the diff says it would be sent.
 */
export function resolveLookup(column: ImportColumn): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, { lookups }) =>
    typeof value === "string" ? (listOf(column, lookups)?.resolve(value) ?? value) : value;
}

/**
 * An update field for a lookup column, emptied with `null`. `stored` reads the id or code off the
 * record; `written` is the text an export writes for it where that is not the list's own label.
 */
export function lookupField<Item>(
  column: ImportColumn,
  stored: (item: Item) => string | null,
  options: { field?: string | undefined; written?: ((item: Item) => string) | undefined } = {},
): UpdateField<Item> {
  return {
    column: column.key,
    field: options.field,
    current: (item, { lookups }) => storedReference(listOf(column, lookups), stored(item), options.written?.(item)),
    resolve: resolveLookup(column),
    clear: null,
  };
}

/**
 * `UpdateSpec.check` for lookup columns, once the diff has said which fields would be sent: a
 * changed lookup cell must name exactly one entry of its list. The engine does not check a lookup
 * cell of a row that updates a record (a record may hold a value the column no longer offers), so
 * an unknown or ambiguous label is refused here, in the engine's own words and with the column's
 * update hint, and only when the cell is a change.
 */
export function refuseUnlistedReferences(
  columns: readonly ImportColumn[],
  row: ParsedRow,
  changed: readonly string[],
  { lookups }: ImportContext,
): void {
  for (const column of columns) {
    const label = row[column.key];
    if (column.lookup === undefined || typeof label !== "string" || !changed.includes(column.header)) continue;
    const lookup = listOf(column, lookups);
    if (lookup === undefined) {
      throw new RowProblem(`The accepted values for ${column.header} could not be loaded, so it cannot be checked`);
    }
    if (lookup.resolve(label) === undefined) throw new RowProblem(unacceptedLookupValue(column, label, lookup, true));
  }
}
