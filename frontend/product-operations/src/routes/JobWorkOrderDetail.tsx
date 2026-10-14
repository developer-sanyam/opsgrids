import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  approveJobWorkOrder,
  cancelJobWorkOrder,
  closeJobWorkOrder,
  getJobWorkOrder,
} from "@/api/client";
import type { ChallanOut, JobWorkMaterialOut, JobWorkOrderDetailOut, JobWorkReceiptOut } from "@/api/types";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { ChallanEwayDialog } from "@/forms/ChallanEwayDialog";
import { JobWorkIssueDialog } from "@/forms/JobWorkIssueDialog";
import { JobWorkReceiveDialog } from "@/forms/JobWorkReceiveDialog";
import { formatDate, formatDateTime, formatMoneyPrecise } from "@/lib/format";
import { closeBlockReason, orderActions, reconciliationFoots } from "@/lib/jobwork";
import { formatQty } from "@/lib/quantity";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  EwayStatusPill,
  InlineAlert,
  JobWorkStatusPill,
  OverduePill,
  PageHeader,
  ReconciliationPill,
  Skeleton,
  TextArea,
} from "@/ui";
import type { Column } from "@/ui";
import { ArrowRightIcon, ReceiptIcon, useToast } from "@opsgrids/ui";

function BackLink() {
  return (
    <Link
      to="/jobwork/orders"
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to job-work orders
    </Link>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: "danger" }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className={`text-2xl font-semibold tnum ${tone === "danger" ? "text-danger" : "text-ink"}`}>{value}</p>
    </Card>
  );
}

/**
 * One job-work order (Inventory plan Phase 6): what was sent, what has come back (good, rejected,
 * short), the reconciliation that must balance before the order can close, the delivery challan,
 * and the actions the order's own status and the caller's permissions allow — issue, receive,
 * reconcile, close. Every quantity is the decimal string the server sent; none is parsed to a
 * float. An action that is not allowed is not rendered; the server refuses it regardless.
 */
