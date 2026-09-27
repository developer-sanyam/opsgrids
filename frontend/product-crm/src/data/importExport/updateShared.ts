/**
 * What the four CRM update specs (ADR-0022, "Addendum: update mode") have in common: reading the
 * whole list to diff against, turning a lookup cell into the id the list stores, comparing text
 * the way a sheet hands it back, and the one rule every list with an amount needs.
 *
 * The specs describe a change; `planUpdate` in `@opsgrids/client` decides what actually differs.
 * Nothing here talks to a user about a row that the engine has already refused: a lookup cell that
 * is not in its list never reaches `resolve` in a real run, and the sentence below only backs up a
 * lookup that changed between the check and the send.
 */
import { RowProblem, collectPages } from "@opsgrids/client";
import type { ExportPage, ImportColumn, ImportContext, ParsedRow, ParsedValue } from "@opsgrids/client";

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

function quote(text: string): string {
  return `'${text.length > 40 ? `${text.slice(0, 37)}…` : text}'`;
}

/**
 * The id the list stores for the label a lookup column's cell names, from the same lookup and by
 * the same rule `toPayload` uses when adding a record. `what` completes "is not ...": "an active
 * sales rep", "a company on file". A name the lookup does not hold, or holds twice, refuses the row.
 */
export function lookupId(column: ImportColumn, what: string, label: string, ctx: ImportContext): string {
  const lookup = column.lookup === undefined ? undefined : ctx.lookups[column.lookup];
  const id = lookup?.resolve(label);
  if (id !== undefined) return id;
  if (lookup?.isAmbiguous(label) === true) {
    throw new RowProblem(`${quote(label)} matches more than one entry for ${column.header}, so it cannot be chosen.`);
  }
  throw new RowProblem(
    `${quote(label)} is not ${what}. Use one from the Lists sheet, or leave ${column.header} blank to keep it as it is.`,
  );
}

/** `UpdateField.resolve` for a lookup column: the label the cell names becomes the id stored. */
export function resolveByLookup(
  column: ImportColumn,
  what: string,
): (value: ParsedValue, ctx: ImportContext) => ParsedValue {
  return (value, ctx) => (typeof value === "string" ? lookupId(column, what, value, ctx) : value);
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
