/**
 * One cell to one typed value, per `ColumnKind`, and one sheet row to one `ParsedRow`. Every
 * refusal is a sentence a person can act on ("Price can have at most 2 decimal places for INR"),
 * because it lands next to a row number in the preview and in the result report.
 *
 * Nothing here guesses. A phone with no country code, a date like 03/04/2026, an amount with a
 * thousands separator: each is refused with the format that would have been accepted, because a
 * wrong guess is a wrong record.
 */

import {
  InvalidCell,
  importableColumns,
  isBlank,
  isValidDate,
  isoDate,
  utcMidnight,
} from "./cells";
import type { AnyEntity, CellInput } from "./cells";
import { EMAIL_MAX, normaliseEmail } from "./email";
import { normalisePhone } from "../phone";
import { compareDecimals, parseMajorUnits, plainNumberString, describeMoneyProblem } from "./money";
import type { ImportColumn, ImportContext, Lookup, LookupSet, ParsedRow, ParsedValue } from "./types";
import { RowProblem } from "./types";
import { isClearToken } from "./update";

export type Coerced = { ok: true; value: ParsedValue } | { ok: false; error: string };

/** What a column may need beyond its own cell: the row's currency, and the loaded lookups. */
export interface CoerceEnv {
  lookups: LookupSet;
  /** money: the currency the row's amount is in. Defaults to the column's `defaultCurrency`, then INR. */
  currency?: string | undefined;
  /** Said after "<Header> is required" when a cell is missing: which rows need it, in update mode. */
  requiredNote?: string | undefined;
  /** A lookup column's cell is plain text, not checked against the lookup: the row updates a record,
   *  and the update field's own `resolve` judges the label (it may accept what the record already has). */
  plainLookups?: boolean | undefined;
}

const DEFAULT_CURRENCY = "INR";
const ENUM_LIST_LIMIT = 12;

const TRUE_WORDS = new Set(["yes", "y", "true", "1"]);
const FALSE_WORDS = new Set(["no", "n", "false", "0"]);

const ok = (value: ParsedValue): Coerced => ({ ok: true, value });
const fail = (error: string): Coerced => ({ ok: false, error });
const required = (column: ImportColumn, env: CoerceEnv): Coerced =>
  fail(`${column.header} is required${env.requiredNote ?? ""}`);

function quote(text: string): string {
  return `'${text.length > 40 ? `${text.slice(0, 37)}…` : text}'`;
}

function normalise(text: string): string {
  return text.trim().replace(/\s+/g, " ").toLowerCase();
}

/** A control character other than, when `allowLineBreaks`, a tab or a line break: those are how a
 *  multi-line note or summary is typed in a cell, so free text keeps them. */
function hasControlCharacter(text: string, allowLineBreaks = false): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (allowLineBreaks && (code === 0x09 || code === 0x0a || code === 0x0d)) continue;
    if (code < 0x20 || (code >= 0x7f && code <= 0x9f)) return true;
  }
  return false;
}

/** The text a cell holds, for a column that stores text. Null when the cell is not text-like. */
function textOf(cell: CellInput): string | null {
  if (typeof cell === "string") return cell.trim();
  if (typeof cell === "number") return plainNumberString(cell);
  if (typeof cell === "boolean") return cell ? "TRUE" : "FALSE";
  return null;
}

/**
 * A real date cell where text belongs is Excel's doing: "1-10" or "11-50" typed into a General
 * cell becomes 10 January or 11 May. Accepting it as an ISO date would import the wrong text, so
 * it is refused with the way out.
 */
function excelDateInTextColumn(column: ImportColumn): Coerced {
  return fail(
    `${column.header} was turned into a date by Excel (for example 1-10 becomes 10 January). Format the column as Text and type it again.`,
  );
}

/** The text a number-typed cell holds: digits as typed, or the plain form of a number cell. */
function numericTextOf(cell: CellInput): string | null {
  if (typeof cell === "string") return cell.trim();
  if (typeof cell === "number") return plainNumberString(cell);
  return null;
}