export function JobWorkOrderDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const canWrite = useCan("operations.jobwork.order.write", "ORG");
  const canIssue = useCan("operations.jobwork.order.issue", "ORG");
  const canReceive = useCan("operations.jobwork.order.receive", "ORG");
  const canApprove = useCan("operations.jobwork.order.approve", "ORG");
  const keyFor = useSubmissionKeys();

  const [issuing, setIssuing] = useState(false);
  const [receiving, setReceiving] = useState(false);
  const [closing, setClosing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const [ewayFor, setEwayFor] = useState<ChallanOut | null>(null);

  const state = useAsync(() => getJobWorkOrder(id), [id]);

  const approve = useMutation(() => approveJobWorkOrder(id, keyFor({ action: "approve", id })), {
    onSuccess: () => {
      state.reload();
      toast.success("Order approved.");
    },
  });
  const close = useMutation(() => closeJobWorkOrder(id, keyFor({ action: "close", id })), {
    onSuccess: () => {
      setClosing(false);
      state.reload();
      toast.success("Order closed.");
    },
  });
  const cancel = useMutation(
    (why: string) => cancelJobWorkOrder(id, why, keyFor({ action: "cancel", id, why })),
    {
      onSuccess: () => {
        setCancelling(false);
        setReason("");
        state.reload();
      },
    },
  );

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" aria-busy="true" aria-label="Loading job-work order">
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
        <ErrorState title="Couldn't load this order" message={problemMessage(state.error)} onRetry={state.reload} />
      </div>
    );
  }

  const order: JobWorkOrderDetailOut = state.data;
  const actions = orderActions(order.status);
  const rec = order.reconciliation;
  const blocked = closeBlockReason(rec);

  const materialColumns: Column<JobWorkMaterialOut>[] = [
    {
      key: "material",
      header: "Material",
      render: (m) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{m.sku}</span>
          <span className="text-xs text-muted">{m.uom}</span>
        </div>
      ),
    },
    { key: "sent", header: "Sent", align: "right", render: (m) => <span className="tnum">{formatQty(m.sent)}</span> },
    {
      key: "received",
      header: "Received",
      align: "right",
      render: (m) => <span className="tnum">{formatQty(m.received)}</span>,
    },
    {
      key: "rejected",
      header: "Rejected",
      align: "right",
      render: (m) => <span className="tnum">{formatQty(m.rejected)}</span>,
    },
    {
      key: "shortage",
      header: "Shortage",
      align: "right",
      render: (m) => <span className="tnum">{formatQty(m.shortage)}</span>,
    },
    {
      key: "outstanding",
      header: "At vendor",
      align: "right",
      render: (m) => <span className="tnum font-semibold text-ink">{formatQty(m.outstanding)}</span>,
    },
  ];

  const receiptColumns: Column<JobWorkReceiptOut>[] = [
    {
      key: "receipt",
      header: "Receipt",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{r.receipt_no}</span>
          <span className="text-xs text-muted">{formatDate(r.received_on)}</span>
        </div>
      ),
    },
    { key: "sku", header: "Material", render: (r) => r.sku },
    {
      key: "received",
      header: "Good",
      align: "right",
      render: (r) => (
        <span className="tnum">
          {formatQty(r.received)}
          {r.qc_handoff && r.received !== "0" ? " · in QC" : ""}
        </span>
      ),
    },
    { key: "rejected", header: "Rejected", align: "right", render: (r) => <span className="tnum">{formatQty(r.rejected)}</span> },
    { key: "shortage", header: "Shortage", align: "right", render: (r) => <span className="tnum">{formatQty(r.shortage)}</span> },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow="Job work"
        title={order.number}
        description={`${order.process || "Job work"} · ${order.supplier_name || "vendor"}${
          order.due_date ? ` · due back ${formatDate(order.due_date)}` : ""
        }`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <JobWorkStatusPill status={order.status} />
            {order.is_overdue ? <OverduePill days={order.days_overdue} /> : null}
            {actions.approve && canApprove ? (
              <Button loading={approve.pending} onClick={() => approve.mutate(undefined)}>
                Approve
              </Button>
            ) : null}
            {actions.issue && canIssue ? <Button onClick={() => setIssuing(true)}>Issue to vendor</Button> : null}
            {actions.receive && canReceive ? (
              <Button onClick={() => setReceiving(true)}>Receive material</Button>
            ) : null}
            {actions.close && canApprove ? (
              <Button variant="secondary" onClick={() => setClosing(true)}>
                Close order
              </Button>
            ) : null}
            {actions.cancel && canWrite ? (
              <Button variant="destructive" onClick={() => setCancelling(true)}>
                Cancel order
              </Button>
            ) : null}
          </div>
        }
      />

      {approve.error ? <InlineAlert tone="danger" title={problemMessage(approve.error)} /> : null}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Figure label="Sent" value={formatQty(rec.total_sent)} />
        <Figure label="Received good" value={formatQty(rec.total_received)} />
        <Figure label="Rejected" value={formatQty(rec.total_rejected)} />
        <Figure label="Shortage" value={formatQty(rec.total_shortage)} />
      </div>

      <Card className="p-2 sm:p-4">
        <h2 className="px-3 pt-2 text-base font-semibold text-ink">Materials</h2>
        <DataTable
          columns={materialColumns}
          rows={order.materials}
          rowKey={(m) => m.id}
          caption="Materials on this order"
          emptyState={<EmptyState title="No materials" description="This order has no material lines." />}
        />
      </Card>

      <Card className="p-4 sm:p-6 flex flex-col gap-3" aria-labelledby="reconciliation-heading">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="reconciliation-heading" className="text-base font-semibold text-ink">
            Reconciliation
          </h2>
          <ReconciliationPill issued={rec.issued} balanced={rec.balanced} />
        </div>
        <p className="text-sm text-muted tnum">
          {formatQty(rec.total_sent)} sent = {formatQty(rec.total_received)} received +{" "}
          {formatQty(rec.total_rejected)} rejected + {formatQty(rec.total_shortage)} shortage +{" "}
          {formatQty(rec.total_outstanding)} still at the vendor
        </p>
        {!reconciliationFoots(rec) ? (
          <InlineAlert tone="danger" title="These figures do not add up. Do not rely on this screen; reload and tell an administrator." />
        ) : null}
        {rec.issued && blocked ? <InlineAlert tone="warning" title={`Can't close yet: ${blocked}`} /> : null}
        {rec.drift.length > 0 ? (
          <ul className="flex flex-col gap-1 text-sm" aria-label="Documents and ledger disagree">
            {rec.drift.map((d) => (
              <li key={d.item_id} className="text-danger tnum">
                {d.sku || d.item_id}: documents say {formatQty(d.documents)} at the vendor, the ledger says{" "}
                {formatQty(d.ledger)}.
              </li>
            ))}
          </ul>
        ) : null}
        {rec.issued && !blocked ? (
          <p className="text-sm text-muted">Every unit is accounted for and the ledger agrees with the documents.</p>
        ) : null}
      </Card>

      <Card className="p-2 sm:p-4">
        <h2 className="px-3 pt-2 text-base font-semibold text-ink">Receipts</h2>
        <DataTable
          columns={receiptColumns}
          rows={order.receipts}
          rowKey={(r) => r.id}
          caption="Receipts from the vendor"
          emptyState={
            <EmptyState
              icon={<ReceiptIcon className="size-6" />}
              title="Nothing received yet"
              description={
                order.status === "ISSUED" || order.status === "PARTIALLY_RECEIVED"
                  ? "Record a receipt as the vendor returns material."
                  : "Receipts appear here once material is back."
              }
            />
          }
        />
      </Card>

      <Card className="p-4 sm:p-6 flex flex-col gap-3" aria-labelledby="challans-heading">
        <h2 id="challans-heading" className="text-base font-semibold text-ink">
          Delivery challans
        </h2>
        {order.challans.length === 0 ? (
          <p className="text-sm text-muted">A challan is cut when the material is issued.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {order.challans.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 rounded-tile border border-line p-3">
                <div className="flex flex-col leading-tight">
                  <span className="font-semibold text-ink">{c.challan_number}</span>
                  <span className="text-xs text-muted">
                    {formatDateTime(c.issued_at)} · {formatMoneyPrecise(c.total_value_minor, c.currency)}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <EwayStatusPill status={c.eway_status} />
                  {c.eway_bill_no ? <span className="tnum text-sm text-muted">No. {c.eway_bill_no}</span> : null}
                  {canIssue && c.eway_status !== "GENERATED" ? (
                    <Button variant="secondary" size="sm" onClick={() => setEwayFor(c)}>
                      {c.eway_status === "NOT_REQUIRED" ? "Request e-way bill" : "Update e-way request"}
                    </Button>
                  ) : null}
                  <Link
                    to={`/jobwork/challans/${c.id}/print`}
                    className="text-sm font-semibold text-ink underline underline-offset-2"
                  >
                    Print challan
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {order.notes ? (
        <Card className="p-4 sm:p-6">
          <h2 className="text-base font-semibold text-ink">Notes</h2>
          <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{order.notes}</p>
        </Card>
      ) : null}

      {issuing && canIssue ? (
        <JobWorkIssueDialog
          order={order}
          onClose={() => setIssuing(false)}
          onIssued={() => {
            setIssuing(false);
            state.reload();
            toast.success("Material issued and challan cut.");
          }}
        />
      ) : null}
      {receiving && canReceive ? (
        <JobWorkReceiveDialog
          order={order}
          onClose={() => setReceiving(false)}
          onReceived={() => {
            setReceiving(false);
            state.reload();
            toast.success("Receipt recorded.");
          }}
        />
      ) : null}
      {ewayFor && canIssue ? (
        <ChallanEwayDialog
          challan={ewayFor}
          onClose={() => setEwayFor(null)}
          onRequested={() => {
            setEwayFor(null);
            state.reload();
            toast.success("E-way bill requested from finance.");
          }}
        />
      ) : null}

      <ConfirmDialog
        open={closing}
        onClose={() => {
          setClosing(false);
          close.reset();
        }}
        onConfirm={() => close.mutate(undefined)}
        title="Close this order?"
        body={
          blocked
            ? `The server will refuse this: ${blocked}`
            : "Every unit is accounted for. Closing settles the order and can't be undone."
        }
        confirmLabel="Close order"
        pendingLabel="Closing…"
        pending={close.pending}
        {...(close.error ? { error: problemMessage(close.error) } : {})}
      />

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          setReason("");
          cancel.reset();
        }}
        onConfirm={() => cancel.mutate(reason.trim())}
        title="Cancel this order?"
        body="Nothing has left stock. A cancelled order can't be reopened."
        confirmLabel="Cancel order"
        pendingLabel="Cancelling…"
        tone="danger"
        pending={cancel.pending}
        {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
      >
        <TextArea label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
      </ConfirmDialog>
    </div>
  );
}
