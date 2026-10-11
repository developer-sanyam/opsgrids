import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  approveTransfer,
  cancelTransfer,
  getTransfer,
  rejectTransfer,
  requestTransfer,
} from "@/api/movements";
import type { TransferOut, TransferStatus } from "@/api/movementTypes";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { hasTransferAction, transferActions } from "@/lib/transferActions";
import { DispatchTransferDialog } from "@/forms/DispatchTransferDialog";
import { ReasonDialog } from "@/forms/ReasonDialog";
import { ReceiveTransferDialog } from "@/forms/ReceiveTransferDialog";
import {
  ApprovalRouteChip,
  Button,
  Card,
  ErrorState,
  InlineAlert,
  SectionHeader,
  Skeleton,
  TransferStatusPill,
} from "@/ui";
import { ArrowRightIcon, Timeline, useToast } from "@opsgrids/ui";
import type { TimelineEntry, TimelineTone } from "@opsgrids/ui";

const STATUS_WORDS: Record<TransferStatus, string> = {
  DRAFT: "Draft",
  REQUESTED: "Requested",
  APPROVED: "Approved",
  IN_TRANSIT: "In transit",
  RECEIVED: "Received",
  CANCELLED: "Cancelled",
  REJECTED: "Rejected",
};

const STATUS_TONE: Record<TransferStatus, TimelineTone> = {
  DRAFT: "neutral",
  REQUESTED: "late",
  APPROVED: "positive",
  IN_TRANSIT: "info",
  RECEIVED: "positive",
  CANCELLED: "negative",
  REJECTED: "negative",
};

function BackLink() {
  return (
    <Link
      to="/inventory/transfers"
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to transfers
    </Link>
  );
}

function timelineOf(transfer: TransferOut): TimelineEntry[] {
  return transfer.transitions.map((t, index) => ({
    id: `${index}-${t.to_status}`,
    content:
      t.from_status === null
        ? `${t.actor} drafted the transfer`
        : `${t.actor} moved it from ${STATUS_WORDS[t.from_status]} to ${STATUS_WORDS[t.to_status]}`,
    tone: STATUS_TONE[t.to_status],
    timestamp: formatDateTime(t.created_at),
    dateTime: t.created_at,
    ...(t.note !== "" ? { quote: t.note } : {}),
  }));
}

const HEAD = "py-3 px-3 text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap";

/**
 * One transfer: where it stands, its lines with quantity / sent / received / short / still in
 * transit, its history, and only the actions its status allows *and* the viewer holds the
 * permission for (`lib/transferActions.ts`). A hidden button is presentation; every action is also
 * refused by the server, and its refusal (the requester approving their own, say) is shown as sent.
 */
