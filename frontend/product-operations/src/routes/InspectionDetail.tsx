import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getInspection } from "@/api/client";
import type { InspectionStep } from "@/api/client";
import type { InspectionResultOut } from "@/api/types";
import { problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  DispositionDialog,
  RejectDialog,
  ReleaseDialog,
  StepDialog,
} from "@/forms/InspectionDecisionDialogs";
import { RecordResultsDialog } from "@/forms/RecordResultsDialog";
import { formatDateTime } from "@/lib/format";
import { availableActions, formatBounds } from "@/lib/quality";
import { formatQty } from "@/lib/quantity";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  InspectionStatusPill,
  PageHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { ArrowRightIcon, ClipboardListIcon, useToast } from "@opsgrids/ui";

type Dialog = "record" | InspectionStep | "release" | "reject" | "dispose" | null;

const KIND_LABEL = { incoming: "Incoming", "in-process": "In-process", finished: "Finished" } as const;
const BUCKET_LABEL = { REJECTED: "Rejected", QUARANTINE: "Quarantine" } as const;
const DISPOSITION_LABEL = {
  NONE: "Not yet",
  RETURN_TO_VENDOR: "Returned to vendor",
  SCRAP: "Scrapped",
} as const;

function BackLink() {
  return (
    <Link
      to="/quality/inspections"
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to inspections
    </Link>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="text-xl font-semibold text-ink tnum">{value}</p>
    </Card>
  );
}

/**
 * One inspection: what was held, what was measured, and the decisions. Every button is shown only
 * when the person holds the permission **and** the inspection's state allows it
 * (`availableActions`) — a convenience, not the protection; the server re-checks both. A release
 * or rejection moves stock through the ledger on the server; nothing here previews that figure.
 */
