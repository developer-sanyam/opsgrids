/**
 * From a parsed sheet to what the dialog shows and what the runner sends: every row is coerced,
 * unchanged sample rows are set aside, and a row that cannot be turned into a payload (a company
 * name that matches nothing) is reported now, in the preview, rather than halfway through a run.
 */

import { displayValue, importableColumns, isBlank } from "./cells";
import type { AnyEntity } from "./cells";
import { coerceRow } from "./coerce";
import { prepareMessage } from "./errors";
import { sampleMatcher } from "./sample";
import type { RunnerRow } from "./runner";
import type { ImportContext, ImportPreview, LookupSet, PreviewRow, RowOutcome } from "./types";
import type { ParsedSheet } from "./workbook";

/** How many rows the preview lists (invalid ones first); the counts cover them all. */
export const PREVIEW_ROW_LIMIT = 200;

export const RECORD_ID_MESSAGE =
  "This row has a Record ID, so it came from an export and is already in the system. Clear the Record ID cell to add it as a new record.";

/** One sentence for a row with several problems: a lone one is kept as it is, several are separated with `;`. */
function joinProblems(problems: readonly string[]): string {
  return problems.length === 1 ? problems.join("") : problems.map((problem) => problem.replace(/\.$/, "")).join("; ");
}

export interface PreparedImport {
  /** Rows that passed every check, ready to send. */
  valid: RunnerRow[];
  /** Every row that will not be sent, as the report will list it. */
  invalid: RowOutcome[];
  lookups: LookupSet;
}

export function prepareImport(
  entity: AnyEntity,
  sheet: ParsedSheet,
  ctx: ImportContext,
  fileName: string,
): { preview: ImportPreview; prepared: PreparedImport } {
  const columns = importableColumns(entity);
  const identifying = columns.find((column) => column.required === true) ?? columns[0];

  const isSample = sampleMatcher(entity, ctx.lookups);
  const { recordIdKey } = entity;
  const valid: RunnerRow[] = [];
  const invalid: RowOutcome[] = [];
  const shown: PreviewRow[] = [];
  let samples = 0;

  for (const { row, cells } of sheet.rows) {
    const values: Record<string, string> = {};
    for (const column of sheet.columns) values[column.key] = displayValue(cells[column.key]);
    const label = identifying === undefined ? "" : (values[identifying.key] ?? "");

    // A row that carries an export's record id is already in the system. Said first and alone:
    // the column errors of a row that must not be sent would only bury the reason.
    if (recordIdKey !== undefined && !isBlank(cells[recordIdKey])) {
      invalid.push({ row, status: "invalid", message: RECORD_ID_MESSAGE, label });
      shown.push({ row, values, error: RECORD_ID_MESSAGE, sample: false });
      continue;
    }

    // Before any validation: an unchanged example row is never reported wrong, whatever the
    // organisation's lists hold.
    if (isSample(cells)) {
      samples += 1;
      shown.push({ row, values, error: null, sample: true });
      continue;
    }

    const { row: parsed, errors } = coerceRow(entity, cells, ctx);
    if (errors.length === 0) {
      try {
        entity.toPayload(parsed, ctx);
      } catch (error) {
        errors.push(prepareMessage(error));
      }
    }

    if (errors.length > 0) {
      const message = joinProblems(errors);
      invalid.push({ row, status: "invalid", message, label });
      shown.push({ row, values, error: message, sample: false });
    } else {
      valid.push({ row, values: parsed, label });
      shown.push({ row, values, error: null, sample: false });
    }
  }

  const rows = [...shown.filter((entry) => entry.error !== null), ...shown.filter((entry) => entry.error === null)];
  return {
    preview: {
      fileName,
      total: sheet.rows.length,
      valid: valid.length,
      invalid: invalid.length,
      sample: samples,
      columns: sheet.columns.map(({ key, header }) => ({ key, header })),
      rows: rows.slice(0, PREVIEW_ROW_LIMIT),
      unknownHeaders: sheet.unknownHeaders,
      missingRequired: sheet.missingRequired,
    },
    prepared: { valid, invalid, lookups: ctx.lookups },
  };
}
