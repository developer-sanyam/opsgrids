/**
 * From a parsed sheet to what the dialog shows and what the runner sends: every row is coerced,
 * unchanged sample rows are set aside, and a row that cannot be turned into a payload (a company
 * name that matches nothing) is reported now, in the preview, rather than halfway through a run.
 *
 * In update mode (ADR-0022 addendum) each row is first matched to a stored record, by its Record ID
 * or by the list's unique key, and is then one of four things: a new record, an update (with the
 * columns it changes), a row that already matches its record (no request), or a row refused.
 */

import { withoutUntouched } from "./baseline";
import { displayValue, importableColumns, isBlank, recordIdHeader } from "./cells";
import type { AnyEntity } from "./cells";
import { coerceRow } from "./coerce";
import type { RowIntent } from "./coerce";
import { UNCHANGED_MESSAGE, prepareMessage } from "./errors";
import { sampleMatcher } from "./sample";
import type { RunnerRow } from "./runner";
import type {
  BaselineUse,
  ImportContext,
  ImportMode,
  ImportPreview,
  LookupSet,
  ParsedRow,
  PreviewAction,
  PreviewRow,
  RowOutcome,
  UpdatePlan,
  UpdateSpec,
} from "./types";
import { indexExisting, matchRow, planUpdate } from "./update";
import type { ExistingIndex } from "./update";
import type { ParsedSheet } from "./workbook";

/** How many rows the preview lists (invalid ones first); the counts cover them all. */
export const PREVIEW_ROW_LIMIT = 200;

/** Said of a row that carries its record's id when nothing is being updated. `header` is the list's own
 *  name for that column ("Company ID"): the words the sheet's header row uses. */
export function recordIdMessage(header: string): string {
  return `This row has a ${header}, so it came from an export and is already in the system. Clear the ${header} cell to add it as a new record.`;
}

/** Added to `recordIdMessage` for a list that can be updated, where the way to use the id exists. */
export const RECORD_ID_UPDATE_HINT = "Switch to Add new and update existing to update it.";

/** A record id no stored record has. A caller who may see only their own records cannot tell "deleted"
 *  from "not yours", and neither can we. */
export function unknownRecordIdMessage(header: string): string {
  return `That ${header} is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see.`;
}

/**
 * A key that no record has as typed, though records have it in other capitals or spacing. The
 * server counts those as different keys, so the row could mean either: it is refused, with what is
 * stored named. Where the list has a record id column, that names one record unambiguously.
 */
export function keyVariantMessage(typed: string, stored: readonly string[], recordIdColumn: string | null): string {
  const quoted = stored.map((spelling) => `'${spelling}'`);
  const [first] = quoted;
  const alone = quoted.length === 1 && first !== undefined;
  const which = alone ? `A record ${first} exists that differs` : `Records ${quoted.join(", ")} exist that differ`;
  const way = recordIdColumn === null ? "." : alone ? `, or its ${recordIdColumn}.` : `, or the ${recordIdColumn}.`;
  return `${which} from '${typed}' only in capitals or spacing. Use ${alone ? "its" : "an"} exact spelling${way}`;
}

/** One sentence for a row with several problems: a lone one is kept as it is, several are separated with `;`. */
function joinProblems(problems: readonly string[]): string {
  return problems.length === 1 ? problems.join("") : problems.map((problem) => problem.replace(/\.$/, "")).join("; ");
}

export interface PreparedImport {
  /** Rows that passed every check, ready to send: new records and updates, in file order. */
  valid: RunnerRow[];
  /** Every row that will not be sent, as the report will list it. */
  invalid: RowOutcome[];
  /** Update rows that already match their record: nothing is sent, and the report says so. */
  unchanged: RowOutcome[];
  lookups: LookupSet;
  /**
   * What the preview promised, row by row: which action, and which columns it changes. Compared with
   * the same file planned against records loaded later, it says whether what the user reviewed is
   * still what would be sent.
   */
  review: string;
}

/** What update mode needs to match a row: the spec, the index over the loaded records, the key's header. */
interface Updating {
  spec: UpdateSpec<unknown>;
  index: ExistingIndex<unknown>;
  keyColumn: string | undefined;
  keyHeader: string;
}

