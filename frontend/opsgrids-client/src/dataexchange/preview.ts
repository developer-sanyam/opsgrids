/**
 * From a parsed sheet to what the dialog shows and what the runner sends: every row is coerced,
 * unchanged sample rows are set aside, and a row that cannot be turned into a payload (a company
 * name that matches nothing) is reported now, in the preview, rather than halfway through a run.
 *
 * In update mode (ADR-0022 addendum) each row is first matched to a stored record, by its Record ID
 * or by the list's unique key, and is then one of four things: a new record, an update (with the
 * columns it changes), a row that already matches its record (no request), or a row refused.
 */

import { displayValue, importableColumns, isBlank } from "./cells";
import type { AnyEntity } from "./cells";
import { coerceRow } from "./coerce";
import type { RowIntent } from "./coerce";
import { UNCHANGED_MESSAGE, prepareMessage } from "./errors";
import { sampleMatcher } from "./sample";
import type { RunnerRow } from "./runner";
import type {
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

export const RECORD_ID_MESSAGE =
  "This row has a Record ID, so it came from an export and is already in the system. Clear the Record ID cell to add it as a new record.";

/** Added to `RECORD_ID_MESSAGE` for a list that can be updated, where the way to use the Record ID exists. */
export const RECORD_ID_UPDATE_HINT = "Switch to Add new and update existing to update it.";

export const UNKNOWN_RECORD_ID_MESSAGE =
  "That Record ID is not in this list: the record may have been deleted, or the file came from another organisation.";

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
  return keyColumn === undefined || key === null ? row : { ...row, [keyColumn]: key };
}

/** Why update mode can match nothing in this file, when the file has neither column that names a record. */
function matchWarningFor(entity: AnyEntity, updating: Updating, sheet: ParsedSheet): string | null {
  const { recordIdKey } = entity;
  const hasRecordId = recordIdKey !== undefined && sheet.rows.some(({ cells }) => Object.hasOwn(cells, recordIdKey));
  const hasKey = updating.keyColumn !== undefined && sheet.columns.some((column) => column.key === updating.keyColumn);
  if (hasRecordId || hasKey) return null;
  const names = [
    recordIdKey === undefined ? null : (entity.columns.find((column) => column.key === recordIdKey)?.header ?? "Record ID"),
    updating.keyColumn === undefined ? null : updating.keyHeader,
  ].filter((name) => name !== null);
  if (names.length === 0) return null;
  return `This file has no ${names.join(" or ")} column, so it cannot update existing records; every row would be added as new.`;
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
  const recordIdRefusal =
    entity.update === undefined ? RECORD_ID_MESSAGE : `${RECORD_ID_MESSAGE} ${RECORD_ID_UPDATE_HINT}`;

  const valid: RunnerRow[] = [];
  const invalid: RowOutcome[] = [];
  const unchanged: RowOutcome[] = [];
  const shown: PreviewRow[] = [];
  const tally = { create: 0, update: 0, sample: 0 };
  /** The first row that updates each record: a second row for the same record is refused. */
  const claimed = new Map<string, number>();

  for (const { row, cells } of sheet.rows) {
    const values: Record<string, string> = {};
    for (const column of sheet.columns) values[column.key] = displayValue(cells[column.key]);
    let label = identifying === undefined ? "" : (values[identifying.key] ?? "");

    const show = (action: PreviewAction, error: string | null, changes: readonly string[] = []): void => {
      shown.push({ row, values, error, sample: action === "sample", action, changes });
    };
    const refuse = (message: string): void => {
      invalid.push({ row, status: "invalid", message, label });
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
      const match = matchRow(updating.index, displayValue(cells[recordIdKey]), null);
      if (match.kind !== "found") {
        refuse(UNKNOWN_RECORD_ID_MESSAGE);
        continue;
      }
      stored = { item: match.item, id: updating.spec.idOf(match.item), by: "id" };
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
      if (match.kind === "found") stored = { item: match.item, id: updating.spec.idOf(match.item), by: "key" };
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
      unchanged.push({ row, status: "unchanged", message: UNCHANGED_MESSAGE, label });
      show("unchanged", null);
      continue;
    }
    const { spec } = updating;
    const { item } = stored;
    tally.update += 1;
    valid.push({
      row,
      values: parsed,
      label,
      update: { plan: (runCtx) => planUpdate(entity.columns, spec, parsed, cleared, item, runCtx) },
    });
    show("update", null, plan.changed);
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
      matchWarning: updating === null ? null : matchWarningFor(entity, updating, sheet),
    },
    prepared: { valid, invalid, unchanged, lookups: ctx.lookups },
  };
}
