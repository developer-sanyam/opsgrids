import { RowProblem, normaliseKey, unacceptedLookupValue } from "@opsgrids/client";
import type { ImportColumn, ImportContext, Lookup, ParsedRow, ParsedValue } from "@opsgrids/client";

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
 * Compares a cell with a stored key the way the engine matched the row to its record: ignoring
 * case and runs of spaces. An update that found `emp-0007` on the record `EMP-0007` must not then
 * refuse the row for "changing" its number.
 */
export function sameKey(cell: ParsedValue, stored: ParsedValue): boolean {
  return typeof cell === "string" && typeof stored === "string" && normaliseKey(cell) === normaliseKey(stored);
}

/** A column of an entity by key, so a spec cannot name a header it does not have. */
export function columnOf(columns: readonly ImportColumn[], key: string): ImportColumn {
  const found = columns.find((column) => column.key === key);
  if (found === undefined) throw new Error(`The entity has no column "${key}"`);
  return found;
}

/**
 * `UpdateField.resolve` for a lookup column: the cell's label as the id or code the record stores.
 * The engine does not check a lookup cell of a row that updates a record (blank leaves it alone),
 * so this is where an unknown or ambiguous label is refused, in the engine's own words and with the
 * column's update hint.
 */
export function resolveLookup(column: ImportColumn): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, { lookups }) => {
    if (typeof value !== "string") return value;
    const lookup = column.lookup === undefined ? undefined : lookups[column.lookup];
    if (lookup === undefined) {
      throw new RowProblem(`The accepted values for ${column.header} could not be loaded, so it cannot be checked`);
    }
    const resolved = lookup.resolve(value);
    if (resolved === undefined) throw new RowProblem(unacceptedLookupValue(column, value, lookup, true));
    return resolved;
  };
}
