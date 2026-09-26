/**
 * What the four Operations import definitions have in common (ADR-0022): reading a parsed row,
 * resolving a name against a lookup, refusing a name that only differs in case from one that
 * exists, and the currency cell. Nothing here knows an entity; each entity file owns its own
 * columns. The lists themselves are read in `referenceData.ts`.
 */
import { RowProblem } from "@opsgrids/client";
import type { ColumnOption, Lookup, LookupSet, ParsedRow } from "@opsgrids/client";

import type { Category } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";

/** The procurement category an item or a supplier carries, with the labels the screens use. */
export const CATEGORY_OPTIONS: readonly { value: Category; label: string }[] = (
  ["raw_materials", "packaging", "components", "mro", "logistics", "services"] as const
).map((value) => ({ value, label: CATEGORY_LABEL[value] }));

/* ------------------------------ reading a parsed row ------------------------------ */

/** The trimmed text of a cell, or null when it is blank. */
export function optionalText(row: ParsedRow, key: string): string | null {
  const value = row[key];
  if (typeof value === "number") return String(value);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** As `optionalText`, but a blank cell fails the row. The engine already refuses a blank required
 *  cell; this is what lets `toPayload` hand the API a `string` without a non-null assertion. */
export function requiredText(row: ParsedRow, key: string, header: string): string {
  const value = optionalText(row, key);
  if (value === null) throw new RowProblem(`${header} is required.`);
  return value;
}

export function optionalNumber(row: ParsedRow, key: string): number | null {
  const value = row[key];
  return typeof value === "number" ? value : null;
}

export function optionalBoolean(row: ParsedRow, key: string): boolean | null {
  const value = row[key];
  return typeof value === "boolean" ? value : null;
}

/** The value of an enum column, checked against the options the column offers. */
export function requiredOption<T extends string>(
  row: ParsedRow,
  key: string,
  header: string,
  options: readonly { value: T }[],
): T {
  const value = requiredText(row, key, header);
  const match = options.find((option) => option.value === value);
  if (match === undefined) {
    throw new RowProblem(`${header} "${value}" is not one of the accepted values.`);
  }
  return match.value;
}

/** As `requiredOption`, for a column that may be left blank. */
export function optionalOption<T extends string>(
  row: ParsedRow,
  key: string,
  header: string,
  options: readonly { value: T }[],
): T | null {
  return optionalText(row, key) === null ? null : requiredOption(row, key, header, options);
}

/* ------------------------------ resolving a name ------------------------------ */

/**
 * The value the API wants for a name in the sheet (a warehouse name, a unit code, a category id).
 *
 * The engine has already refused a name the lookup cannot resolve, with the column's own
 * `lookupHint`, before `toPayload` runs; this only keeps `toPayload` total when it is called
 * without that check (a lookup that was never loaded), failing the row rather than sending a guess.
 */
export function resolveReference(lookups: LookupSet, lookupName: string, label: string, header: string): string {
  const value = lookups[lookupName]?.resolve(label);
  if (value === undefined) {
    throw new RowProblem(`${header} "${label}" does not match exactly one entry on the Lists sheet.`);
  }
  return value;
}

/**
 * Refuses a name that matches one the organisation already has when capitals and spacing are
 * ignored, but is not spelled the same. The server accepts such a name (it compares exactly), and
 * two records that differ only in case make every later reference to either of them ambiguous:
 * the lookups behind the inventory import would call both unresolvable. A name spelled exactly as
 * an existing one is left alone, so the server answers it with its own "already exists" and the
 * row is reported as skipped. `describe` gets the existing spelling, or null when several exist.
 *
 * `spellings` maps each existing name to itself (label and value both the exact spelling), so
 * `labelOf(typed)` says whether that exact spelling exists. `labels` cannot say: a lookup leaves
 * out a label that more than one record carries, and two existing case variants are exactly that.
 */
export function refuseNearDuplicate(
  spellings: Lookup | undefined,
  typed: string,
  describe: (existing: string | null) => string,
): void {
  if (spellings === undefined || spellings.labelOf(typed) !== undefined) return;
  const existing = spellings.resolve(typed);
  if (existing === undefined && !spellings.isAmbiguous(typed)) return;
  throw new RowProblem(describe(existing ?? null));
}

/** The label of an enum option for a value the API returned; an unlisted value is written as is. */
export function optionLabel(options: readonly ColumnOption[], value: string): string {
  return options.find((option) => option.value === value)?.label ?? value;
}

/* ------------------------------ currency ------------------------------ */

/** The three-letter code a currency cell carries, upper-cased as the API stores it. */
export const CURRENCY_PATTERN = /^[A-Za-z]{3}$/;
export const CURRENCY_HINT = "Three letters, for example INR, USD or EUR.";