function outOfBounds(column: ImportColumn, value: number): string | null {
  const { min, max, header } = column;
  if (min !== undefined && value < min) {
    return max !== undefined ? `${header} must be between ${min} and ${max}` : `${header} must be at least ${min}`;
  }
  if (max !== undefined && value > max) {
    return min !== undefined ? `${header} must be between ${min} and ${max}` : `${header} must be at most ${max}`;
  }
  return null;
}

function coerceText(column: ImportColumn, cell: CellInput, env: CoerceEnv): Coerced {
  const { header } = column;
  if (cell instanceof Date) return excelDateInTextColumn(column);
  const text = textOf(cell);
  if (text === null) return fail(`${header} must be text`);
  if (hasControlCharacter(text, column.multiline === true)) {
    return fail(
      column.multiline === true
        ? `${header} contains a control character`
        : `${header} contains a line break, tab or another control character`,
    );
  }
  if (column.maxLength !== undefined) {
    const length = Array.from(text).length;
    if (length > column.maxLength) {
      return fail(`${header} is too long (${length} characters; the limit is ${column.maxLength})`);
    }
  }
  if (column.pattern !== undefined) {
    // A copy without `g` / `y`: those make `test` remember where it stopped, and the next row
    // would be judged by the previous one.
    const stateless = new RegExp(column.pattern.source, column.pattern.flags.replace(/[gy]/g, ""));
    if (!stateless.test(text)) {
      const hint = column.patternHint !== undefined ? `: ${column.patternHint}` : "";
      return fail(`${header} is not in the expected format${hint}`);
    }
  }
  if (column.lookup !== undefined && env.plainLookups !== true) return coerceLookup(column, column.lookup, text, env);
  return ok(text);
}

/**
 * The sentence for a lookup label the list does not accept (unknown, or naming more than one entry),
 * ending in the column's hint for putting it right. `updating` picks `lookupHintUpdate` where the
 * column has one: on a row that updates a record, leaving a cell blank means leaving the field alone,
 * which the add-mode hint ("leave it blank to assign it to yourself") may contradict.
 *
 * An update field's `resolve` is where a matched row's lookup label is judged, so it uses this too.
 */
export function unacceptedLookupValue(column: ImportColumn, text: string, lookup: Lookup, updating: boolean): string {
  const written = updating ? (column.lookupHintUpdate ?? column.lookupHint) : column.lookupHint;
  const hint = written === undefined ? "" : ` ${written}`;
  if (lookup.isAmbiguous(text)) {
    return `${quote(text)} matches more than one of the accepted values for ${column.header}, so it cannot be chosen.${hint}`;
  }
  return `${quote(text)} is not one of the accepted values for ${column.header} (see the Lists sheet).${hint}`;
}

function coerceLookup(column: ImportColumn, name: string, text: string, env: CoerceEnv): Coerced {
  const lookup = env.lookups[name];
  if (lookup === undefined) {
    return fail(`The accepted values for ${column.header} could not be loaded, so it cannot be checked`);
  }
  if (lookup.resolve(text) !== undefined) return ok(text);
  return fail(unacceptedLookupValue(column, text, lookup, false));
}

function coerceInteger(column: ImportColumn, cell: CellInput): Coerced {
  const text = numericTextOf(cell);
  if (text === null || !/^-?\d+$/.test(text)) return fail(`${column.header} must be a whole number`);
  const value = Number(text);
  if (!Number.isSafeInteger(value)) return fail(`${column.header} is too large`);
  const bound = outOfBounds(column, value);
  return bound === null ? ok(value) : fail(bound);
}

