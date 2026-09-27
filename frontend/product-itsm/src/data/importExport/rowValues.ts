import { RowProblem } from "@opsgrids/client";
import type { ParsedRow, ParsedValue } from "@opsgrids/client";

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

/** A stored text as an update reads it: an empty string is "nothing there", the same as null. */
export function storedText(value: string | null): string | null {
  return value === null || value === "" ? null : value;
}

/** A currency code as the API stores it: upper case. */
export function upperCased(value: ParsedValue): ParsedValue {
  return typeof value === "string" ? value.toUpperCase() : value;
}
