import { useState } from "react";
import { Link } from "react-router-dom";

import { cancelCountSession, listCountSessions, listWarehouses, startCountReview } from "@/api/client";
import type { CountSessionOut, CountSessionStatus } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import { OpenCountSessionDialog } from "@/forms/OpenCountSessionDialog";
import {
  Button,
  Card,
  ConfirmDialog,
  CountSessionStatusPill,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardCheckIcon } from "@opsgrids/ui";

type StatusFilter = CountSessionStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ACTIVE", label: "Active" },
  { value: "REVIEW", label: "In review" },
  { value: "CLOSED", label: "Closed" },
  { value: "CANCELLED", label: "Cancelled" },
];

/**
 * Cycle counting / stocktaking (ADR-0026, Fulfilment plan F8) — the manager's board. Counting a
 * line is the worker app's job (`frontend/worker-app`); this screen opens a session, tracks it,
 * and links to the discrepancy review screen once it reaches `REVIEW`.
 */
export function CountSessionsPage() {
  const canManage = useCan("operations.inventory.count.manage", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [opening, setOpening] = useState(false);
  const [cancelling, setCancelling] = useState<CountSessionOut | null>(null);

  const loader = () => listCountSessions({ status, page, size });
  const state = useAsync(loader, [status, page, size]);

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouseLabel = new Map(
    warehousesState.status === "success" ? warehousesState.data.items.map((w) => [w.id, `${w.code} · ${w.name}`]) : [],
  );

  const review = useMutation((s: CountSessionOut) => startCountReview(s.id), { onSuccess: () => state.reload() });
  const cancel = useMutation(
    (s: CountSessionOut) => cancelCountSession(s.id, {}),
    { onSuccess: () => { setCancelling(null); state.reload(); } },
  );

  const columns: Column<CountSessionOut>[] = [
    {
      key: "session",
      header: "Session",
      render: (s) => (
        <div className="flex flex-col leading-tight">
          <Link to={`/counting/${s.id}`} className="font-semibold text-ink hover:underline">
            {s.label || warehouseLabel.get(s.warehouse_id) || s.warehouse_id.slice(0, 8)}
          </Link>
          <span className="text-xs text-muted">{warehouseLabel.get(s.warehouse_id) ?? s.warehouse_id}</span>
        </div>
      ),
    },
    {
      key: "scope",
      header: "Scope",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="text-muted">{s.scope_kind}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (s) => <CountSessionStatusPill status={s.status} />,
    },
    {
      key: "progress",
      header: "Lines",
      align: "right",
      render: (s) => (
        <span className="tnum text-muted">
          {s.counted_count}/{s.line_count}
          {s.pending_review_count > 0 ? ` · ${s.pending_review_count} pending` : ""}
        </span>
      ),
    },
    {
      key: "opened",
      header: "Opened",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="text-muted">{formatDate(s.created_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (s) => (
        <RowActions
          label={`Count session ${s.label || s.id.slice(0, 8)}`}
          actions={[
            {
              label: "Move to review",
              onSelect: () => review.mutate(s),
              hidden: !canManage || s.status !== "ACTIVE",
            },
            {
              label: "Cancel",
              tone: "danger",
              onSelect: () => setCancelling(s),
              hidden: !canManage || (s.status !== "ACTIVE" && s.status !== "REVIEW"),
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
        eyebrow="Inventory"
        title="Cycle counts"
        description="Open a stocktake, track it while it runs, and review the discrepancies it finds."
        actions={
          canManage ? (
            <Button onClick={() => setOpening(true)}>Open count</Button>
          ) : undefined
        }
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

      {review.error ? <InlineAlert tone="danger" title={problemMessage(review.error)} /> : null}
      {cancel.error ? <InlineAlert tone="danger" title={problemMessage(cancel.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load count sessions" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(s) => s.id}
              caption="Count sessions"
              emptyState={
                <EmptyState
                  icon={<ClipboardCheckIcon className="size-6" />}
                  title={filtersActive ? "No sessions match this filter" : "No count sessions yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the filter."
                      : "Open one to start a stocktake."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="sessions"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {opening ? (
        <Can permission="operations.inventory.count.manage" scope="ORG">
          <OpenCountSessionDialog
            onClose={() => setOpening(false)}
            onOpened={() => {
              setOpening(false);
              state.reload();
            }}
          />
        </Can>
      ) : null}

      <ConfirmDialog
        open={cancelling !== null}
        onClose={() => {
          setCancelling(null);
          cancel.reset();
        }}
        onConfirm={() => cancelling && cancel.mutate(cancelling)}
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
