import { useState } from "react";

import { cancelTask, listTasks, listWorkers } from "@/api/client";
import type { PickTaskOut, PickTaskStatus } from "@/api/types";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { OfferTaskDialog } from "@/forms/OfferTaskDialog";
import {
  AssignmentKindPill,
  AssignmentOfferStatusPill,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  Pill,
  PickTaskStatusPill,
  RowActions,
  Skeleton,
  TextArea,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardCheckIcon } from "@opsgrids/ui";

type StatusFilter = PickTaskStatus | "all";

/** ADR-0025 §2's exact vocabulary — the filter row offers exactly this set. */
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "CREATED", label: "Created" },
  { value: "OFFERED", label: "Offered" },
  { value: "ESCALATED", label: "Escalated" },
  { value: "ACCEPTED", label: "Accepted" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "EXCEPTION", label: "Exception" },
  { value: "PICKED", label: "Picked" },
  { value: "CLOSED", label: "Closed" },
  { value: "CANCELLED", label: "Cancelled" },
];

/** Cancelling a task already in a worker's hands needs `operations.fulfilment.task.override`
 *  (ADR-0025 §2) — a stronger permission on an edge that IS in the map, never a way around it. */
const OVERRIDE_REQUIRED_FOR_CANCEL: ReadonlySet<PickTaskStatus> = new Set([
  "ACCEPTED",
  "IN_PROGRESS",
  "EXCEPTION",
]);

const OPEN_STATUSES: ReadonlySet<PickTaskStatus> = new Set([
  "CREATED",
  "OFFERED",
  "ESCALATED",
  "ACCEPTED",
  "IN_PROGRESS",
  "EXCEPTION",
]);

/**
 * The dispatcher's board (plan F3): every pick task, its current offer and round, offer-to-a-
 * worker and cancel. Scan, pick and pack confirmation belong to the worker PWA
 * (`frontend/worker-app`), a separate app — this screen only ever dispatches and tracks.
 */
