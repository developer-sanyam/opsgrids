import { useState } from "react";

import { DownloadIcon } from "../icons";
import { formatCount, summarySentence } from "../lib/importExportCopy";
import type {
  ImportExportMeta,
  ImportExportMode,
  ImportExportRowOutcome,
  ImportExportRowStatus,
  ImportExportSummary,
} from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportStats } from "./ImportExportStats";
import type { ImportExportStat } from "./ImportExportStats";
import { ImportExportCellText, ImportExportTable } from "./ImportExportTable";
import type { ImportExportTableColumn } from "./ImportExportTable";
import { InlineAlert } from "./InlineAlert";
import type { InlineAlertTone } from "./InlineAlert";
import { StatusBadge } from "./StatusBadge";
import type { StatusBadgeProps } from "./StatusBadge";

/** A skipped or failed row is the registry's own word; the others are this dialog's. */
const OUTCOME_BADGE: Record<ImportExportRowStatus, StatusBadgeProps> = {
  created: { tone: "success", label: "Created" },
  updated: { tone: "success", label: "Updated" },
  unchanged: { tone: "neutral", label: "No change" },
  skipped: { status: "skipped" },
  failed: { status: "failed" },
  invalid: { tone: "warning", label: "Invalid" },
};

/**
 * The listed rows' columns. An update run also names the stored record each row was matched to, so a
 * Record ID column that slipped by a row is seen in the result as it was in the preview.
 */
function columnsFor(outcomes: readonly ImportExportRowOutcome[]): ImportExportTableColumn<ImportExportRowOutcome>[] {
  const naming = outcomes.some((outcome) => outcome.record !== undefined && outcome.record !== "");
  return [
    { key: "row", header: "Row", rowHeader: true, className: "tnum text-muted", render: (outcome) => outcome.row },
    { key: "item", header: "Item", render: (outcome) => <ImportExportCellText value={outcome.label} /> },
    ...(naming
      ? [
          {
            key: "record",
            header: "Existing record",
            render: (outcome: ImportExportRowOutcome) => <ImportExportCellText value={outcome.record} />,
          },
        ]
      : []),
    { key: "result", header: "Result", render: (outcome) => <StatusBadge {...OUTCOME_BADGE[outcome.status]} /> },
    {
      key: "details",
      header: "Details",
      className: "min-w-48",
      render: (outcome) => <ImportExportCellText value={outcome.message} />,
    },
  ];
}

function toneOf(summary: ImportExportSummary): InlineAlertTone {
  if (summary.cancelled) return "info";
  if (summary.failed + summary.invalid > 0) return "warning";
  return summary.created + summary.updated > 0 ? "success" : "info";
}

/** What became of the rows: adding can skip a row that exists, updating never does (a 409 there is a failure). */
function statsFor(summary: ImportExportSummary, mode: ImportExportMode): { stats: ImportExportStat[]; columns: string } {
  if (mode === "add") {
    return {
      columns: "grid-cols-2 sm:grid-cols-4",
      stats: [
        { label: "Created", value: summary.created },
        { label: "Skipped, already exist", value: summary.skipped },
        { label: "Failed", value: summary.failed },
        { label: "Invalid", value: summary.invalid },
      ],
    };
  }
  return {
    columns: "grid-cols-2 sm:grid-cols-5",
    stats: [
      { label: "Created", value: summary.created },
      { label: "Updated", value: summary.updated },
      { label: "Unchanged", value: summary.unchanged },
      { label: "Failed", value: summary.failed },
      { label: "Invalid", value: summary.invalid },
    ],
  };
}

/** Rows that need a look: not made, not changed, and not already right. */
function needsLook(outcome: ImportExportRowOutcome): boolean {
  return outcome.status === "skipped" || outcome.status === "failed" || outcome.status === "invalid";
}

interface ResultProps {
  summary: ImportExportSummary | null;
  meta: ImportExportMeta;
  mode: ImportExportMode;
  /** The last download's failure, when the result report could not be built. */
  notice: string | null;
}

/**
 * The done phase: one sentence that says how it went, the counts, and every row that was not
 * saved, with the reason. A clean run has no table at all, and the sentence says everything
 * imported: an empty list under a "problems" heading is a question, not an answer. Rows that were
 * updated or already matched are counted, not listed; the result report lists every row.
 */
export function ImportExportResult({ summary, meta, mode, notice }: ResultProps) {
  if (summary === null) {
    return <InlineAlert tone="info" live="off" title="The import has finished." />;
  }
  const problems = summary.outcomes.filter(needsLook);
  const { stats, columns } = statsFor(summary, mode);
  const saved = mode === "add" ? "created" : "imported";
  return (
    <div className="flex flex-col gap-4">
      {notice !== null ? <InlineAlert tone="danger" title={notice} /> : null}
      <InlineAlert tone={toneOf(summary)} live="off" title={summarySentence(summary, meta)} />

      <ImportExportStats columns={columns} stats={stats} />

      {problems.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="text-body-sm text-muted">
            {`${formatCount(problems.length)} ${problems.length === 1 ? "row was" : "rows were"} not ${saved}. The result report lists every row.`}
          </p>
          <ImportExportTable
            label={`Rows that were not ${saved}`}
            columns={columnsFor(problems)}
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
