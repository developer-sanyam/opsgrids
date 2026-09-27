/**
 * What the four Operations import definitions have in common (ADR-0022): reading a parsed row,
 * resolving a name against a lookup, refusing a name that only differs in case from one that
 * exists, the currency cell, and the comparisons an update makes against a stored record. Nothing
 * here knows an entity; each entity file owns its own columns. The lists themselves are read in
 * `referenceData.ts`.
 */
import { RowProblem, normaliseKey, unacceptedLookupValue } from "@opsgrids/client";
import type {
  ColumnOption,
  ImportColumn,
  ImportContext,
  Lookup,
  LookupSet,
  ParsedRow,
  ParsedValue,
} from "@opsgrids/client";

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

/** A column of an entity by key, so a spec cannot name a column it does not have. */
export function columnOf(columns: readonly ImportColumn[], key: string): ImportColumn {
  const found = columns.find((column) => column.key === key);
  if (found === undefined) throw new Error(`The entity has no column "${key}"`);
  return found;
}

/**
 * `UpdateField.resolve` for a lookup column: the value `toPayload` sends for the same cell (a
 * warehouse's name, a unit's label, a category's id), so a cell is compared with what the list
 * stores and not with what the person typed.
 *
 * The engine does not check a lookup cell against its list on a row that updates a record, so this
 * is the only guard: a label the list does not accept refuses the row, in the words the engine uses
 * for a new record and with the column's update hint.
 */
export function resolveLookupCell(column: ImportColumn): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, ctx) => {
    if (typeof value !== "string") return value;
    const lookup = column.lookup === undefined ? undefined : ctx.lookups[column.lookup];
    if (lookup === undefined) {
      throw new RowProblem(`The accepted values for ${column.header} could not be loaded, so it cannot be checked`);
    }
    const resolved = lookup.resolve(value);
    if (resolved === undefined) throw new RowProblem(unacceptedLookupValue(column, value, lookup, true));
    return resolved;
  };
}

/**
 * `UpdateField.same` for a currency cell: an ISO code is the same code in any capitals. A currency
 * beside an amount arrives upper-cased from the engine, but a currency column with no amount (the
 * supplier's) is plain text, and "inr" is not a change of currency.
 */
export function sameCurrency(cell: ParsedValue, stored: ParsedValue): boolean {
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