function coerceDecimal(column: ImportColumn, cell: CellInput): Coerced {
  const text = numericTextOf(cell);
  const plain = text === null ? null : /^-?(?:\d+(?:\.\d+)?|\.\d+)$/.exec(text)?.[0];
  if (plain === null || plain === undefined) {
    return fail(`${column.header} must be a plain number, for example 12.5 (no commas, no exponent)`);
  }
  const value = Number(plain);
  if (!Number.isFinite(value)) return fail(`${column.header} is too large`);
  const bound = outOfBounds(column, value);
  return bound === null ? ok(value) : fail(bound);
}

function coerceMoney(column: ImportColumn, cell: CellInput, env: CoerceEnv): Coerced {
  const currency = env.currency ?? column.defaultCurrency ?? DEFAULT_CURRENCY;
  const text = numericTextOf(cell);
  if (text === null) return fail(`${column.header} must be a plain number, for example 1499.50`);
  const parsed = parseMajorUnits(text, currency);
  if (!parsed.ok) return fail(`${column.header} ${describeMoneyProblem(parsed.problem, currency)}`);
  const { min, max } = column;
  const tooLow = min !== undefined && compareDecimals(parsed.major, plainNumberString(min) ?? "0") < 0;
  const tooHigh = max !== undefined && compareDecimals(parsed.major, plainNumberString(max) ?? "0") > 0;
  if (tooLow || tooHigh) {
    if (min !== undefined && max !== undefined) return fail(`${column.header} must be between ${min} and ${max}`);
    return fail(tooLow ? `${column.header} must be at least ${min}` : `${column.header} must be at most ${max}`);
  }
  return ok(parsed.minor);
}

function coerceDate(column: ImportColumn, cell: CellInput): Coerced {
  const { header } = column;
  const example = "YYYY-MM-DD, e.g. 2026-03-04";
  if (cell instanceof Date) {
    const year = cell.getUTCFullYear();
    if (!isValidDate(cell) || year < 1900 || year > 9999) return fail(`${header} is not a valid date`);
    return ok(isoDate(cell));
  }
  if (typeof cell !== "string") return fail(`${header} must be a date written as ${example}`);
  const text = cell.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const date = utcMidnight(text);
    if (date === null || date.getUTCFullYear() < 1900) return fail(`${header} ${quote(text)} is not a real date`);
    return ok(text);
  }
  if (/^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$/.test(text)) {
    return fail(`${header} ${quote(text)} is ambiguous (day first or month first). Write dates as ${example}`);
  }
  return fail(`${header} must be a date written as ${example}`);
}

function coerceBoolean(column: ImportColumn, cell: CellInput): Coerced {
  if (typeof cell === "boolean") return ok(cell);
  const text = numericTextOf(cell)?.toLowerCase();
  if (text !== undefined) {
    if (TRUE_WORDS.has(text)) return ok(true);
    if (FALSE_WORDS.has(text)) return ok(false);
  }
  return fail(`${column.header} must be Yes or No`);
}

function coerceEnum(column: ImportColumn, cell: CellInput): Coerced {
  const options = column.options ?? [];
  if (cell instanceof Date) return excelDateInTextColumn(column);
  const text = textOf(cell);
  if (text === null || text === "") return fail(`${column.header} must be one of the allowed values`);
  const wanted = normalise(text);
  const match =
    options.find((option) => normalise(option.label) === wanted) ??
    options.find((option) => normalise(option.value) === wanted);
  if (match !== undefined) return ok(match.value);
  const labels = options.map((option) => option.label);
  const shown =
    labels.length > ENUM_LIST_LIMIT
      ? `${labels.slice(0, ENUM_LIST_LIMIT).join(", ")} and ${labels.length - ENUM_LIST_LIMIT} more (see the Instructions sheet)`
      : labels.join(", ");
  return fail(`${quote(text)} is not one of the allowed values for ${column.header}: ${shown}`);
}

