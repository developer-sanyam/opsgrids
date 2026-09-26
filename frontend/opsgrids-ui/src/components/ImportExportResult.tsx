import { useState } from "react";

import { DownloadIcon } from "../icons";
import { formatCount, summarySentence } from "../lib/importExportCopy";
import type {
  ImportExportMeta,
  ImportExportRowOutcome,
  ImportExportRowStatus,
  ImportExportSummary,
} from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportStats } from "./ImportExportStats";
import { ImportExportCellText, ImportExportTable } from "./ImportExportTable";
import type { ImportExportTableColumn } from "./ImportExportTable";
import { InlineAlert } from "./InlineAlert";
import type { InlineAlertTone } from "./InlineAlert";
import { StatusBadge } from "./StatusBadge";
import type { StatusBadgeProps } from "./StatusBadge";

/** A skipped or failed row is the registry's own word; "created" and "invalid" are this dialog's. */
const OUTCOME_BADGE: Record<ImportExportRowStatus, StatusBadgeProps> = {
  created: { tone: "success", label: "Created" },
  skipped: { status: "skipped" },
  failed: { status: "failed" },
  invalid: { tone: "warning", label: "Invalid" },
};

const COLUMNS: ImportExportTableColumn<ImportExportRowOutcome>[] = [
  { key: "row", header: "Row", rowHeader: true, className: "tnum text-muted", render: (outcome) => outcome.row },
  { key: "item", header: "Item", render: (outcome) => <ImportExportCellText value={outcome.label} /> },
  { key: "result", header: "Result", render: (outcome) => <StatusBadge {...OUTCOME_BADGE[outcome.status]} /> },
  {
    key: "details",
    header: "Details",
    className: "min-w-48",
    render: (outcome) => <ImportExportCellText value={outcome.message} />,
  },
];

function toneOf(summary: ImportExportSummary): InlineAlertTone {
  if (summary.cancelled) return "info";
  if (summary.failed + summary.invalid > 0) return "warning";
  return summary.created > 0 ? "success" : "info";
}

interface ResultProps {
  summary: ImportExportSummary | null;
  meta: ImportExportMeta;
  /** The last download's failure, when the result report could not be built. */
  notice: string | null;
}

/**
 * The done phase: one sentence that says how it went, the four counts, and every row that was not
 * created with the reason. A clean run has no table at all, and the sentence says everything
 * imported: an empty list under a "problems" heading is a question, not an answer.
 */
export function ImportExportResult({ summary, meta, notice }: ResultProps) {
  if (summary === null) {
    return <InlineAlert tone="info" live="off" title="The import has finished." />;
  }
  const problems = summary.outcomes.filter((outcome) => outcome.status !== "created");
  return (
    <div className="flex flex-col gap-4">
      {notice !== null ? <InlineAlert tone="danger" title={notice} /> : null}
      <InlineAlert tone={toneOf(summary)} live="off" title={summarySentence(summary, meta)} />

      <ImportExportStats
        columns="grid-cols-2 sm:grid-cols-4"
        stats={[
          { label: "Created", value: summary.created },
          { label: "Skipped, already exist", value: summary.skipped },
          { label: "Failed", value: summary.failed },
          { label: "Invalid", value: summary.invalid },
        ]}
      />

      {problems.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="text-body-sm text-muted">
            {`${formatCount(problems.length)} ${problems.length === 1 ? "row was" : "rows were"} not created. The result report lists every row.`}
          </p>
          <ImportExportTable
            label="Rows that were not created"
            columns={COLUMNS}
            rows={problems}
            rowKey={(outcome) => String(outcome.row)}
          />
        </div>
      ) : null}
    </div>
  );
}

interface ResultActionsProps {
  onDownload: () => Promise<void>;
  onDone: () => void;
}

/** The done phase's footer: keep the report, then leave. */
export function ImportExportResultActions({ onDownload, onDone }: ResultActionsProps) {
  const [building, setBuilding] = useState(false);

  async function download() {
    setBuilding(true);
    try {
      await onDownload();
    } finally {
      setBuilding(false);
    }
  }

  return (
    <>
      <Button
        variant="secondary"
        loading={building}
        leadingIcon={<DownloadIcon className="size-4" />}
        onClick={() => void download()}
      >
        Download result report
      </Button>
      <Button variant="primary" onClick={onDone}>
        Done
      </Button>
    </>
  );
}
