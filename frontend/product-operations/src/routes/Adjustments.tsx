import { useState } from "react";

import { cancelAdjustment, listAdjustments } from "@/api/movements";
import type { AdjustmentOut, AdjustmentStatus } from "@/api/movementTypes";
import { Can, problemMessage, useCan, useMutation } from "@opsgrids/client";
import { ADJUSTMENT_REASONS } from "@/lib/adjustmentForm";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { AdjustmentRequestDialog } from "@/forms/AdjustmentRequestDialog";
import { ReasonDialog } from "@/forms/ReasonDialog";
import { LoadMoreFooter } from "@/movements/LoadMoreFooter";
import { useCursorList } from "@/tracking/useCursorList";
import {
  AdjustmentStatusPill,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pill,
  RowActions,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardCheckIcon, useToast } from "@opsgrids/ui";

type StatusFilter = AdjustmentStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PENDING_APPROVAL", label: "Waiting for approval" },
  { value: "APPLIED", label: "Applied" },
  { value: "REJECTED", label: "Rejected" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "FAILED", label: "Failed" },
];

const REASON_LABEL = new Map<string, string>(ADJUSTMENT_REASONS.map((r) => [r.value, r.label]));

/** What came of the request, beyond the status word: who is deciding, who decided, why it failed. */
function outcomeNote(a: AdjustmentOut): string | null {
  if (a.status === "PENDING_APPROVAL") return `Waiting for ${a.approver_name ?? "the approval service"}`;
  if (a.status === "REJECTED" || a.status === "CANCELLED") {
    const by = a.decided_by_name !== null ? `By ${a.decided_by_name}. ` : "";
    return `${by}${a.decision_comment}`.trim() || null;
  }
  if (a.status === "FAILED") return a.failure_reason !== "" ? a.failure_reason : null;
  return null;
}

/**
 * Adjustments (inventory Phase 3): signed changes to stock with a reason. Small ones apply at once;
 * ones at or over the organization's threshold wait for approval and can be withdrawn here while
 * they wait. "Request adjustment" and "Withdraw" are gated on `operations.inventory.stock.adjust`
 * — presentation only; the server refuses the call regardless.
 */
export function AdjustmentsPage() {
  const canAdjust = useCan("operations.inventory.stock.adjust", "ORG");
  const toast = useToast();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [requesting, setRequesting] = useState(false);
  const [withdrawing, setWithdrawing] = useState<AdjustmentOut | null>(null);

  const list = useCursorList(
    (cursor, signal) =>
      listAdjustments({ status: status === "all" ? undefined : status, cursor, limit: 25, signal }),
    [status],
  );

  const withdraw = useMutation(
    (input: { adjustment: AdjustmentOut; reason: string }) =>
      cancelAdjustment(input.adjustment.id, input.reason === "" ? {} : { reason: input.reason }),
    {
      onSuccess: (saved) => {
        setWithdrawing(null);
        toast.success(`${saved.number} withdrawn.`);
        list.reload();
      },
    },
  );

  const columns: Column<AdjustmentOut>[] = [
    {
      key: "number",
      header: "Adjustment",
      mobile: "title",
      render: (a) => <span className="font-semibold text-ink tnum">{a.number}</span>,
    },
    {
      key: "item",
      header: "Item",
      render: (a) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{a.sku}</span>
          <span className="text-xs text-muted">
            {a.item_name} · {a.location_code}
          </span>
        </div>
      ),
    },
    {
      key: "change",
      header: "Change",
      align: "right",
      render: (a) => (
        <span className="tnum font-semibold text-ink">
          {a.quantity_delta.startsWith("-") ? "" : "+"}
          {formatQty(a.quantity_delta)} {a.uom}
        </span>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (a) => <span className="text-ink-soft">{REASON_LABEL.get(a.reason) ?? a.reason}</span>,
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (a) => {
        const note = outcomeNote(a);
        return (
          <div className="flex flex-col items-start gap-1">
            <AdjustmentStatusPill status={a.status} />
            {note !== null ? <span className="text-xs text-muted">{note}</span> : null}
          </div>
        );
      },
    },
    {
      key: "date",
      header: "Date",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (a) => <span className="text-muted">{formatDate(a.occurred_on)}</span>,
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      mobile: "actions",
      render: (a) => (
        <RowActions
          label={`Adjustment ${a.number}`}
          actions={[
            {
              label: "Withdraw",
              tone: "danger",
              onSelect: () => setWithdrawing(a),
              hidden: !canAdjust || a.status !== "PENDING_APPROVAL",
            },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Adjustments"
        description="Changes to stock with a reason. Large ones wait for approval before the stock moves."
        actions={
          <Can permission="operations.inventory.stock.adjust" scope="ORG">
            <Button onClick={() => setRequesting(true)}>Request adjustment</Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        <DataTable
          columns={columns}
          rows={list.items}
          rowKey={(a) => a.id}
          caption="Adjustments"
          mobileCard="auto"
          loading={list.status === "loading"}
          loadingRows={6}
          errorState={
            list.status === "error" ? (
              <ErrorState title="Couldn't load adjustments" message={problemMessage(list.error)} onRetry={list.reload} />
            ) : undefined
          }
          emptyState={
            <EmptyState
              icon={<ClipboardCheckIcon className="size-6" />}
              title={status !== "all" ? "No adjustments with this status" : "No adjustments yet"}
              description={
                status !== "all"
                  ? "Try a different status."
                  : "Request one to correct stock that is damaged, lost or found."
              }
            />
          }
        />
        {list.status === "ready" ? (
          <LoadMoreFooter
            loaded={list.items.length}
            hasMore={list.hasMore}
            loading={list.loadingMore}
            error={list.moreError}
            onLoadMore={list.loadMore}
            noun="adjustments"
          />
        ) : null}
      </Card>

      {withdraw.error && withdrawing === null ? (
        <InlineAlert tone="danger" title={problemMessage(withdraw.error)} />
      ) : null}

      {requesting ? (
        <Can permission="operations.inventory.stock.adjust" scope="ORG">
          <AdjustmentRequestDialog
            onClose={() => setRequesting(false)}
            onDone={() => {
              setRequesting(false);
              list.reload();
            }}
          />
        </Can>
      ) : null}

      {withdrawing ? (
        <ReasonDialog
          title={`Withdraw ${withdrawing.number}?`}
          description="The adjustment is cancelled and the stock stays as it is. This can't be undone."
          label="Reason (optional)"
          submitLabel="Withdraw adjustment"
          pendingLabel="Withdrawing…"
          pending={withdraw.pending}
          error={withdraw.error}
          fieldError={withdraw.fieldErrors.reason}
          onSubmit={(reason) => void withdraw.mutate({ adjustment: withdrawing, reason })}
          onClose={() => {
            setWithdrawing(null);
            withdraw.reset();
          }}
        />
      ) : null}
    </div>
  );
}