function coerceEmail(column: ImportColumn, cell: CellInput): Coerced {
  const { header } = column;
  const invalid = fail(`${header} must be a valid email address, e.g. name@example.com`);
  if (typeof cell !== "string") return invalid;
  const trimmed = cell.trim();
  if (hasControlCharacter(trimmed)) return invalid;
  const limit = Math.min(column.maxLength ?? EMAIL_MAX, EMAIL_MAX);
  if (trimmed.length > limit) return fail(`${header} is too long (${trimmed.length} characters; the limit is ${limit})`);
  const address = normaliseEmail(trimmed);
  return address === null ? invalid : ok(address);
}

/**
 * A phone cell, as E.164. A number with a country code is taken as written; a bare national number
 * is completed only for an organization whose own country is India (`normalisePhone`: ten digits
 * starting 6-9, with or without a leading 0 or 91) and refused for every other. Nothing is guessed.
 */
function coercePhone(column: ImportColumn, cell: CellInput): Coerced {
  const text = textOf(cell);
  const phone = text === null ? null : normalisePhone(text);
  if (phone === null) {
    return fail(`${column.header} must start with + and the country code, e.g. +919876543210`);
  }
  return ok(phone);
}

/**
 * Coerces one cell for one column. A blank cell is `null` (or "<Header> is required"); everything
 * else is judged by the column's kind. `env.currency` is the money column's row currency.
 */
export function coerceValue(column: ImportColumn, cell: CellInput | undefined, env: CoerceEnv): Coerced {
  if (cell instanceof InvalidCell) return fail(`${column.header} ${cell.reason}`);
  if (cell === undefined || cell === null || isBlank(cell)) {
    return column.required === true ? required(column, env) : ok(null);
  }
  switch (column.kind) {
    case "text":
      return coerceText(column, cell, env);
    case "integer":
      return coerceInteger(column, cell);
    case "decimal":
      return coerceDecimal(column, cell);
    case "money":
      return coerceMoney(column, cell, env);
    case "date":
      return coerceDate(column, cell);
    case "boolean":
      return coerceBoolean(column, cell);
    case "enum":
      return coerceEnum(column, cell);
    case "email":
      return coerceEmail(column, cell);
    case "phone":
      return coercePhone(column, cell);
  }
}

/** The currency a money column falls back to when its row names none. */
function fallbackCurrency(entity: Pick<AnyEntity, "columns">, money: ImportColumn): string {
  const holder = entity.columns.find((column) => column.key === (money.currencyKey ?? "currency"));
  return (money.defaultCurrency ?? holder?.defaultCurrency ?? DEFAULT_CURRENCY).toUpperCase();
}

/**
 * The currency named by one row: its own cell, else the fallback. An error sentence when the cell
 * is unusable. A row that updates a record may leave the currency blank even where the column is
 * required (blank leaves the field alone), so `allowBlank` skips that check.
 */
function resolveCurrency(
  holder: ImportColumn,
  cell: CellInput | undefined,
  fallback: string,
  env: CoerceEnv,
  allowBlank: boolean,
): Coerced {
  if (isBlank(cell)) return holder.required === true && !allowBlank ? required(holder, env) : ok(fallback);
  const coerced = coerceValue(holder, cell, env);
  if (!coerced.ok) return coerced;
  const code = String(coerced.value).toUpperCase();
  if (!/^[A-Z]{3}$/.test(code)) {
    return fail(`${holder.header} must be a three-letter currency code, e.g. INR`);
  }
  return ok(code);
}


/** What a row is for. A row that names an existing record is coerced differently from one that creates a record. */
export type RowIntent =
  | {
      kind: "create";
      /** The dialog is in update mode and this row matched no record: a missing required cell is
       *  then said to be needed "to add a new record", since a row that updates one needs none. */
      whileUpdating?: boolean;
    }
  | {
      kind: "update";
      /** The stored record's currency (`UpdateSpec.currencyOf`), used for a money cell when the row names none. */
      storedCurrency: string | null;
    };

export const CLEAR_REFUSED_MESSAGE = "[clear] only works when updating existing records";

