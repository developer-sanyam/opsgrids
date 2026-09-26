/**
 * A lint for an `ImportEntity` definition. Each product describes its lists once, and the sample
 * sheet, the importer and the exporter are all built from that description, so a mistake in it
 * (a lookup nobody loads, a sample row that fails its own validation) ships to every customer.
 * Entity authors call `checkImportEntity` from their own test with lookups shaped like the real
 * ones and expect an empty array.
 */

import { coerceRow } from "./coerce";
import { headerKey, importableColumns } from "./cells";
import type { AnyEntity } from "./cells";
import { substituteSampleRows } from "./sample";
import type { ImportColumn, LookupSet } from "./types";

function duplicates(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const repeated = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) repeated.add(value);
    seen.add(value);
  }
  return [...repeated];
}

function checkColumn(entity: AnyEntity, column: ImportColumn, lookups: LookupSet): string[] {
  const problems: string[] = [];
  const name = `Column "${column.key}"`;
  if (column.header.trim() === "") problems.push(`${name} has no header`);
  if (column.help.trim() === "") problems.push(`${name} has no help text for the Instructions sheet`);
  if (column.min !== undefined && column.max !== undefined && column.min > column.max) {
    problems.push(`${name} has min above max`);
  }
  if (column.maxLength !== undefined && column.maxLength < 1) problems.push(`${name} has a maxLength below 1`);
  if (column.pattern !== undefined && column.patternHint === undefined) {
    problems.push(`${name} has a pattern but no patternHint, so a user cannot tell what is expected`);
  }
  if (column.lookupHint !== undefined && column.lookup === undefined) {
    problems.push(`${name} has a lookupHint but is not a lookup column`);
  }
  if (column.multiline === true && column.kind !== "text") {
    problems.push(`${name} is marked multiline but is not a text column`);
  }
  if (column.readOnly === true && column.importOnly === true) {
    problems.push(`${name} is both readOnly and importOnly, so it would appear nowhere`);
  }
  if (column.readOnly === true) return problems;

  if (column.kind === "enum") {
    const options = column.options ?? [];
    if (options.length === 0) problems.push(`${name} is an enum with no options`);
    const owner = new Map<string, number>();
    options.forEach((option, index) => {
      for (const spelling of new Set([option.label, option.value].map((text) => text.trim().toLowerCase()))) {
        const previous = owner.get(spelling);
        if (previous !== undefined && previous !== index) {
          problems.push(`${name} accepts "${spelling}" for two different options`);
        }
        owner.set(spelling, index);
      }
    });
  } else if (column.options !== undefined) {
    problems.push(`${name} has options but is not an enum`);
  }

  if (column.lookup !== undefined) {
    if (column.kind !== "text") problems.push(`${name} uses a lookup, so its kind must be "text"`);
    if (lookups[column.lookup] === undefined) {
      problems.push(`${name} uses lookup "${column.lookup}", which loadLookups does not provide`);
    }
  }

  if (column.kind === "money") {
    const key = column.currencyKey ?? "currency";
    const holder = entity.columns.find((other) => other.key === key);
    if (holder === undefined) {
      if (column.defaultCurrency === undefined) {
        problems.push(
          `${name} is money but there is no "${key}" currency column; add one, or set defaultCurrency for a single-currency list`,
        );
      }
    } else if (holder.readOnly === true) {
      problems.push(`${name} takes its currency from "${key}", which is read-only and ignored on import`);
    }
  }
  return problems;
}

/** Every problem with the definition, as sentences. Empty when the entity is sound. */
export function checkImportEntity(entity: AnyEntity, lookups: LookupSet): string[] {
  const problems: string[] = [];
  for (const field of ["id", "noun", "nounSingular", "fileStem"] as const) {
    if (entity[field].trim() === "") problems.push(`The entity has an empty ${field}`);
  }
  if (entity.maxRows !== undefined && entity.maxRows < 1) problems.push("maxRows must be at least 1");
  if (entity.columns.length === 0) problems.push("The entity has no columns");

  for (const key of duplicates(entity.columns.map((column) => column.key))) {
    problems.push(`More than one column has the key "${key}"`);
  }
  for (const header of duplicates(entity.columns.map((column) => headerKey(column.header)))) {
    problems.push(`More than one column has the header "${header}" (headers are matched ignoring case, spaces and a trailing *)`);
  }
  for (const column of entity.columns) problems.push(...checkColumn(entity, column, lookups));

  if (entity.recordIdKey !== undefined) {
    const holder = entity.columns.find((column) => column.key === entity.recordIdKey);
    if (holder === undefined) {
      problems.push(`recordIdKey "${entity.recordIdKey}" names no column`);
    } else if (holder.readOnly !== true) {
      problems.push(`recordIdKey "${entity.recordIdKey}" must name a readOnly column: the export's own id`);
    }
  }

  const known = new Map(entity.columns.map((column) => [column.key, column]));
  const importable = importableColumns(entity);
  if (entity.sampleRows.length === 0) problems.push("There are no sample rows for the sample sheet");

  const ctx = { lookups, signal: new AbortController().signal };
  // What the sample sheet actually holds: an example naming something the organisation lacks is
  // swapped for one it has, so that is the row that must pass.
  const written = substituteSampleRows(entity, lookups);
  entity.sampleRows.forEach((sample, index) => {
    const label = `Sample row ${index + 1}`;
    for (const key of Object.keys(sample)) {
      const column = known.get(key);
      if (column === undefined) problems.push(`${label} has the unknown key "${key}"`);
      else if (column.readOnly === true) problems.push(`${label} has a value for "${key}", which is read-only`);
    }
    for (const column of importable) {
      const value = sample[column.key];
      const blank = value === undefined || value === null || (typeof value === "string" && value.trim() === "");
      if (column.required === true && blank) problems.push(`${label} has no value for required "${column.key}"`);
    }
    const { row, errors } = coerceRow(entity, written[index] ?? sample, ctx);
    for (const error of errors) problems.push(`${label} does not pass its own validation: ${error}`);
    if (errors.length === 0) {
      try {
        entity.toPayload(row, ctx);
      } catch (error) {
        const reason = error instanceof Error ? error.message : "it threw";
        problems.push(`${label} cannot be turned into a payload: ${reason}`);
      }
    }
  });
  return problems;
}