function headerOf(entity: AnyEntity, key: string): string {
  return entity.columns.find((column) => column.key === key)?.header ?? key;
}

function withStoredKey(row: ParsedRow, updating: Updating, item: unknown): ParsedRow {
  const { keyColumn } = updating;
  const key = updating.spec.keyOf?.(item) ?? null;
  return keyColumn === undefined || key === null ? row : { ...row, [keyColumn]: key.trim() };
}

/** Who a stored record is, in words a person recognises: the list's own label, else its key, else its id. */
function recordLabelOf(spec: UpdateSpec<unknown>, item: unknown): string {
  const named = spec.recordLabel?.(item).trim() ?? "";
  if (named !== "") return named;
  const key = spec.keyOf?.(item)?.trim() ?? "";
  return key !== "" ? key : spec.idOf(item);
}

/**
 * Whether a row found by its key wrote that key in other capitals than the record has it. The match
 * ignores capitals only where the server does, so this is the one thing a row found by its key
 * cannot change: the key cell is taken as stored (so it is not read as a rename).
 */
function writesKeyInOtherCapitals(updating: Updating, typed: ParsedRow, item: unknown): boolean {
  const cell = updating.keyColumn === undefined ? undefined : typed[updating.keyColumn];
  const stored = updating.spec.keyOf?.(item)?.trim();
  return typeof cell === "string" && stored !== undefined && cell !== stored;
}

/** Said of a row whose only difference from its record is the capitals of its key. */
function capitalsOnlyMessage(keyHeader: string, idHeader: string): string {
  return `The ${keyHeader} differs from the record's only in capitals, and a row found by it cannot change that, so nothing was sent. Use the ${idHeader} to change only the capitals of a name.`;
}

/**
 * What to tell the user about how rows find their record, when it would surprise them: the file has
 * neither column that names a record (so every row would be added), or the list can only be matched
 * by Record ID and some rows have none (so those are added as new records).
 */
function matchWarningFor(entity: AnyEntity, updating: Updating, sheet: ParsedSheet, unmatched: number): string | null {
  const { recordIdKey } = entity;
  const hasRecordId = recordIdKey !== undefined && sheet.rows.some(({ cells }) => Object.hasOwn(cells, recordIdKey));
  const hasKey = updating.keyColumn !== undefined && sheet.columns.some((column) => column.key === updating.keyColumn);
  if (!hasRecordId && !hasKey) {
    const names = [
      recordIdKey === undefined ? null : recordIdHeader(entity),
      updating.keyColumn === undefined ? null : updating.keyHeader,
    ].filter((name) => name !== null);
    if (names.length === 0) return null;
    return `This file has no ${names.join(" or ")} column, so it cannot update existing records; every row would be added as new.`;
  }
  // A list with a key finds a record by it; only one without a key relies on the id alone.
  if (recordIdKey === undefined || updating.keyColumn !== undefined || unmatched === 0) return null;
  const idHeader = recordIdHeader(entity);
  return unmatched === 1
    ? `1 row has no ${idHeader}, so it will be created as a new record.`
    : `${unmatched} rows have no ${idHeader}, so they will be created as new records.`;
}

function updatingFor(entity: AnyEntity, mode: ImportMode, existing: readonly unknown[] | null): Updating | null {
  const spec = entity.update;
  if (mode !== "addAndUpdate" || spec === undefined || existing === null) return null;
  const keyColumn = spec.keyColumn;
  return {
    spec,
    index: indexExisting(spec, existing),
    keyColumn,
    keyHeader: entity.columns.find((column) => column.key === keyColumn)?.header ?? keyColumn ?? "",
  };
}

/**
 * Checks every row of a read sheet. `existing` is the loaded list of stored records; it is what an
 * update-mode preview matches against, and is ignored (as is `mode`) for an entity with no `update`.
 * In `add` mode nothing is matched: a row carrying a Record ID is refused, as it always was.
 */
