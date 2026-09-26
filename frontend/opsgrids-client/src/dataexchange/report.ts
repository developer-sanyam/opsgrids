/**
 * The per-row result of an import as a workbook the user can keep: which rows went in, which were
 * skipped because the record already exists, and which were refused and why.
 */

import { importableColumns } from "./cells";
import type { AnyEntity } from "./cells";
import { clipCell, loadExcel, styleHeaderRow, widthFor, workbookToBlob } from "./excel";
import type { ImportSummary, RowStatus } from "./types";

const STATUS_LABEL: Record<RowStatus, string> = {
  created: "Created",
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

/** The result workbook: one sheet, "Result", with Row, the first required cell, Status, Message. */
export async function buildReportWorkbook(entity: AnyEntity, summary: ImportSummary): Promise<Blob> {
  const columns = importableColumns(entity);
  const identifying = columns.find((column) => column.required === true) ?? columns[0];
  const labelHeader = identifying?.header ?? "Record";

  const excel = await loadExcel();
  const workbook = new excel.Workbook();
  workbook.creator = "OpsGrids";
  const sheet = workbook.addWorksheet("Result", { views: [{ state: "frozen", ySplit: 1 }] });
  styleHeaderRow(sheet.addRow(["Row", labelHeader, "Status", "Message"]));
  sheet.getColumn(1).style = { numFmt: "0" };
  sheet.getColumn(2).style = { numFmt: "@" };
  sheet.getColumn(3).style = { numFmt: "@" };
  sheet.getColumn(4).style = { numFmt: "@" };

  const ordered = [...summary.outcomes].sort((a, b) => a.row - b.row);
  for (const outcome of ordered) {
    sheet.addRow([
      outcome.row,
      textOrNull(outcome.label),
      STATUS_LABEL[outcome.status],
      textOrNull(outcome.message),
    ]);
  }

  const longest = (texts: readonly string[]): number =>
    texts.reduce((most, text) => Math.max(most, text.length), 0);
  sheet.getColumn(1).width = widthFor(4);
  sheet.getColumn(2).width = widthFor(Math.max(labelHeader.length, longest(ordered.map((o) => o.label))));
  sheet.getColumn(3).width = widthFor(8);
  sheet.getColumn(4).width = widthFor(longest(ordered.map((o) => o.message ?? "")));
  return workbookToBlob(workbook);
}
