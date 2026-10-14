import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  FULL_PAGE,
  cancelCountSession,
  closeCountSession,
  getCountSession,
  getWarehouseById,
  listCountLines,
  reviewCountLine,
  startCountReview,
} from "@/api/client";
import type { CountLineOut, CountLineReviewStatus } from "@/api/types";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import {
  Button,
  Card,
  ConfirmDialog,
  CountLineReviewStatusPill,
  CountSessionStatusPill,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pill,
  Skeleton,
  TextArea,
} from "@/ui";
import type { Column } from "@/ui";
import { ArrowRightIcon, ClipboardCheckIcon, useToast } from "@opsgrids/ui";

type LineFilter = CountLineReviewStatus | "all";

const LINE_FILTERS: { value: LineFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PENDING", label: "Pending review" },
  { value: "APPROVED", label: "Approved" },
  { value: "REJECTED", label: "Rejected" },
  { value: "NONE", label: "No discrepancy" },
];

function BackLink() {
  return (
    <Link
      to="/counting"
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to count sessions
    </Link>
  );
}

/**
 * The manager's discrepancy review screen (ADR-0026, Fulfilment plan F8) — one session's lines,
 * filterable by review outcome, with approve/reject on each `PENDING` one. Approving posts
 * through the stock kernel on the server in the same call; nothing here computes or previews that
 * posting client-side (the server, under the bucket's own lock, is the only place that figure is
 * ever correct — ADR-0026 §6).
 */
