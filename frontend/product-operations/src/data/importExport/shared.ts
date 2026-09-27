/**
 * What the four Operations import definitions have in common (ADR-0022): reading a parsed row,
 * resolving a name against a lookup, refusing a name that only differs in case from one that
 * exists, the currency cell, and the comparisons an update makes against a stored record. Nothing
 * here knows an entity; each entity file owns its own columns. The lists themselves are read in
 * `referenceData.ts`.
 */
import { RowProblem, normaliseKey } from "@opsgrids/client";
import type { ColumnOption, ImportContext, Lookup, LookupSet, ParsedRow, ParsedValue } from "@opsgrids/client";

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

/* ------------------------------ updating an existing record ------------------------------ */

/**
 * `UpdateField.resolve` for a lookup column: the value `toPayload` sends for the same cell (a
 * warehouse's name, a unit's label, a category's id), so a cell is compared with what the list
 * stores and not with what the person typed.
 */
export function resolveLookupCell(
  lookupName: string,
  header: string,
): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, ctx) => (typeof value === "string" ? resolveReference(ctx.lookups, lookupName, value, header) : value);
}

/**
 * `UpdateField.same` for a code that identifies a record (SKU, a supplier, warehouse or category
 * code) and for a currency: equal when they differ only in capitals or spacing. That is how a row
 * finds its record, so a row that found "RM-STL-4501" by typing "rm-stl-4501" is not changing it.
 */
export function sameCode(cell: ParsedValue, stored: ParsedValue): boolean {
  return typeof cell === "string" && typeof stored === "string" && normaliseKey(cell) === normaliseKey(stored);
}

function withLowerDomain(email: string): string {
  const at = email.lastIndexOf("@");
  return at < 0 ? email : email.slice(0, at + 1) + email.slice(at + 1).toLowerCase();
}

/**
 * `UpdateField.same` for an email. A cell has its domain lower-cased but keeps its local part as
 * typed, while the server keeps a stored address exactly as it was first typed, so an address
 * stored as "Sales@Apex.example" would otherwise look changed by its own export.
 */
export function sameEmail(cell: ParsedValue, stored: ParsedValue): boolean {
  return typeof cell === "string" && typeof stored === "string" && withLowerDomain(cell) === withLowerDomain(stored);
}

/** A stored text the API returns as `""` when there is none, as the `null` a blank cell parses to. */
export function textOrNull(stored: string): string | null {
  return stored === "" ? null : stored;
}
