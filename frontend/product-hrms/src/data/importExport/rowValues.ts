import { RowProblem } from "@opsgrids/client";
import type { Lookup, ParsedRow } from "@opsgrids/client";

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
