import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ChevronRightIcon,
  ErrorState,
  InlineAlert,
  PageHeader,
  Skeleton,
  TextArea,
} from "@opsgrids/ui";

import { RequireGrant } from "@/auth/RequireGrant";
import { acceptOffer, declineOffer, getTask, releaseTask, resolveException } from "@/api/client";
import type { PickExceptionOut } from "@/api/types";
import { formatQty, qtyToMilli } from "@/lib/quantity";
import { shortId } from "@/lib/shortId";
import { AssignmentOfferStatusPill, PickTaskStatusPill } from "@/ui/StatusPill";
import { useMyWorker } from "@/worker/context";
import { findMyLiveOffer } from "@/worker/offers";
import { LINE_STATUS_LABEL, remainingMilli, sortedLines } from "@/worker/taskLines";

const EXCEPTION_REASON_LABEL: Record<PickExceptionOut["reason"], string> = {
  short_pick: "Short pick",
  damaged: "Damaged",
  not_found: "Not found",
  other: "Other",
};

export function TaskDetailPage() {
  return (
    <RequireGrant permission="operations.fulfilment.task.read" scope="ORG" what="this task">
      <TaskDetailContent />
    </RequireGrant>
  );
}

function TaskDetailContent() {
  const { taskId } = useParams<{ taskId: string }>();
  const worker = useMyWorker();
  const state = useAsync(() => getTask(taskId ?? ""), [taskId]);

  if (!taskId) return null;

  if (state.status === "loading" || worker.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-9 w-48 rounded-tile" />
        <Card className="p-6">
          <Skeleton className="h-48 w-full rounded-tile" />
        </Card>
      </div>
    );
  }

  if (state.status === "error") {
    const notFound = state.error instanceof ApiError && state.error.status === 404;
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Task" />
        <Card className="p-2">
          <ErrorState
            title={notFound ? "That task isn't available" : "Couldn't load this task"}
            message={
              notFound
                ? "It may have been cancelled, or it isn't part of your organization."
                : problemMessage(state.error)
            }
            {...(notFound ? {} : { onRetry: state.reload })}
            action={
              <Link to="/" className="text-sm font-semibold text-accent hover:underline">
                Back to tasks
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  if (worker.status !== "ready" || !worker.worker) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Task" />
        <InlineAlert tone="warning" title="Your worker profile couldn't be found">
          Ask your supervisor to link your account to a worker profile.
        </InlineAlert>
      </div>
    );
  }

  const task = state.data;
  const myOffer = findMyLiveOffer(task, worker.worker.id);
  const lines = sortedLines(task.lines);
  const openExceptions = task.exceptions.filter((e) => e.status === "open");

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow={`Round ${task.round} of ${task.max_rounds}`}
        title={`Order ${shortId(task.order_id)}`}
        actions={<PickTaskStatusPill status={task.status} />}
      />

      {myOffer?.state === "OFFERED" ? (
        <OfferResponse offerId={myOffer.id} onDone={state.reload} />
      ) : null}

      {task.status === "ACCEPTED" && myOffer?.state === "ACCEPTED" ? (
        <ReleaseAction taskId={task.id} onDone={state.reload} />
      ) : null}

      {openExceptions.length > 0 ? (
        <div className="flex flex-col gap-3">
          {openExceptions.map((exception) => (
            <ExceptionCard key={exception.id} taskId={task.id} exception={exception} onResolved={state.reload} />
          ))}
        </div>
      ) : null}

      {task.status === "PICKED" ? (
        <InlineAlert tone="success" title="Every line is picked">
          <Link to={`/orders/${task.order_id}/pack`} className="font-semibold underline">
            Pack this order
          </Link>
        </InlineAlert>
      ) : null}

      {(task.status === "ACCEPTED" || task.status === "IN_PROGRESS" || task.status === "EXCEPTION") &&
      myOffer?.state === "ACCEPTED" ? (
        <Link
          to={`/tasks/${task.id}/scan`}
          className="flex h-12 items-center justify-center gap-2 rounded-tile bg-accent px-4 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
        >
          Scan
        </Link>
      ) : null}

      <Card className="flex flex-col divide-y divide-line p-0">
        {lines.map((line) => {
          const remaining = remainingMilli(line);
          const canPick =
            remaining > 0 &&
            (task.status === "ACCEPTED" || task.status === "IN_PROGRESS") &&
            myOffer?.state === "ACCEPTED";
          return (
            <div key={line.id} className="flex items-center gap-3 p-4">
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="truncate font-mono text-sm font-semibold text-ink">{line.sku}</span>
                <span className="text-xs text-muted">
                  {formatQty(line.quantity)} requested
                  {qtyMilliZero(line.picked) ? "" : ` · ${formatQty(line.picked)} picked`}
                  {qtyMilliZero(line.short) ? "" : ` · ${formatQty(line.short)} short`}
                </span>
                <span className="text-xs text-faint">
                  {LINE_STATUS_LABEL[line.status]} · from {shortId(line.source_location_id)}
                  {line.sequence !== null ? ` · stop ${line.sequence}` : ""}
                </span>
              </div>
              {canPick ? (
                <Link
                  to={`/tasks/${task.id}/lines/${line.id}/pick`}
                  className="flex h-10 shrink-0 items-center gap-1 rounded-tile border border-field-edge px-3 text-sm font-semibold text-ink hover:bg-sunken"
                >
                  Confirm
                  <ChevronRightIcon className="size-4" aria-hidden />
                </Link>
              ) : null}
            </div>
          );
        })}
      </Card>

      {task.offers.length > 0 ? (
        <Card className="flex flex-col gap-2 p-4">
          <h2 className="text-sm font-semibold text-muted">Offer history</h2>
          <ul className="flex flex-col gap-2">
            {task.offers.map((offer) => (
              <li key={offer.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-muted">Round {offer.round}</span>
                <AssignmentOfferStatusPill state={offer.state} />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function qtyMilliZero(qty: string): boolean {
  return qtyToMilli(qty) === 0;
}

function OfferResponse({ offerId, onDone }: { offerId: string; onDone: () => void }) {
  const accept = useMutation(() => acceptOffer(offerId), { onSuccess: onDone });
  const decline = useMutation(() => declineOffer(offerId), { onSuccess: onDone });
  const pending = accept.pending || decline.pending;
  const error = accept.error ?? decline.error;

  return (
    <Card className="flex flex-col gap-3 p-4">
      <p className="text-sm font-semibold text-ink">You've been offered this task.</p>
      {error ? <InlineAlert tone="danger" title={problemMessage(error)} /> : null}
      <div className="flex gap-2">
        <Button className="flex-1" onClick={() => void accept.mutate(undefined)} loading={accept.pending} disabled={pending}>
          Accept
        </Button>
        <Button
          variant="secondary"
          className="flex-1"
          onClick={() => void decline.mutate(undefined)}
          loading={decline.pending}
          disabled={pending}
        >
          Decline
        </Button>
      </div>
    </Card>
  );
}

function ReleaseAction({ taskId, onDone }: { taskId: string; onDone: () => void }) {
  const release = useMutation(() => releaseTask(taskId), { onSuccess: onDone });
  return (
    <Card className="flex flex-col gap-2 p-4">
      {release.error ? <InlineAlert tone="danger" title={problemMessage(release.error)} /> : null}
      <Button variant="secondary" onClick={() => void release.mutate(undefined)} loading={release.pending}>
        Hand this task back
      </Button>
    </Card>
  );
}

function ExceptionCard({
  taskId,
  exception,
  onResolved,
}: {
  taskId: string;
  exception: PickExceptionOut;
  onResolved: () => void;
}) {
  const [note, setNote] = useState("");
  const resolve = useMutation(() => resolveException(taskId, exception.id, { note: note.trim() }), {
    onSuccess: () => {
      setNote("");
      onResolved();
    },
  });

  return (
    <InlineAlert tone="warning" title={`${EXCEPTION_REASON_LABEL[exception.reason]} — ${formatQty(exception.quantity)}`}>
      <div className="mt-2 flex flex-col gap-2">
        {exception.note ? <p className="text-sm">{exception.note}</p> : null}
        {resolve.error ? <p className="text-sm font-medium text-negative">{problemMessage(resolve.error)}</p> : null}
        <TextArea
          label="Resolution note"
          optional
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={500}
        />
        <Button size="sm" onClick={() => void resolve.mutate(undefined)} loading={resolve.pending}>
          Resolve and resume picking
        </Button>
      </div>
    </InlineAlert>
  );
}
