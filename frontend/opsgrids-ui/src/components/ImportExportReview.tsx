import { useRef } from "react";
import type { ReactNode } from "react";

import { FileTextIcon } from "../icons";
import { formatCount, importButtonLabel } from "../lib/importExportCopy";
import type { ImportExportPreview, ImportExportPreviewRow } from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportStats } from "./ImportExportStats";
import type { ImportExportStat } from "./ImportExportStats";
import { ImportExportCellText, ImportExportTable } from "./ImportExportTable";
import type { ImportExportTableColumn } from "./ImportExportTable";
import { InlineAlert } from "./InlineAlert";
import { EmptyState } from "./States";
import { StatusBadge } from "./StatusBadge";

/** What a row of the preview will do, in a word and, for a row that changes or cannot be sent, the details. */
function RowAction({ row }: { row: ImportExportPreviewRow }) {
  switch (row.action) {
    case "invalid":
      return (
        <div className="flex min-w-48 flex-col items-start gap-1">
          <StatusBadge tone="danger" label="Needs fixing" />
          <span className="text-ink-soft">{row.error}</span>
        </div>
      );
    case "sample":
      return (
        <div className="flex min-w-48 flex-col items-start gap-1">
          <StatusBadge tone="neutral" label="Sample" />
          <span className="text-ink-soft">An unchanged example from the sample sheet. It is skipped.</span>
        </div>
      );
    case "update":
      return (
        <div className="flex min-w-48 flex-col items-start gap-1">
          <StatusBadge tone="info" label="Update" />
          <span className="text-ink-soft">{`Changes: ${row.changes.join(", ")}`}</span>
        </div>
      );
    case "unchanged":
      return <StatusBadge tone="neutral" label="No change" />;
    case "create":
      return <StatusBadge tone="success" label="New" />;
  }
}

function columnsFor(preview: ImportExportPreview): ImportExportTableColumn<ImportExportPreviewRow>[] {
  return [
    { key: "row", header: "Row", rowHeader: true, className: "tnum text-muted", render: (row) => row.row },
    { key: "action", header: "Action", render: (row) => <RowAction row={row} /> },
    ...preview.columns.map((column) => ({
      key: `value-${column.key}`,
      header: column.header,
      render: (row: ImportExportPreviewRow) => <ImportExportCellText value={row.values[column.key]} />,
    })),
  ];
}

/** The counts the preview leads with: what will be sent, and in update mode, of what kind. */
function statsFor(preview: ImportExportPreview): { stats: ImportExportStat[]; columns: string } {
  if (preview.mode === "add") {
    return {
      columns: "grid-cols-2 sm:grid-cols-4",
      stats: [
        { label: "Rows in file", value: preview.total },
        { label: "Ready to import", value: preview.valid },
        { label: "Need fixing", value: preview.invalid },
        { label: "Sample rows skipped", value: preview.sample },
      ],
    };
  }
  return {
    columns: "grid-cols-2 sm:grid-cols-3",
    stats: [
      { label: "Rows in file", value: preview.total },
      { label: "New records", value: preview.create },
      { label: "Updates", value: preview.update },
      { label: "No change", value: preview.unchanged },
      { label: "Need fixing", value: preview.invalid },
      { label: "Sample rows skipped", value: preview.sample },
    ],
  };
}

/** Why the import cannot start, when there is a reason the counts alone do not give. */
function Blocker({ preview }: { preview: ImportExportPreview }) {
  if (preview.missingRequired.length > 0 || preview.valid > 0 || preview.total === 0) return null;
  if (preview.mode === "addAndUpdate" && preview.unchanged > 0 && preview.invalid === 0) {
    return (
      <InlineAlert tone="info" live="off" title="Nothing to change.">
        Every row already matches its record, so there is nothing to send. Change a cell, save the file, then choose it
        again.
      </InlineAlert>
    );
  }
  if (preview.invalid === 0) {
    return (
      <InlineAlert tone="warning" live="off" title="Only the sample rows are in this file.">
        Replace the examples with your own rows, then choose the file again.
      </InlineAlert>
    );
  }
  return (
    <InlineAlert tone="warning" live="off" title="No rows can be imported yet.">
      Fix the rows marked Needs fixing, save the file, then choose it again.
    </InlineAlert>
  );
}