export function prepareImport(
  entity: AnyEntity,
  sheet: ParsedSheet,
  ctx: ImportContext,
  fileName: string,
  mode: ImportMode = "add",
  existing: readonly unknown[] | null = null,
): { preview: ImportPreview; prepared: PreparedImport } {
  const columns = importableColumns(entity);
  const identifying = columns.find((column) => column.required === true) ?? columns[0];

  const isSample = sampleMatcher(entity, ctx.lookups);
  const { recordIdKey } = entity;
  const updating = updatingFor(entity, mode, existing);
  const idHeader = recordIdHeader(entity);
  const recordIdRefusal =
    entity.update === undefined ? recordIdMessage(idHeader) : `${recordIdMessage(idHeader)} ${RECORD_ID_UPDATE_HINT}`;

  const valid: RunnerRow[] = [];
  const invalid: RowOutcome[] = [];
  const unchanged: RowOutcome[] = [];
  const shown: PreviewRow[] = [];
  const tally = { create: 0, update: 0, sample: 0, matched: 0 };
  /** The first row that updates each record: a second row for the same record is refused. */
  const claimed = new Map<string, number>();
  const reviewed: string[] = [];
  let baselineUsed = false;

  for (const { row, cells: typed } of sheet.rows) {
    const values: Record<string, string> = {};
    for (const column of sheet.columns) values[column.key] = displayValue(typed[column.key]);
    let label = identifying === undefined ? "" : (values[identifying.key] ?? "");
    // What the row's cells count as: what was typed, less the cells the export wrote and nobody changed.
    let cells = typed;
    // Who the record this row names is, once it is known to name one.
    let record: string | null = null;

    const show = (
      action: PreviewAction,
      error: string | null,
      details: { changes?: readonly string[]; note?: string } = {},
    ): void => {
      const changes = details.changes ?? [];
      reviewed.push(`${row}:${action}:${changes.join("|")}`);
      shown.push({ row, values, error, sample: action === "sample", action, changes, record, note: details.note ?? null });
    };
    const refuse = (message: string): void => {
      invalid.push({ row, status: "invalid", message, label, record: record ?? undefined });
      show("invalid", message);
    };

    // The record this row updates, once it is known to name one.
    let stored: { item: unknown; id: string; by: "id" | "key" } | null = null;

    // A row that carries an export's record id came out of the system. Said first and alone: the
    // column errors of a row that must not be sent would only bury the reason.
    const hasRecordId = recordIdKey !== undefined && !isBlank(cells[recordIdKey]);
    if (hasRecordId && recordIdKey !== undefined) {
      if (updating === null) {
        refuse(recordIdRefusal);
        continue;
      }
      const recordId = displayValue(cells[recordIdKey]);
      const match = matchRow(updating.index, recordId, null);
      if (match.kind !== "found") {
        refuse(unknownRecordIdMessage(idHeader));
        continue;
      }
      stored = { item: match.item, id: updating.spec.idOf(match.item), by: "id" };
      record = recordLabelOf(updating.spec, match.item);
      tally.matched += 1;
      // A cell the export wrote and nobody has changed is not this row's to send: what the record
      // holds now may be a colleague's later edit.
      const untouched = sheet.baseline.kind === "found" ? sheet.baseline.untouched.get(row) : undefined;
      if (untouched !== undefined) {
        baselineUsed = true;
        cells = withoutUntouched(typed, untouched);
      }
    }

    // Before any validation: an unchanged example row is never reported wrong, whatever the
    // organisation's lists hold. A row with a Record ID is never an example.
    if (!hasRecordId && isSample(cells)) {
      tally.sample += 1;
      show("sample", null);
      continue;
    }

    if (updating !== null && !hasRecordId) {
      const key = updating.keyColumn === undefined ? null : displayValue(cells[updating.keyColumn]);
      const match = matchRow(updating.index, null, key);
      if (match.kind === "ambiguous") {
        refuse(`More than one record has that ${updating.keyHeader}, so the row cannot say which to update.`);
        continue;
      }
      if (match.kind === "variant") {
        refuse(keyVariantMessage(key ?? "", match.spellings, recordIdKey === undefined ? null : idHeader));
        continue;
      }
      if (match.kind === "found") {
        stored = { item: match.item, id: updating.spec.idOf(match.item), by: "key" };
        record = recordLabelOf(updating.spec, match.item);
        tally.matched += 1;
      }
    }

    if (updating !== null && stored !== null) {
      const first = claimed.get(stored.id);
      if (first !== undefined) {
        refuse(`This row updates the same record as row ${first}. Keep only one of the two rows.`);
        continue;
      }
      if (label === "") label = updating.spec.keyOf?.(stored.item) ?? "";
      // A cell (or a [clear]) where the update does nothing would look as if it had changed the
      // field. Said before the cell is even read, so a bad value there is not what the row is refused for.
      const locked = (updating.spec.notUpdatable ?? []).filter((key) => !isBlank(cells[key]));
      if (locked.length > 0) {
        refuse(
          joinProblems(
            locked.map((key) => `${headerOf(entity, key)} cannot be changed by an update: leave the cell blank.`),
          ),
        );
        continue;
      }
    }

    const intent: RowIntent =
      updating !== null && stored !== null
        ? { kind: "update", storedCurrency: updating.spec.currencyOf?.(stored.item) ?? null }
        : { kind: "create", whileUpdating: updating !== null };
    const coerced = coerceRow(entity, cells, ctx, intent);
    const { errors, cleared } = coerced;
    // A row found by its key names the record BY that cell, so the cell is the record's key however
    // it was capitalised or spaced: it is not a change (a rename, or a "different" immutable code).
    const parsed =
      updating !== null && stored?.by === "key" ? withStoredKey(coerced.row, updating, stored.item) : coerced.row;
    let plan: UpdatePlan | null = null;
    if (errors.length === 0) {
      try {
        if (updating !== null && stored !== null) {
          plan = planUpdate(entity.columns, updating.spec, parsed, cleared, stored.item, ctx);
        } else {
          entity.toPayload(parsed, ctx);
        }
      } catch (error) {
        errors.push(prepareMessage(error));
      }
    }
    if (errors.length > 0) {
      refuse(joinProblems(errors));
      continue;
    }

    if (updating === null || stored === null) {
      tally.create += 1;
      valid.push({ row, values: parsed, label });
      show("create", null);
      continue;
    }
    claimed.set(stored.id, row);
    if (plan === null) {
      // A row found by a key written in other capitals changes nothing, since it cannot change the key.
      const capitals = stored.by === "key" && writesKeyInOtherCapitals(updating, coerced.row, stored.item);
      const note = capitals ? capitalsOnlyMessage(updating.keyHeader, idHeader) : null;
      unchanged.push({ row, status: "unchanged", message: note ?? UNCHANGED_MESSAGE, label, record: record ?? undefined });
      show("unchanged", null, note === null ? {} : { note });
      continue;
    }
    const { spec } = updating;
    const { item } = stored;
    tally.update += 1;
    valid.push({
      row,
      values: parsed,
      label,
      record: record ?? undefined,
      update: { plan: (runCtx) => planUpdate(entity.columns, spec, parsed, cleared, item, runCtx) },
    });
    show("update", null, { changes: plan.changed });
  }

  const rows = [...shown.filter((entry) => entry.error !== null), ...shown.filter((entry) => entry.error === null)];
  return {
    preview: {
      fileName,
      mode: updating === null ? "add" : "addAndUpdate",
      total: sheet.rows.length,
      valid: valid.length,
      invalid: invalid.length,
      sample: tally.sample,
      create: tally.create,
      update: tally.update,
      unchanged: unchanged.length,
      columns: sheet.columns.map(({ key, header }) => ({ key, header })),
      rows: rows.slice(0, PREVIEW_ROW_LIMIT),
      unknownHeaders: sheet.unknownHeaders,
      // Where rows may update records, a column a row leaves blank leaves the field alone, so a
      // sheet of only a Record ID and the columns to change is a good one. A row that would create
      // a record still needs its required cells, and says so.
      missingRequired: updating === null ? sheet.missingRequired : [],
      matchWarning: updating === null ? null : matchWarningFor(entity, updating, sheet, tally.create),
      baseline: updating === null ? null : baselineUse(sheet, tally.matched, baselineUsed),
    },
    prepared: { valid, invalid, unchanged, lookups: ctx.lookups, review: reviewed.join("\n") },
  };
}

/** What became of the export baseline, said only where some row names a stored record and could have been stale. */
function baselineUse(sheet: ParsedSheet, matched: number, used: boolean): BaselineUse | null {
  if (matched === 0) return null;
  if (used) return "used";
  return sheet.baseline.kind === "unusable" ? "unusable" : "none";
}