export function CountSessionDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const canManage = useCan("operations.inventory.count.manage", "ORG");
  const canReview = useCan("operations.inventory.count.review", "ORG");

  const [lineFilter, setLineFilter] = useState<LineFilter>("PENDING");
  const [cancelling, setCancelling] = useState(false);
  const [approving, setApproving] = useState<CountLineOut | null>(null);
  const [rejecting, setRejecting] = useState<CountLineOut | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | undefined>(undefined);

  const sessionState = useAsync(() => getCountSession(id), [id]);
  const warehouseState = useAsync(
    () => (sessionState.status === "success" ? getWarehouseById(sessionState.data.warehouse_id) : Promise.resolve(null)),
    [sessionState.status === "success" ? sessionState.data.warehouse_id : null],
  );
  const linesState = useAsync(
    () =>
      listCountLines(id, {
        size: FULL_PAGE,
        ...(lineFilter === "all" ? {} : { review_status: lineFilter }),
      }),
    [id, lineFilter],
  );

  const startReview = useMutation(() => startCountReview(id), {
    onSuccess: () => {
      sessionState.reload();
      toast.success("Session moved to review.");
    },
  });
  const close = useMutation(() => closeCountSession(id), {
    onSuccess: () => {
      sessionState.reload();
      toast.success("Session closed.");
    },
  });
  const cancel = useMutation(() => cancelCountSession(id, {}), {
    onSuccess: () => {
      setCancelling(false);
      sessionState.reload();
    },
  });
  const review = useMutation(
    (input: { line: CountLineOut; decision: "approve" | "reject"; note: string }) =>
      reviewCountLine(id, input.line.id, { decision: input.decision, note: input.note }),
    {
      onSuccess: () => {
        setApproving(null);
        setRejecting(null);
        setNote("");
        linesState.reload();
        sessionState.reload();
      },
    },
  );

  function submitReject() {
    setNoteError(undefined);
    if (note.trim().length < 1) {
      setNoteError("Say why this count is being rejected.");
      return;
    }
    if (!rejecting) return;
    void review.mutate({ line: rejecting, decision: "reject", note: note.trim() });
  }

  if (sessionState.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Skeleton className="h-24 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (sessionState.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <ErrorState title="Couldn't load this session" message={problemMessage(sessionState.error)} onRetry={sessionState.reload} />
      </div>
    );
  }

  const session = sessionState.data;

  const columns: Column<CountLineOut>[] = [
    {
      key: "location",
      header: "Location",
      render: (l) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">{l.location_id.slice(0, 8)}</span>
          {l.is_unlisted ? <span className="text-xs text-muted">Found — not expected</span> : null}
        </div>
      ),
    },
    { key: "sku", header: "SKU", render: (l) => l.sku || l.inventory_item_id.slice(0, 8) },
    { key: "expected", header: "Expected", align: "right", render: (l) => <span className="tnum">{l.expected}</span> },
    {
      key: "counted",
      header: "Counted",
      align: "right",
      render: (l) => <span className="tnum">{l.counted ?? "—"}</span>,
    },
    {
      key: "review",
      header: "Review",
      render: (l) => <CountLineReviewStatusPill status={l.review_status} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (l) =>
        l.review_status === "PENDING" && canReview ? (
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => setApproving(l)}>
              Approve
            </Button>
            <Button size="sm" variant="destructive" onClick={() => setRejecting(l)}>
              Reject
            </Button>
          </div>
        ) : null,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow="Cycle count"
        title={session.label || (warehouseState.status === "success" && warehouseState.data ? warehouseState.data.name : session.warehouse_id)}
        description={`${session.scope_kind} · opened ${formatDate(session.created_at)}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <CountSessionStatusPill status={session.status} />
            {canManage && session.status === "ACTIVE" ? (
              <Button variant="secondary" loading={startReview.pending} onClick={() => startReview.mutate(undefined)}>
                Move to review
              </Button>
            ) : null}
            {canManage && session.status === "REVIEW" ? (
              <Button
                variant="secondary"
                loading={close.pending}
                disabled={session.pending_review_count > 0}
                onClick={() => close.mutate(undefined)}
              >
                Close
              </Button>
            ) : null}
            {canManage && (session.status === "ACTIVE" || session.status === "REVIEW") ? (
              <Button variant="destructive" onClick={() => setCancelling(true)}>
                Cancel
              </Button>
            ) : null}
          </div>
        }
      />

      {startReview.error ? <InlineAlert tone="danger" title={problemMessage(startReview.error)} /> : null}
      {close.error ? <InlineAlert tone="danger" title={problemMessage(close.error)} /> : null}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Lines</p>
          <p className="text-2xl font-semibold text-ink tnum">{session.line_count}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Counted</p>
          <p className="text-2xl font-semibold text-ink tnum">{session.counted_count}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Pending review</p>
          <p className="text-2xl font-semibold text-ink tnum">{session.pending_review_count}</p>
        </Card>
      </div>

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {LINE_FILTERS.map((f) => (
            <Pill key={f.value} active={lineFilter === f.value} onClick={() => setLineFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      {review.error ? <InlineAlert tone="danger" title={problemMessage(review.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {linesState.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {linesState.status === "error" ? (
          <ErrorState title="Couldn't load lines" message={problemMessage(linesState.error)} onRetry={linesState.reload} />
        ) : null}
        {linesState.status === "success" ? (
          <DataTable
            columns={columns}
            rows={linesState.data.items}
            rowKey={(l) => l.id}
            caption="Count lines"
            emptyState={
              <EmptyState
                icon={<ClipboardCheckIcon className="size-6" />}
                title="No lines match this filter"
                description="Try a different filter."
              />
            }
          />
        ) : null}
      </Card>

      <ConfirmDialog
        open={approving !== null}
        onClose={() => {
          setApproving(null);
          review.reset();
        }}
        onConfirm={() => approving && review.mutate({ line: approving, decision: "approve", note: "" })}
        title="Approve this discrepancy?"
        body={
          approving
            ? `Posts a stock adjustment so ${approving.sku || "this item"} at this location reads ${approving.counted}.`
            : ""
        }
        confirmLabel="Approve"
        pendingLabel="Posting…"
        pending={review.pending}
        {...(review.error ? { error: problemMessage(review.error) } : {})}
      />

      <ConfirmDialog
        open={rejecting !== null}
        onClose={() => {
          setRejecting(null);
          setNote("");
          setNoteError(undefined);
          review.reset();
        }}
        onConfirm={submitReject}
        title="Reject this count?"
        body="Nothing posts to stock. The count stays on record as rejected."
        confirmLabel="Reject"
        pendingLabel="Rejecting…"
        tone="danger"
        pending={review.pending}
        {...(review.error ? { error: problemMessage(review.error) } : {})}
      >
        <TextArea
          label="Reason"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={500}
          required
          {...(noteError !== undefined ? { error: noteError } : {})}
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          cancel.reset();
        }}
        onConfirm={() => cancel.mutate(undefined)}
        title="Cancel this count session?"
        body="Any line not already approved posts nothing. This can't be undone."
        confirmLabel="Cancel session"
        pendingLabel="Cancelling…"
        tone="danger"
        pending={cancel.pending}
        {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
      />
    </div>
  );
}
