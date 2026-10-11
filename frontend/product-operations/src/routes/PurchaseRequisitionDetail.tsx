import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  approveRequisition,
  cancelRequisition,
  getRequisition,
  rejectRequisition,
  submitRequisition,
} from "@/api/client";
import type { RequisitionOut } from "@/api/types";
import { ConvertRequisitionDialog } from "@/forms/ConvertRequisitionDialog";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate, formatDateTime, formatMoneyPrecise } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { requisitionActions } from "@/lib/requisitionActions";
import {
  Button,
  Card,
  ConfirmDialog,
  FormDialog,
  InlineAlert,
  LoadError,
  RequisitionStatusPill,
  SectionHeader,
  Skeleton,
  TextArea,
} from "@/ui";
import { ArrowRightIcon, Timeline, useToast } from "@opsgrids/ui";
import type { TimelineEntry } from "@opsgrids/ui";

function BackLink() {
  return (
    <Link
      to="/purchase-requisitions"
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to requisitions
    </Link>
  );
}

/** What happened to this requisition, oldest first, from the timestamps and the decision it carries. */
function historyOf(req: RequisitionOut): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    {
      id: "created",
      content: `Raised by ${req.requested_by || "someone"}`,
      tone: "neutral",
      timestamp: formatDateTime(req.created_at),
      dateTime: req.created_at,
    },
  ];
  if (req.submitted_at) {
    entries.push({
      id: "submitted",
      content: "Submitted for approval",
      tone: "info",
      timestamp: formatDateTime(req.submitted_at),
      dateTime: req.submitted_at,
    });
  }
  if (req.decided_at && (req.status === "APPROVED" || req.status === "CONVERTED")) {
    entries.push({
      id: "approved",
      content: "Approved",
      tone: "positive",
      timestamp: formatDateTime(req.decided_at),
      dateTime: req.decided_at,
      ...(req.decision_note ? { quote: req.decision_note } : {}),
    });
  }
  if (req.decided_at && req.status === "REJECTED") {
    entries.push({
      id: "rejected",
      content: "Rejected",
      tone: "negative",
      timestamp: formatDateTime(req.decided_at),
      dateTime: req.decided_at,
      ...(req.decision_note ? { quote: req.decision_note } : {}),
    });
  }
  if (req.status === "CANCELLED") {
    entries.push({
      id: "cancelled",
      content: "Cancelled",
      tone: "negative",
      timestamp: formatDateTime(req.updated_at),
      dateTime: req.updated_at,
      ...(req.decision_note ? { quote: req.decision_note } : {}),
    });
  }
  if (req.status === "CONVERTED") {
    entries.push({
      id: "converted",
      content: `Converted to purchase order ${req.converted_po_number ?? ""}`.trim(),
      tone: "positive",
      timestamp: formatDateTime(req.updated_at),
      dateTime: req.updated_at,
    });
  }
  return entries;
}

type Dialog = "approve" | "reject" | "cancel" | "convert" | null;