/** Rows that cannot be sent first, each group in the order the file had them. */
function problemsFirst(rows: readonly ImportExportPreviewRow[]): ImportExportPreviewRow[] {
  return [...rows.filter((row) => row.error !== null), ...rows.filter((row) => row.error === null)];
}

function tableNote(preview: ImportExportPreview): string | null {
  if (preview.rows.length < preview.total) {
    return `Showing ${formatCount(preview.rows.length)} of ${formatCount(preview.total)} rows. Rows that need fixing come first.`;
  }
  return preview.invalid > 0 ? "Rows that need fixing come first." : null;
}

interface ReviewProps {
  preview: ImportExportPreview;
  /** The choice between adding only and updating too, or null where the list has no update. Changing it
   *  checks this same file again, so it stays in reach while the rows are being read. */
  modeChoice: ReactNode;
}

/** The preview phase: what was found in the file, what is wrong with it, and every row it will send. */
export function ImportExportReview({ preview, modeChoice }: ReviewProps) {
  const note = tableNote(preview);
  const { stats, columns } = statsFor(preview);
  return (
    <div className="flex flex-col gap-4">
      <p className="flex min-w-0 items-center gap-2">
        <FileTextIcon aria-hidden className="size-4 shrink-0 text-muted" />
        <span className="truncate font-semibold text-ink">{preview.fileName}</span>
      </p>

      {modeChoice}

      <ImportExportStats columns={columns} stats={stats} />

      {preview.missingRequired.length > 0 ? (
        <InlineAlert tone="danger" live="off" title={`Your file is missing required columns: ${preview.missingRequired.join(", ")}.`}>
          Add them to the first row, or download the sample sheet to see the exact names, then choose the file again.
        </InlineAlert>
      ) : null}
      {preview.matchWarning !== null ? <InlineAlert tone="warning" live="off" title={preview.matchWarning} /> : null}
      {preview.unknownHeaders.length > 0 ? (
        <InlineAlert tone="info" live="off" title={`These columns are ignored: ${preview.unknownHeaders.join(", ")}.`} />
      ) : null}
      <Blocker preview={preview} />

      {preview.rows.length === 0 ? (
        <EmptyState
          title="No rows found"
          description="Put your data under the header row, save the file, then choose it again."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {note !== null ? <p className="text-body-sm text-muted">{note}</p> : null}
          <ImportExportTable
            label={`Rows found in ${preview.fileName}`}
            columns={columnsFor(preview)}
            rows={problemsFirst(preview.rows)}
            rowKey={(row) => String(row.row)}
          />
        </div>
      )}
    </div>
  );
}

interface ReviewActionsProps {
  preview: ImportExportPreview;
  onChooseAnother: () => void;
  onImport: () => Promise<void>;
}

/** The preview's footer: pick a different file, or send the rows that passed. */
export function ImportExportReviewActions({ preview, onChooseAnother, onImport }: ReviewActionsProps) {
  const started = useRef(false);
  const blocked = preview.valid === 0 || preview.missingRequired.length > 0;

  function start() {
    // Latched synchronously: the phase moves on a render later, and a second click before then
    // would send every row twice.
    if (started.current) return;
    started.current = true;
    void onImport().finally(() => {
      started.current = false;
    });
  }

  return (
    <>
      <Button variant="secondary" onClick={onChooseAnother}>
        Choose another file
      </Button>
      <Button variant="primary" disabled={blocked} onClick={start}>
        {importButtonLabel(preview)}
      </Button>
    </>
  );
}
