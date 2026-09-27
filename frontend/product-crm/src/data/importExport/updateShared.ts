/**
 * What the four CRM update specs (ADR-0022, "Addendum: update mode") have in common: reading the
 * whole list to diff against, turning a lookup cell into the id the list stores, comparing text
 * the way a sheet hands it back, and the one rule every list with an amount needs.
 *
 * The specs describe a change; `planUpdate` in `@opsgrids/client` decides what actually differs.
 */
import { RowProblem, collectPages, unacceptedLookupValue } from "@opsgrids/client";
import type { ExportPage, ImportColumn, ImportContext, Lookup, ParsedRow, ParsedValue } from "@opsgrids/client";

import type { SpellingLookup } from "./shared";

/**
 * Every record of a list, all pages, each once: what a sheet row is matched and diffed against.
 * Unfiltered on purpose (an update must see records a screen filter would hide), it takes the
 * dialog's signal, and `collectPages` refuses a list longer than an import can read (10,000)
 * instead of matching against part of it.
 */
export function loadEvery<Item extends { id: string }>(
  list: (page: { page: number; size: number; signal: AbortSignal }) => Promise<ExportPage<Item>>,
  ctx: ImportContext,
): Promise<Item[]> {
  return collectPages((page, size) => list({ page, size, signal: ctx.signal }), {
    signal: ctx.signal,
    key: (item) => item.id,
  });
}

/** A column of an entity by key, so a spec cannot name a header it does not have. */
export function columnOf(columns: readonly ImportColumn[], key: string): ImportColumn {
  const found = columns.find((column) => column.key === key);
  if (found === undefined) throw new Error(`The entity has no column "${key}"`);
  return found;
}

/** A stored empty text, which is how the CRM services keep "none", as the `null` an update field
 *  reports for an empty value. */
export function orNull(text: string | null): string | null {
  return text === null || text === "" ? null : text;
}

/**
 * The changed fields as the update function's body type. The body is built from the spec's own
 * `fields`, each keyed as that endpoint names it, so this states what the spec already guarantees.
 */
export function patchOf<Patch>(body: Readonly<Record<string, unknown>>): Patch {
  return body as Patch;
}

function knowsSpelling(lookup: Lookup): lookup is SpellingLookup {
  return "resolveSpelling" in lookup;
}

/**
 * The value a lookup's label names, for a cell that updates a record. Labels match ignoring
 * capitals, and a label that two entries share that way is ambiguous. Where the one spelling the
 * cell holds belongs to exactly one entry, though, it names that entry: an export writes each
 * stored value in its own spelling, so a cell that repeats what is on file always finds it, even
 * beside a twin the server keeps apart (contact emails differing only in capitals), and a person
 * who types the exact spelling of one of two twins has said which. Any other spelling of a shared
 * label is still refused, never guessed.
 */
export function resolveLabel(lookup: Lookup, label: string): string | undefined {
  const value = lookup.resolve(label);
  if (value !== undefined || !lookup.isAmbiguous(label)) return value;
  return knowsSpelling(lookup) ? lookup.resolveSpelling(label) : undefined;
}

/**
 * The id the list stores for the label a lookup column's cell names. The engine does not check a
 * lookup cell on a row that updates a record (a record may hold a value the column no longer offers),
 * so this is the only guard: an unknown label, or one that names two entries, refuses the row with
 * the engine's own sentence and the column's `lookupHintUpdate`. `lookupName` says which lookup
 * judges the label when it is not the column's own.
 */
export function lookupId(
  column: ImportColumn,
  label: string,
  ctx: ImportContext,
  lookupName: string | undefined = column.lookup,
): string {
  const lookup = lookupName === undefined ? undefined : ctx.lookups[lookupName];
  if (lookup === undefined) {
    throw new RowProblem(`The accepted values for ${column.header} could not be loaded, so it cannot be checked`);
  }
  const id = resolveLabel(lookup, label);
  if (id === undefined) throw new RowProblem(unacceptedLookupValue(column, label, lookup, true));
  return id;
}

/** `UpdateField.resolve` for a lookup column: the label the cell names becomes the id stored. */
export function resolveByLookup(
  column: ImportColumn,
  lookupName?: string,
): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, ctx) => (typeof value === "string" ? lookupId(column, value, ctx, lookupName) : value);
}

/**
 * A rep who has left still owns their records, and an export writes them by email, so an owner cell
 * that repeats what a record already has must be accepted whoever that is: the owner field resolves
 * against every rep (`resolveByLookup(column, LOOKUP_ALL_OWNERS)`). Giving a record to a rep who is
 * not active is another matter, and is refused here, once the diff has said the owner changes.
 */
export function refuseInactiveOwner(
  owner: ImportColumn,
  row: ParsedRow,
  changed: readonly string[],
  ctx: ImportContext,
): void {
  if (!changed.includes(owner.header)) return;
  const label = row[owner.key];
  const active = owner.lookup === undefined ? undefined : ctx.lookups[owner.lookup];
  if (typeof label !== "string" || active === undefined || resolveLabel(active, label) !== undefined) return;
  throw new RowProblem(unacceptedLookupValue(owner, label, active, true));
}

/** `UpdateField.resolve` for a currency cell: the server keeps ISO codes upper-cased. */
export function upperCased(value: ParsedValue): ParsedValue {
  return typeof value === "string" ? value.toUpperCase() : value;
}

function canonicalText(value: ParsedValue): ParsedValue {
  return typeof value === "string" ? value.replace(/\r\n?/g, "\n").trim() : value;
}

/**
 * `UpdateField.same` for free text that may run over several lines. A sheet hands a note back
 * with its ends trimmed and its line breaks as it saved them, so a note that only differs in
 * those ways is not a change (and is not written back, with an audit row, for nothing).
 */
export function sameMultiline(cell: ParsedValue, stored: ParsedValue): boolean {
  return canonicalText(cell) === canonicalText(stored);
}

/**
 * The currency of a stored amount cannot change on its own. The services change `currency`
 * without converting the amount beside it, so a currency cell that differs while the amount cell
 * is blank would re-label what is on file (84,000,000 rupees become 84,000,000 dollars). The row
 * must restate the amount in the new currency. An amount of zero has nothing to re-label.
 */
export function refuseCurrencyChangeWithoutAmount(
  money: { amount: ImportColumn; currency: ImportColumn },
  row: ParsedRow,
  changed: readonly string[],
  storedAmount: number,
): void {
  if (!changed.includes(money.currency.header)) return;
  const restated = row[money.amount.key];
  if (restated !== null && restated !== undefined) return;
  if (storedAmount === 0) return;
  const now = row[money.currency.key];
  const currency = typeof now === "string" ? ` in ${now.toUpperCase()}` : "";
  throw new RowProblem(
    `${money.currency.header} cannot change on its own: ${money.amount.header} is not converted, so the amount on file would be read in the new currency. Fill in ${money.amount.header}${currency} too.`,
  );
}