export interface CoercedRow {
  row: ParsedRow;
  errors: string[];
  /** Update rows: the columns whose cell said `[clear]`. They are `null` in `row`, like a blank cell,
   *  but a blank cell leaves the field alone and these empty it. */
  cleared: ReadonlySet<string>;
}

/**
 * Coerces every importable column of one sheet row, collecting every column's error rather than
 * stopping at the first, then (for a row that creates a record) runs the entity's cross-column
 * `validateRow` when the columns are clean (a rule over half-parsed values would only repeat the
 * errors already listed).
 *
 * A row that updates a record differs in four ways: `[clear]` is read before the cell's own type
 * (and empties the field), a blank cell is `null` and never "required" (blank leaves the field
 * alone), a blank currency cell stays `null` instead of becoming the default currency, and a money
 * cell with no currency is converted in the stored record's currency.
 */
export function coerceRow(
  entity: AnyEntity,
  cells: Readonly<Record<string, CellInput>>,
  ctx: ImportContext,
  intent: RowIntent = { kind: "create" },
): CoercedRow {
  const columns = importableColumns(entity);
  const updating = intent.kind === "update";
  const env: CoerceEnv = {
    lookups: ctx.lookups,
    ...(intent.kind === "create" && intent.whileUpdating === true ? { requiredNote: " to add a new record" } : {}),
    ...(updating ? { plainLookups: true } : {}),
  };
  const errors: string[] = [];
  const row: Record<string, ParsedValue> = {};
  const cleared = new Set<string>();

  /** The cell as a value is read from it: `[clear]` is not a value. */
  const valueCell = (key: string): CellInput | undefined => (isClearToken(cells[key]) ? null : cells[key]);
  const currencyFor = (money: ImportColumn): string => {
    const stored = intent.kind === "update" ? intent.storedCurrency : null;
    return stored !== null && stored.trim() !== "" ? stored.trim().toUpperCase() : fallbackCurrency(entity, money);
  };

  // Currency columns first: every money column needs its row's currency before it can convert.
  const currencies = new Map<string, Coerced>();
  for (const money of columns.filter((column) => column.kind === "money")) {
    const key = money.currencyKey ?? "currency";
    const holder = columns.find((column) => column.key === key);
    if (holder !== undefined && !currencies.has(key)) {
      currencies.set(key, resolveCurrency(holder, valueCell(key), currencyFor(money), env, updating));
    }
  }

  let clearRefused = false;
  for (const column of columns) {
    const cell = cells[column.key];
    if (isClearToken(cell)) {
      row[column.key] = null;
      if (updating) cleared.add(column.key);
      else clearRefused = true;
      continue;
    }
    if (updating && isBlank(cell)) {
      row[column.key] = null;
      continue;
    }
    let result: Coerced;
    const held = currencies.get(column.key);
    if (held !== undefined) {
      result = held;
    } else if (column.kind === "money") {
      const named = currencies.get(column.currencyKey ?? "currency");
      if (named !== undefined && !named.ok) {
        // The currency column already carries the error; a second one here would be noise.
        row[column.key] = null;
        continue;
      }
      const currency = named !== undefined && named.ok ? String(named.value) : currencyFor(column);
      result = coerceValue(column, cell, { ...env, currency });
    } else {
      result = coerceValue(column, cell, env);
    }
    if (result.ok) {
      row[column.key] = result.value;
    } else {
      row[column.key] = null;
      errors.push(result.error);
    }
  }
  if (clearRefused) errors.push(CLEAR_REFUSED_MESSAGE);

  if (errors.length === 0 && !updating && entity.validateRow !== undefined) {
    try {
      const problem = entity.validateRow(row, ctx);
      if (problem !== null) errors.push(problem);
    } catch (error) {
      errors.push(error instanceof RowProblem ? error.message : "This row could not be checked");
    }
  }
  return { row, errors, cleared };
}
