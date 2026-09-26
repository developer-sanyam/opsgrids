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
 * The id a name in a cell stands for. `missing` and `ambiguous` are the sentences shown against
 * the row, so each reference column can say what to do about it.
 */
export function resolveReference(
  lookup: Lookup | undefined,
  label: string,
  problems: { missing: string; ambiguous: string },
): string {
  const value = lookup?.resolve(label);
  if (value !== undefined) return value;
  throw new RowProblem(lookup?.isAmbiguous(label) ? problems.ambiguous : problems.missing);
}

const labelsByLookup = new WeakMap<Lookup, ReadonlyMap<string, string>>();

/**
 * The label a lookup gives an id: the reverse of `resolve`, for writing a reference back to a
 * sheet. Built once per lookup, because an export asks it for every row.
 */
export function labelOfValue(lookup: Lookup | undefined, value: string | null): string {
  if (lookup === undefined || value === null) return "";
  let byValue = labelsByLookup.get(lookup);
  if (byValue === undefined) {
    const built = new Map<string, string>();
    for (const label of lookup.labels) {
      const resolved = lookup.resolve(label);
      if (resolved !== undefined) built.set(resolved, label);
    }
    byValue = built;
    labelsByLookup.set(lookup, byValue);
  }
  return byValue.get(value) ?? "";
}
