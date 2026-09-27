/**
 * The per-row result of an import as a workbook the user can keep: which rows went in (created or,
 * when updating, updated and which columns changed), which matched a record that already had those
 * values, which were skipped because the record already exists, and which were refused and why.
 */

import { importableColumns } from "./cells";
import type { AnyEntity } from "./cells";
import { clipCell, loadExcel, styleHeaderRow, widthFor, workbookToBlob } from "./excel";
import type { ImportSummary, RowOutcome, RowStatus } from "./types";

const STATUS_LABEL: Record<RowStatus, string> = {
  created: "Created",
  updated: "Updated",
  unchanged: "Unchanged",
  skipped: "Skipped",
  failed: "Failed",
  invalid: "Invalid",
};

function textOrNull(text: string | null): string | null {
  return text === null || text === "" ? null : clipCell(text);
}

export function reportFileName(entity: Pick<AnyEntity, "fileStem">): string {
  return `${entity.fileStem}-import-result.xlsx`;
}

/**
 * The result workbook: one sheet, "Result", with Row, the first required cell, Status and Message,
 * and, when any row updated (or tried to update) a record, Changes (the headers of the columns it
 * changes) and Existing record (who the stored record is, in the list's own words).
 */
export async function buildReportWorkbook(entity: AnyEntity, summary: ImportSummary): Promise<Blob> {
  const columns = importableColumns(entity);
  const identifying = columns.find((column) => column.required === true) ?? columns[0];
  const labelHeader = identifying?.header ?? "Record";

  const excel = await loadExcel();
  const workbook = new excel.Workbook();
  workbook.creator = "OpsGrids";
  const sheet = workbook.addWorksheet("Result", { views: [{ state: "frozen", ySplit: 1 }] });
  const ordered = [...summary.outcomes].sort((a, b) => a.row - b.row);
  const changesOf = (outcome: RowOutcome): string | null => textOrNull((outcome.changes ?? []).join(", "));
  const recordOf = (outcome: RowOutcome): string | null => textOrNull(outcome.record ?? "");
  const withChanges = ordered.some((outcome) => changesOf(outcome) !== null);
  const withRecord = ordered.some((outcome) => recordOf(outcome) !== null);

  const optional = [
    { header: "Changes", show: withChanges, of: changesOf },
    { header: "Existing record", show: withRecord, of: recordOf },
  ].filter((column) => column.show);

  styleHeaderRow(sheet.addRow(["Row", labelHeader, "Status", "Message", ...optional.map((column) => column.header)]));
  sheet.getColumn(1).style = { numFmt: "0" };
  for (let position = 2; position <= 4 + optional.length; position += 1) sheet.getColumn(position).style = { numFmt: "@" };

  for (const outcome of ordered) {
    sheet.addRow([
      outcome.row,
      textOrNull(outcome.label),
      STATUS_LABEL[outcome.status],
      textOrNull(outcome.message),
      ...optional.map((column) => column.of(outcome)),
    ]);
  }

  const longest = (texts: readonly string[]): number =>
    texts.reduce((most, text) => Math.max(most, text.length), 0);
  sheet.getColumn(1).width = widthFor(4);
  sheet.getColumn(2).width = widthFor(Math.max(labelHeader.length, longest(ordered.map((o) => o.label))));
  sheet.getColumn(3).width = widthFor(8);
  sheet.getColumn(4).width = widthFor(longest(ordered.map((o) => o.message ?? "")));
  optional.forEach((column, index) => {
    sheet.getColumn(5 + index).width = widthFor(
      Math.max(column.header.length, longest(ordered.map((outcome) => column.of(outcome) ?? ""))),
    );
  });
  return workbookToBlob(workbook);
}