export function PurchaseRequisitionDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.procurement.requisition.write", "OWN");
  const canApprove = useCan("operations.procurement.requisition.approve", "ORG");
  const canWritePurchaseOrder = useCan("operations.procurement.purchase_order.write", "OWN");

  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);

  const state = useAsync(() => getRequisition(id), [id]);
  const req = state.status === "success" ? state.data : null;

  function closeDialog() {
    setDialog(null);
    setNote("");
    setReason("");
    setReasonError(undefined);
    approve.reset();
    reject.reset();
    cancel.reset();
  }

  const submit = useMutation(() => submitRequisition(id), {
    onSuccess: (saved) => {
      toast.success(`${saved.number} submitted for approval.`);
      state.reload();
    },
  });
  const approve = useMutation(() => approveRequisition(id, note.trim() ? { note: note.trim() } : {}), {
    onSuccess: (saved) => {
      closeDialog();
      toast.success(`${saved.number} approved.`);
      state.reload();
    },
  });
  const reject = useMutation(() => rejectRequisition(id, { reason: reason.trim() }), {
    onSuccess: (saved) => {
      closeDialog();
      toast.success(`${saved.number} rejected.`);
      state.reload();
    },
  });
  const cancel = useMutation(() => cancelRequisition(id, note.trim() ? { note: note.trim() } : {}), {
    onSuccess: (saved) => {
      closeDialog();
      toast.success(`${saved.number} cancelled.`);
      state.reload();
    },
  });

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" role="status" aria-label="Loading requisition">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (state.status === "error" || !req) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          {state.status === "error" ? (
            <LoadError error={state.error} noun="this requisition" onRetry={state.reload} />
          ) : (
            <p className="text-sm text-muted">That requisition could not be found.</p>
          )}
        </Card>
      </div>
    );
  }

  const actions = requisitionActions(req.status, { canWrite, canApprove, canWritePurchaseOrder });
  const estimated = req.lines.reduce((sum, l) => sum + (l.est_unit_price_minor ?? 0), 0) > 0;

  function submitReject() {
    setReasonError(undefined);
    if (reason.trim() === "") {
      setReasonError("Say why this requisition is being rejected.");
      return;
    }
    void reject.mutate(undefined);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-muted">Requested by {req.requested_by || "—"}</span>
          <div className="flex items-center gap-3">
            <h1 className="text-page-sm sm:text-page tnum">{req.number}</h1>
            <RequisitionStatusPill status={req.status} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actions.edit ? (
            <Button variant="outline" onClick={() => navigate(`/purchase-requisitions/${id}/edit`)}>
              Edit
            </Button>
          ) : null}
          {actions.cancel ? (
            <Button variant="outline" onClick={() => setDialog("cancel")}>
              Cancel requisition
            </Button>
          ) : null}
          {actions.reject ? (
            <Button variant="outline" onClick={() => setDialog("reject")}>
              Reject
            </Button>
          ) : null}
          {actions.approve ? <Button onClick={() => setDialog("approve")}>Approve</Button> : null}
          {actions.submit ? (
            <Button loading={submit.pending} onClick={() => void submit.mutate(undefined)}>
              Submit for approval
            </Button>
          ) : null}
          {actions.convert !== "hidden" ? (
            <div className="flex flex-col items-end gap-1">
              <Button disabled={actions.convert === "disabled"} onClick={() => setDialog("convert")}>
                Convert to purchase order
              </Button>
              {actions.convert === "disabled" ? (
                <span className="text-xs text-muted">Converting also needs permission to write purchase orders.</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </header>

      {submit.error ? <InlineAlert tone="danger" title={problemMessage(submit.error)} /> : null}

      {req.status === "CONVERTED" && req.converted_po_id ? (
        <InlineAlert tone="success" title={`Converted to ${req.converted_po_number ?? "a purchase order"}`}>
          <Link to={`/purchase-orders/${req.converted_po_id}`} className="font-semibold text-accent hover:text-accent-strong">
            Open the purchase order
          </Link>
        </InlineAlert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 items-start">
        <Card className="p-2 sm:p-4 lg:col-span-2">
          <div className="px-3 pt-2 pb-3">
            <SectionHeader title="Lines" subtitle={`${req.lines.length} line${req.lines.length === 1 ? "" : "s"}`} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Lines on {req.number}</caption>
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className="py-3 px-3 text-left text-xs font-semibold uppercase tracking-wide text-faint">
                    Item
                  </th>
                  <th scope="col" className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap">
                    Quantity
                  </th>
                  {estimated ? (
                    <th scope="col" className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap">
                      Est. unit price
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {req.lines.map((line) => (
                  <tr key={line.id} className="border-b border-line/70 last:border-0">
                    <td className="py-3 px-3">
                      <div className="flex flex-col leading-tight">
                        <span className="font-semibold text-ink">{line.item_name}</span>
                        <span className="text-xs text-faint tnum">{line.sku}</span>
                        {line.note ? <span className="text-xs text-muted">{line.note}</span> : null}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.quantity)} {line.uom}
                    </td>
                    {estimated ? (
                      <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                        {line.est_unit_price_minor === null ? "—" : formatMoneyPrecise(line.est_unit_price_minor)}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Request" />
            <dl className="flex flex-col gap-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Warehouse</dt>
                <dd className="font-semibold text-ink">{req.warehouse_name ?? "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Needed by</dt>
                <dd className="tnum font-semibold text-ink">{formatDate(req.needed_by)}</dd>
              </div>
            </dl>
            {req.reason ? <p className="text-sm text-ink-soft leading-relaxed">{req.reason}</p> : null}
          </Card>

          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="History" />
            <Timeline entries={historyOf(req)} />
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={dialog === "approve"}
        onClose={closeDialog}
        onConfirm={() => void approve.mutate(undefined)}
        title="Approve this requisition?"
        body={`${req.number} becomes ready to convert into a purchase order.`}
        confirmLabel="Approve"
        pendingLabel="Approving…"
        tone="primary"
        pending={approve.pending}
        {...(approve.error ? { error: problemMessage(approve.error) } : {})}
      >
        <TextArea
          label="Note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          optional
          maxLength={500}
        />
      </ConfirmDialog>

      <FormDialog
        open={dialog === "reject"}
        onClose={closeDialog}
        onSubmit={submitReject}
        title="Reject this requisition?"
        description={`${req.number} goes back to its requester with your reason.`}
        submitLabel="Reject requisition"
        pendingLabel="Rejecting…"
        pending={reject.pending}
        dirty={reason !== ""}
        {...(reject.error && Object.keys(reject.fieldErrors).length === 0 ? { error: problemMessage(reject.error) } : {})}
      >
        <TextArea
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          required
          maxLength={500}
          {...((reasonError ?? reject.fieldErrors.reason) !== undefined
            ? { error: reasonError ?? reject.fieldErrors.reason }
            : {})}
        />
      </FormDialog>

      <ConfirmDialog
        open={dialog === "cancel"}
        onClose={closeDialog}
        onConfirm={() => void cancel.mutate(undefined)}
        title="Cancel this requisition?"
        body={`${req.number} is closed and can't be converted into a purchase order. This can't be undone.`}
        confirmLabel="Cancel requisition"
        pendingLabel="Cancelling…"
        pending={cancel.pending}
        {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
      >
        <TextArea
          label="Note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          optional
          maxLength={500}
        />
      </ConfirmDialog>

      {dialog === "convert" ? (
        <ConvertRequisitionDialog
          requisition={req}
          onClose={closeDialog}
          onConverted={(po) => {
            toast.success(`${po.po_number} created as a draft.`);
            navigate(`/purchase-orders/${po.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
