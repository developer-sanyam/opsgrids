/**
 * What the four Operations import definitions have in common (ADR-0022): reading a parsed row,
 * resolving a name against a lookup, walking a whole paged list, and the currency cell. Nothing
 * here knows an entity; each entity file owns its own columns.
 */
import { RowProblem, collectPages } from "@opsgrids/client";
import type { ColumnOption, ExportPage, LookupSet, ParsedRow } from "@opsgrids/client";

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

export interface ReferenceProblems {
  /** The sentence when no entry carries the name. */
  unknown(label: string): string;
  /** The sentence when several entries carry it, so the importer will not guess. */
  ambiguous(label: string): string;
}

/**
 * The value the API wants for a name in the sheet (a warehouse name, a unit code, a category id).
 * A name that resolves to nothing, or to more than one record, fails that one row: the importer
 * would rather refuse than attach a stock item to the wrong warehouse.
 */
export function resolveReference(
  lookups: LookupSet,
  lookupName: string,
  label: string,
  problems: ReferenceProblems,
): string {
  const lookup = lookups[lookupName];
  if (lookup?.isAmbiguous(label)) throw new RowProblem(problems.ambiguous(label));
  const value = lookup?.resolve(label);
  if (value === undefined) throw new RowProblem(problems.unknown(label));
  return value;
}

/** The label of an enum option for a value the API returned; an unlisted value is written as is. */
export function optionLabel(options: readonly ColumnOption[], value: string): string {
  return options.find((option) => option.value === value)?.label ?? value;
}

/* ------------------------------ whole lists ------------------------------ */

/**
 * Every record of a paged list, each once. The lists behind the lookups are ordered by name, which
 * is not unique (two item categories can share a name), and offset paging over ties can serve the
 * same row on two pages. A duplicated record would make its own code look ambiguous, so records are
 * de-duplicated by id here.
 */
export async function collectAll<T extends { id: string }>(
  fetchPage: (page: number, size: number) => Promise<ExportPage<T>>,
  signal: AbortSignal,
): Promise<T[]> {
  const rows = await collectPages(fetchPage, { signal });
  const seen = new Set<string>();
  return rows.filter((row) => {
    if (seen.has(row.id)) return false;
    seen.add(row.id);
    return true;
  });
}

/* ------------------------------ currency ------------------------------ */

/** The three-letter code a currency cell carries, upper-cased as the API stores it. */
export const CURRENCY_PATTERN = /^[A-Za-z]{3}$/;
export const CURRENCY_HINT = "Three letters, for example INR, USD or EUR.";
