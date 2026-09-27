/**
 * The pure half of update mode (ADR-0022 addendum): which stored record a sheet row names, and
 * what, exactly, it would change. No I/O and no React: `useImportExport` loads the records, calls
 * these, shows the plan in the preview and runs it.
 *
 * The rules, because a bulk update can overwrite a customer's data:
 * - A blank cell leaves the field alone. Only `[clear]` empties one, and only where the field can
 *   be emptied (`UpdateField.clear`).
 * - Only fields that actually differ are sent, so an untouched row costs no request, no audit row
 *   and no event.
 * - An immutable field (SKU, code, employee number) is never sent; a cell that disagrees with the
 *   stored value refuses the row rather than being silently ignored.
 */

import { CLEAR_TOKEN, RowProblem } from "./types";
import type {
  ImportColumn,
  ImportContext,
  ParsedRow,
  ParsedValue,
  UpdateField,
  UpdatePlan,
  UpdateSpec,
} from "./types";

/** A key compared without case or runs of spaces: the same rule `makeLookup` uses. */
export function normaliseKey(text: string): string {
  return text.trim().replace(/\s+/g, " ").toLowerCase();
}

/** True for a cell whose text is `[clear]` (any case, surrounding spaces ignored). */
export function isClearToken(cell: unknown): boolean {
  return typeof cell === "string" && cell.trim().toLowerCase() === CLEAR_TOKEN;
}

export interface ExistingIndex<Item> {
  byId: ReadonlyMap<string, Item>;
  byKey: ReadonlyMap<string, readonly Item[]>;
}

export function indexExisting<Item>(spec: UpdateSpec<Item>, items: readonly Item[]): ExistingIndex<Item> {
  const byId = new Map<string, Item>();
  const byKey = new Map<string, Item[]>();
  for (const item of items) {
    byId.set(spec.idOf(item), item);
    const key = spec.keyOf?.(item) ?? null;
    if (key === null || key.trim() === "") continue;
    const normalised = normaliseKey(key);
    const bucket = byKey.get(normalised);
    if (bucket === undefined) byKey.set(normalised, [item]);
    else bucket.push(item);
  }
  return { byId, byKey };
}

export type Match<Item> =
  | { kind: "found"; item: Item; by: "id" | "key" }
  /** No such record: the row creates one. */
  | { kind: "new" }
  /** The row carries a Record ID that no record in this list has (deleted, or another organisation's). */
  | { kind: "unknown-id" }
  /** More than one stored record has this key (a list that does not enforce it), so the row cannot say which. */
  | { kind: "ambiguous" };

/** Finds the record a row names: its Record ID first, else the list's unique key. */
export function matchRow<Item>(
  index: ExistingIndex<Item>,
  recordId: string | null,
  key: string | null,
): Match<Item> {
  if (recordId !== null && recordId.trim() !== "") {
    const item = index.byId.get(recordId.trim());
    return item === undefined ? { kind: "unknown-id" } : { kind: "found", item, by: "id" };
  }
  if (key !== null && key.trim() !== "") {
    const candidates = index.byKey.get(normaliseKey(key)) ?? [];
    if (candidates.length === 0) return { kind: "new" };
    const [only] = candidates;
    if (candidates.length === 1 && only !== undefined) return { kind: "found", item: only, by: "key" };
    return { kind: "ambiguous" };
  }
  return { kind: "new" };
}

const isEmpty = (value: ParsedValue | undefined): boolean => value === null || value === undefined || value === "";

function shown(value: ParsedValue): string {
  return value === null || value === "" ? "empty" : `'${String(value)}'`;
}

export interface FieldDiff {
  /** Headers of the columns that change. */
  changed: string[];
  /** Only the fields that differ, keyed as the endpoint wants them. */
  body: Record<string, unknown>;
}

/**
 * What one row changes on one stored record. Throws `RowProblem` to refuse the row (an immutable
 * cell that disagrees, a `[clear]` on a field that cannot be emptied, a rule from `spec.check`).
 */
export function buildUpdate<Item>(
  columns: readonly ImportColumn[],
  spec: UpdateSpec<Item>,
  row: ParsedRow,
  cleared: ReadonlySet<string>,
  existing: Item,
  ctx: ImportContext,
): FieldDiff {
  const changed: string[] = [];
  const body: Record<string, unknown> = {};
  for (const field of spec.fields) {
    const header = columns.find((c) => c.key === field.column)?.header ?? field.column;
    const wantsClear = cleared.has(field.column);
    const cell = row[field.column];
    if (!wantsClear && isEmpty(cell)) continue; // a blank cell leaves the field alone
    const stored = field.current(existing, ctx);

    if (field.immutable === true) {
      if (wantsClear) throw new RowProblem(`${header} cannot be cleared: it cannot be changed once the record exists`);
      const value = resolved(field, cell as ParsedValue, ctx);
      if (stored === undefined) {
        // The list never returns it (opening stock), so we cannot say it is unchanged: refuse any value.
        throw new RowProblem(`${header} cannot be changed once the record exists. Leave the cell blank.`);
      }
      if (!sameValue(field, value, stored)) {
        throw new RowProblem(
          `${header} cannot be changed once the record exists (it is ${shown(stored)}). Leave the cell as it is, or blank.`,
        );
      }
      continue;
    }

    if (wantsClear) {
      if (field.clear === undefined) throw new RowProblem(`${header} cannot be cleared`);
      if (isEmpty(stored)) continue; // already empty
      body[field.field ?? field.column] = field.clear;
      changed.push(header);
      continue;
    }

    const value = resolved(field, cell as ParsedValue, ctx);
    // A value the list never returns cannot be compared, so it counts as different and is sent.
    if (sameValue(field, value, stored ?? null)) continue;
    body[field.field ?? field.column] = field.toWire === undefined ? value : field.toWire(value);
    changed.push(header);
  }
  spec.check?.(row, existing, changed, ctx);
  return { changed, body };
}

function resolved<Item>(field: UpdateField<Item>, cell: ParsedValue, ctx: ImportContext): ParsedValue {
  return field.resolve === undefined ? cell : field.resolve(cell, ctx);
}

function sameValue<Item>(field: UpdateField<Item>, cell: ParsedValue, stored: ParsedValue): boolean {
  if (field.same !== undefined) return field.same(cell, stored);
  return cell === stored;
}

/**
 * The plan for one matched row, or null when it changes nothing (no request is made for it).
 * `run` sends the changed fields once, then each extra step in order.
 */
export function planUpdate<Item>(
  columns: readonly ImportColumn[],
  spec: UpdateSpec<Item>,
  row: ParsedRow,
  cleared: ReadonlySet<string>,
  existing: Item,
  ctx: ImportContext,
): UpdatePlan | null {
  const diff = buildUpdate(columns, spec, row, cleared, existing, ctx);
  const extras = spec.extras?.(row, cleared, existing, ctx) ?? [];
  const changed = [...diff.changed, ...extras.flatMap((step) => step.changed)];
  if (changed.length === 0) return null;
  return {
    changed,
    run: async () => {
      if (Object.keys(diff.body).length > 0) await spec.send(existing, diff.body, ctx);
      for (const step of extras) await step.run();
    },
  };
}