export function FulfilmentTasksPage() {
  const canAssign = useCan("operations.fulfilment.task.assign", "ORG");
  const canOverride = useCan("operations.fulfilment.task.override", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [offering, setOffering] = useState<PickTaskOut | null>(null);
  const [cancelling, setCancelling] = useState<PickTaskOut | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelReasonError, setCancelReasonError] = useState<string | undefined>(undefined);

  const loader = () => listTasks({ status, page, size });
  const state = useAsync(loader, [status, page, size]);

  // Every worker named by a task's own offers, resolved once for the whole page (N+1 avoidance)
  // rather than one lookup per row.
  const workersState = useAsync(() => listWorkers({ size: 200 }), []);
  const workerName = new Map(
    workersState.status === "success" ? workersState.data.items.map((w) => [w.id, w.display_name || w.employee_id]) : [],
  );

  const cancel = useMutation(
    (input: { task: PickTaskOut; reason: string }) => cancelTask(input.task.id, { reason: input.reason }),
    {
      onSuccess: () => {
        setCancelling(null);
        setCancelReason("");
        state.reload();
      },
    },
  );

  function canCancel(task: PickTaskOut): boolean {
    if (!OPEN_STATUSES.has(task.status)) return false;
    return OVERRIDE_REQUIRED_FOR_CANCEL.has(task.status) ? canOverride : canAssign;
  }

  function canOffer(task: PickTaskOut): boolean {
    if (task.status === "CREATED" || task.status === "ESCALATED") return canAssign;
    // Auto-assignment's own override path (this build): a live, not-yet-accepted offer —
    // auto-picked or manual — can be redirected by a caller holding task.override. A plain
    // task.assign caller sees no "Offer" action here at all, the same as F3 has always shown.
    if (task.status === "OFFERED") return canOverride;
    return false;
  }

  function isOverride(task: PickTaskOut): boolean {
    return task.status === "OFFERED";
  }

  function submitCancel() {
    setCancelReasonError(undefined);
    if (cancelReason.trim().length < 1) {
      setCancelReasonError("Say why this task is being cancelled.");
      return;
    }
    if (!cancelling) return;
    void cancel.mutate({ task: cancelling, reason: cancelReason.trim() });
  }

  const columns: Column<PickTaskOut>[] = [
    {
      key: "task",
      header: "Task",
      render: (t) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">Order {t.order_id.slice(0, 8)}</span>
          <span className="text-xs text-muted">
            {t.lines.length} line{t.lines.length === 1 ? "" : "s"}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (t) => <PickTaskStatusPill status={t.status} />,
    },
    {
      key: "round",
      header: "Round",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (t) => (
        <span className="tnum text-muted">
          {t.round} / {t.max_rounds}
        </span>
      ),
    },
    {
      key: "offer",
      header: "Current offer",
      render: (t) => {
        const live = t.offers.find((o) => o.state === "OFFERED" || o.state === "ACCEPTED");
        if (!live) {
          if (t.auto_assign_failure_reason) {
            return <span className="text-xs text-danger">{t.auto_assign_failure_reason}</span>;
          }
          return <span className="text-faint">—</span>;
        }
        return (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <AssignmentOfferStatusPill state={live.state} />
              <AssignmentKindPill kind={live.score_breakdown?.["kind"]} />
            </div>
            <span className="text-xs text-faint">{workerName.get(live.worker_id) ?? "Unknown worker"}</span>
          </div>
        );
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (t) => (
        <RowActions
          label={`Task on order ${t.order_id.slice(0, 8)}`}
          actions={[
            {
              label: isOverride(t) ? "Reassign" : "Offer",
              onSelect: () => setOffering(t),
              hidden: !canOffer(t),
            },
            {
              label: "Cancel",
              tone: "danger",
              onSelect: () => setCancelling(t),
              hidden: !canCancel(t),
            },
          ]}
        />
      ),
    },
  ];

  const filtersActive = status !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Tasks"
        description="Every pick task, its round and its current offer — offer a task to a worker and track it through."
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {STATUS_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={status === f.value}
              onClick={() => {
                setStatus(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      {cancel.error ? <InlineAlert tone="danger" title={problemMessage(cancel.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load tasks" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(t) => t.id}
              caption="Pick tasks"
              emptyState={
                <EmptyState
                  icon={<ClipboardCheckIcon className="size-6" />}
                  title={filtersActive ? "No tasks match this filter" : "No pick tasks yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the filter."
                      : "Tasks appear here once an accepted order starts fulfilment."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="tasks"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {/* `canOffer` already decides task.assign vs task.override by the task's own status
          (`Can` only ever checks one permission, and a fresh offer and an override need
          different ones) — the row action that opened this dialog already passed that gate, so
          this mirrors it rather than re-checking a single hardcoded permission. */}
      {offering && canOffer(offering) ? (
        <OfferTaskDialog
          task={offering}
          onClose={() => setOffering(null)}
          onOffered={() => {
            setOffering(null);
            state.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={cancelling !== null}
        onClose={() => {
          setCancelling(null);
          setCancelReason("");
          setCancelReasonError(undefined);
          cancel.reset();
        }}
        onConfirm={submitCancel}
        title="Cancel this task?"
        body={
          cancelling && OVERRIDE_REQUIRED_FOR_CANCEL.has(cancelling.status)
            ? "A worker already has this task in hand. Cancelling forces it back — this can't be undone."
            : "This task moves to cancelled. This can't be undone."
        }
        confirmLabel="Cancel task"
        pendingLabel="Cancelling…"
        tone="danger"
        pending={cancel.pending}
        {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
      >
        <TextArea
          label="Reason"
          value={cancelReason}
          onChange={(e) => setCancelReason(e.target.value)}
          maxLength={500}
          {...(cancelReasonError !== undefined ? { error: cancelReasonError } : {})}
        />
      </ConfirmDialog>
    </div>
  );
}