export function InspectionDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const canInspect = useCan("operations.quality.inspection.inspect", "ORG");
  const canApprove = useCan("operations.quality.inspection.approve", "ORG");
  const canReject = useCan("operations.quality.inspection.reject", "ORG");
  const [dialog, setDialog] = useState<Dialog>(null);

  const state = useAsync(() => getInspection(id), [id]);

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Skeleton className="h-24 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <ErrorState title="Couldn't load this inspection" message={problemMessage(state.error)} onRetry={state.reload} />
      </div>
    );
  }

  const inspection = state.data;
  const actions = availableActions(inspection, { inspect: canInspect, approve: canApprove, reject: canReject });
  const can = (a: (typeof actions)[number]) => actions.includes(a);

  function done(message: string) {
    return () => {
      setDialog(null);
      state.reload();
      toast.success(message);
    };
  }

  const columns: Column<InspectionResultOut>[] = [
    {
      key: "param",
      header: "Parameter",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{r.param_name}</span>
          {r.is_critical ? <span className="text-xs text-muted">Critical</span> : null}
        </div>
      ),
    },
    {
      key: "limits",
      header: "Limits",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => (
        <span className="text-muted tnum">
          {formatBounds({ kind: r.param_kind, min: r.min, max: r.max })}
          {r.unit ? ` ${r.unit}` : ""}
        </span>
      ),
    },
    {
      key: "value",
      header: "Reading",
      align: "right",
      render: (r) => <span className="tnum">{r.value ?? "—"}</span>,
    },
    {
      key: "verdict",
      header: "Result",
      render: (r) => <Tag>{r.passed ? "Pass" : "Fail"}</Tag>,
    },
    {
      key: "note",
      header: "Note",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="text-muted">{r.note || "—"}</span>,
    },
  ];

  const decided = ["PASSED", "FAILED", "PARTIAL_PASS"].includes(inspection.status);

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow={`${KIND_LABEL[inspection.kind]} inspection`}
        title={inspection.item_sku || inspection.item_id}
        description={`${inspection.item_name} · ${inspection.location_code || "—"} · opened ${formatDateTime(inspection.created_at)}`}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <InspectionStatusPill status={inspection.status} />
            {can("record") ? (
              <Button variant="secondary" onClick={() => setDialog("record")}>
                Record readings
              </Button>
            ) : null}
            {can("hold") ? (
              <Button variant="secondary" onClick={() => setDialog("hold")}>
                Hold
              </Button>
            ) : null}
            {can("rework") ? (
              <Button variant="secondary" onClick={() => setDialog("rework")}>
                Rework
              </Button>
            ) : null}
            {can("reinspect") ? (
              <Button variant="secondary" onClick={() => setDialog("reinspect")}>
                Re-inspect
              </Button>
            ) : null}
            {can("release") ? (
              <Button disabled={!inspection.can_release} onClick={() => setDialog("release")}>
                Release
              </Button>
            ) : null}
            {can("reject") ? (
              <Button variant="destructive" onClick={() => setDialog("reject")}>
                Reject
              </Button>
            ) : null}
            {can("dispose") ? (
              <Button variant="destructive" onClick={() => setDialog("dispose")}>
                Dispose
              </Button>
            ) : null}
          </div>
        }
      />

      {can("release") && !inspection.can_release ? (
        <InlineAlert tone="danger" title="A critical parameter failed">
          This stock cannot be released. Reject it, send it for rework, or keep it on hold.
        </InlineAlert>
      ) : null}
      {inspection.status === "PENDING" && inspection.suggested_outcome !== "PENDING" ? (
        <InlineAlert
          tone="info"
          title={`The readings suggest: ${inspection.suggested_outcome.replace("_", " ").toLowerCase()}`}
        >
          A suggestion from the recorded readings — the decision is yours.
        </InlineAlert>
      ) : null}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Fact label="Inspected" value={`${formatQty(inspection.quantity)} ${inspection.uom}`} />
        <Fact
          label="Accepted"
          value={inspection.accepted !== null ? `${formatQty(inspection.accepted)} ${inspection.uom}` : "—"}
        />
        <Fact
          label="Rejected"
          value={inspection.rejected !== null ? `${formatQty(inspection.rejected)} ${inspection.uom}` : "—"}
        />
        <Fact
          label="Rejected stock"
          value={
            inspection.rejected_bucket
              ? `${BUCKET_LABEL[inspection.rejected_bucket]} · ${DISPOSITION_LABEL[inspection.disposition]}`
              : "—"
          }
        />
      </div>

      {decided ? (
        <Card className="p-4 sm:p-6 flex flex-col gap-1">
          <p className="text-sm font-semibold text-ink">Decision</p>
          <p className="text-sm text-muted">
            {inspection.decided_at ? `Decided ${formatDateTime(inspection.decided_at)}` : "Decided"}
            {inspection.decision_note ? ` — ${inspection.decision_note}` : ""}
          </p>
          {inspection.disposition !== "NONE" ? (
            <p className="text-sm text-muted">
              {DISPOSITION_LABEL[inspection.disposition]}
              {inspection.disposition_reference ? ` · ${inspection.disposition_reference}` : ""}
              {inspection.disposition_note ? ` — ${inspection.disposition_note}` : ""}
            </p>
          ) : null}
        </Card>
      ) : null}

      <Card className="p-2 sm:p-4">
        <DataTable
          columns={columns}
          rows={inspection.results}
          rowKey={(r) => r.id}
          caption="Readings"
          emptyState={
            <EmptyState
              icon={<ClipboardListIcon className="size-6" />}
              title="No readings recorded yet"
              description={can("record") ? "Record what you measured to judge this stock." : "Nothing has been measured yet."}
            />
          }
        />
      </Card>

      {inspection.notes ? (
        <Card className="p-4 sm:p-6">
          <p className="text-sm font-semibold text-ink">Notes</p>
          <p className="text-sm text-muted">{inspection.notes}</p>
        </Card>
      ) : null}

      {dialog === "record" && can("record") ? (
        <RecordResultsDialog
          inspection={inspection}
          onClose={() => setDialog(null)}
          onSaved={done("Readings saved.")}
        />
      ) : null}
      {(dialog === "hold" || dialog === "rework" || dialog === "reinspect") && can(dialog) ? (
        <StepDialog
          step={dialog}
          inspection={inspection}
          onClose={() => setDialog(null)}
          onDone={done(
            dialog === "hold" ? "Kept on hold." : dialog === "rework" ? "Sent for rework." : "Back to pending.",
          )}
        />
      ) : null}
      {dialog === "release" && can("release") ? (
        <ReleaseDialog inspection={inspection} onClose={() => setDialog(null)} onDone={done("Stock released.")} />
      ) : null}
      {dialog === "reject" && can("reject") ? (
        <RejectDialog inspection={inspection} onClose={() => setDialog(null)} onDone={done("Stock rejected.")} />
      ) : null}
      {dialog === "dispose" && can("dispose") ? (
        <DispositionDialog inspection={inspection} onClose={() => setDialog(null)} onDone={done("Stock disposed of.")} />
      ) : null}
    </div>
  );
}