export function TransferDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const canWrite = useCan("operations.inventory.transfer.write", "ORG");
  const canApprove = useCan("operations.inventory.transfer.approve", "ORG");
  const canDispatch = useCan("operations.inventory.transfer.dispatch", "ORG");
  const canReceive = useCan("operations.inventory.transfer.receive", "ORG");

  const state = useAsync(() => getTransfer(id), [id]);
  const [current, setCurrent] = useState<TransferOut | null>(null);
  // Keyed on the loaded data itself: `state` is a new object every render, and re-seeding from it
  // would throw away the transfer a successful action just handed back.
  const loaded = state.status === "success" ? state.data : null;
  useEffect(() => {
    if (loaded !== null) setCurrent(loaded);
  }, [loaded]);
  const transfer = current !== null && current.id === id ? current : null;

  const [rejecting, setRejecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [receiving, setReceiving] = useState(false);

  function landed(saved: TransferOut) {
    setCurrent(saved);
  }

  const request = useMutation(() => requestTransfer(id), {
    onSuccess: (saved) => {
      landed(saved);
      toast.success(
        saved.approval_route === "AUTO"
          ? `${saved.number} was under the approval threshold and is approved.`
          : saved.approval_route === "SERVICE"
            ? `${saved.number} sent to the approval service.`
            : `${saved.number} is waiting for someone to approve it.`,
      );
    },
  });
  const approve = useMutation(() => approveTransfer(id, {}), {
    onSuccess: (saved) => {
      landed(saved);
      toast.success(`${saved.number} approved.`);
    },
  });
  const reject = useMutation((comment: string) => rejectTransfer(id, { comment }), {
    onSuccess: (saved) => {
      setRejecting(false);
      landed(saved);
      toast.success(`${saved.number} rejected.`);
    },
  });
  const cancel = useMutation((reason: string) => cancelTransfer(id, { reason }), {
    onSuccess: (saved) => {
      setCancelling(false);
      landed(saved);
      toast.success(`${saved.number} cancelled.`);
    },
  });

  if (transfer === null) {
    if (state.status === "error") {
      return (
        <div className="rise flex flex-col gap-6">
          <BackLink />
          <Card className="p-6">
            <ErrorState
              title="Couldn't load this transfer"
              message={problemMessage(state.error)}
              onRetry={state.reload}
            />
          </Card>
        </div>
      );
    }
    return (
      <div className="rise flex flex-col gap-6" aria-busy="true">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
      </div>
    );
  }

  const actions = transferActions(transfer.status, transfer.approval_route, {
    write: canWrite,
    approve: canApprove,
    dispatch: canDispatch,
    receive: canReceive,
  });
  const approver = transfer.approver_name ?? "the approval service";
  const failures = [request.error, approve.error].filter((e): e is Error => e !== null);

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <span className="text-sm font-medium text-muted">Transfer</span>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="display text-3xl font-extrabold leading-tight tnum">{transfer.number}</h1>
            <TransferStatusPill status={transfer.status} />
            <ApprovalRouteChip route={transfer.approval_route} />
          </div>
          <p className="text-sm text-muted">
            {transfer.from_warehouse.name} <span className="sr-only">to</span>
            <span aria-hidden="true">→</span> {transfer.to_warehouse.name}
          </p>
        </div>

        {hasTransferAction(actions) ? (
          <div className="flex flex-wrap items-center gap-2">
            {actions.request ? (
              <Button loading={request.pending} onClick={() => void request.mutate(undefined)}>
                Request approval
              </Button>
            ) : null}
            {actions.edit ? (
              <Button variant="secondary" onClick={() => navigate(`/inventory/transfers/${transfer.id}/edit`)}>
                Edit
              </Button>
            ) : null}
            {actions.approve ? (
              <Button loading={approve.pending} onClick={() => void approve.mutate(undefined)}>
                Approve
              </Button>
            ) : null}
            {actions.reject ? (
              <Button variant="secondary" onClick={() => setRejecting(true)}>
                Reject
              </Button>
            ) : null}
            {actions.dispatch ? <Button onClick={() => setDispatching(true)}>Dispatch</Button> : null}
            {actions.receive ? <Button onClick={() => setReceiving(true)}>Receive</Button> : null}
            {actions.cancel ? (
              <Button variant="secondary" onClick={() => setCancelling(true)}>
                Cancel transfer
              </Button>
            ) : null}
          </div>
        ) : null}
      </header>

      {transfer.awaiting_approval ? (
        <InlineAlert tone="warning" title={`Waiting for approval from ${approver}`}>
          Nothing moves until it is approved. You can still cancel the transfer while it waits.
        </InlineAlert>
      ) : null}
      {transfer.status === "REQUESTED" && !transfer.awaiting_approval && transfer.approval_route === "DIRECT" ? (
        <InlineAlert tone="warning" title="Waiting for a direct approval">
          Someone with approval rights — not the person who requested it — has to approve or reject this transfer.
        </InlineAlert>
      ) : null}
      {transfer.status === "REJECTED" ? (
        <InlineAlert tone="danger" title="This transfer was rejected">
          {transfer.decision_comment !== "" ? `Reason: ${transfer.decision_comment}` : "No reason was recorded."}
        </InlineAlert>
      ) : null}
      {transfer.status === "CANCELLED" ? (
        <InlineAlert tone="info" title="This transfer was cancelled">
          {transfer.cancel_reason !== "" ? `Reason: ${transfer.cancel_reason}` : "No reason was recorded."}
        </InlineAlert>
      ) : null}
      {failures.map((error, index) => (
        <InlineAlert key={index} tone="danger" title={problemMessage(error)} />
      ))}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 items-start">
        <Card className="p-2 sm:p-4 lg:col-span-2">
          <div className="px-3 pt-2 pb-3">
            <SectionHeader title="Lines" subtitle={`${transfer.lines.length} ${transfer.lines.length === 1 ? "line" : "lines"}`} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Lines of {transfer.number}</caption>
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className={`${HEAD} text-left`}>
                    Item
                  </th>
                  <th scope="col" className={`${HEAD} text-right`}>
                    Quantity
                  </th>
                  <th scope="col" className={`${HEAD} text-right`}>
                    Dispatched
                  </th>
                  <th scope="col" className={`${HEAD} text-right`}>
                    Received
                  </th>
                  <th scope="col" className={`${HEAD} text-right`}>
                    Short
                  </th>
                  <th scope="col" className={`${HEAD} text-right`}>
                    Still in transit
                  </th>
                </tr>
              </thead>
              <tbody>
                {transfer.lines.map((line) => (
                  <tr key={line.id} className="border-b border-line/70 last:border-0">
                    <td className="py-3 px-3">
                      <div className="flex flex-col leading-tight">
                        <span className="font-semibold text-ink">{line.sku}</span>
                        <span className="text-xs text-muted">
                          {line.item_name} · from {line.from_location_code}
                        </span>
                      </div>
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.quantity)} {line.uom}
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.dispatched)}
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.received)}
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.shortage)}
                    </td>
                    <td className="py-3 px-3 text-right tnum font-semibold text-ink whitespace-nowrap">
                      {formatQty(line.in_transit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="px-3 pt-3 text-xs text-muted">
            Stock still in transit is owned but not yet at the destination, so it is not part of that
            warehouse's on-hand.
          </p>
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Details" />
            <dl className="flex flex-col gap-2 text-sm">
              <Detail label="From" value={transfer.from_warehouse.name} />
              <Detail label="To" value={transfer.to_warehouse.name} />
              <Detail label="Expected" value={formatDate(transfer.expected_on)} />
              <Detail label="Requested by" value={transfer.requested_by ?? "—"} />
              <Detail label="Approved by" value={transfer.approved_by ?? "—"} />
              <Detail label="Dispatched" value={transfer.dispatched_at ? formatDateTime(transfer.dispatched_at) : "—"} />
              <Detail label="Completed" value={transfer.completed_at ? formatDateTime(transfer.completed_at) : "—"} />
              <Detail label="Created" value={formatDateTime(transfer.created_at)} />
            </dl>
            {transfer.note !== "" ? <p className="text-sm text-ink-soft">{transfer.note}</p> : null}
          </Card>

          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="History" />
            <Timeline entries={timelineOf(transfer)} />
          </Card>
        </div>
      </div>

      {rejecting ? (
        <ReasonDialog
          title={`Reject ${transfer.number}?`}
          description="The requester is told why. A rejected transfer can't be reopened."
          label="Reason for rejecting"
          submitLabel="Reject transfer"
          pendingLabel="Rejecting…"
          requiredMessage="Say why this transfer is being rejected."
          pending={reject.pending}
          error={reject.error}
          fieldError={reject.fieldErrors.comment}
          onSubmit={(reason) => void reject.mutate(reason)}
          onClose={() => {
            setRejecting(false);
            reject.reset();
          }}
        />
      ) : null}

      {cancelling ? (
        <ReasonDialog
          title={`Cancel ${transfer.number}?`}
          description="No stock has left the source warehouse. A cancelled transfer can't be reopened."
          label="Reason for cancelling"
          submitLabel="Cancel transfer"
          pendingLabel="Cancelling…"
          requiredMessage="Say why this transfer is being cancelled."
          pending={cancel.pending}
          error={cancel.error}
          fieldError={cancel.fieldErrors.reason}
          onSubmit={(reason) => void cancel.mutate(reason)}
          onClose={() => {
            setCancelling(false);
            cancel.reset();
          }}
        />
      ) : null}

      {dispatching ? (
        <DispatchTransferDialog
          transfer={transfer}
          onClose={() => setDispatching(false)}
          onDispatched={(saved) => {
            setDispatching(false);
            landed(saved);
            toast.success(`${saved.number} dispatched — the stock is now in transit.`);
          }}
        />
      ) : null}

      {receiving ? (
        <ReceiveTransferDialog
          transfer={transfer}
          onClose={() => setReceiving(false)}
          onReceived={(saved) => {
            setReceiving(false);
            landed(saved);
            toast.success(
              saved.status === "RECEIVED"
                ? `${saved.number} is fully received.`
                : `Receipt recorded. ${saved.number} still has stock in transit.`,
            );
          }}
        />
      ) : null}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-semibold text-ink">{value}</dd>
    </div>
  );
}
