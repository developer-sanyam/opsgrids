import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  approvePurchaseOrder,
  cancelPurchaseOrder,
  closePurchaseOrder,
  deletePurchaseOrder,
  getPurchaseOrder,
  listInventory,
  receivePurchaseOrder,
} from "@/api/client";
import type { PoLineItem, PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, isLate } from "@/data/derive";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { daysBetween, formatDate, formatMoney, formatMoneyPrecise, relativeDue } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { today } from "@/lib/now";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  PoStatusPill,
  SectionHeader,
  Skeleton,
  StickyActionBar,
  TextArea,
} from "@/ui";
import {
  ArrowRightIcon,
  BuildingIcon,
  CheckCircleIcon,
  ClockIcon,
  MapPinIcon,
  TruckIcon,
  TABLET_QUERY,
  useMediaQuery,
  useToast,
} from "@opsgrids/ui";
import { purchaseOrderActions } from "../lib/purchaseOrderActions";
import { PurchaseOrderReceipts } from "./PurchaseOrderReceipts";

function lineTotal(line: PoLineItem): number {
  return line.quantity * line.unit_price_minor;
}

/** The delivery banner: on-time / late for an open order, or the received date once fulfilled. */
function DeliveryStatus({ po }: { po: PurchaseOrderOut }) {
  if (po.status === "received" || po.status === "closed") {
    const when = po.received_date ?? today();
    return (
      <div className="flex items-center gap-3 rounded-tile bg-present-soft px-4 py-3 text-present">
        <CheckCircleIcon className="size-5 shrink-0" />
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-bold">Received</span>
          <span className="text-xs opacity-80 tnum">Delivered on {formatDate(when)}</span>
        </div>
      </div>
    );
  }
  if (po.status === "partially_received") {
    return (
      <div className="flex items-center gap-3 rounded-tile bg-info-soft px-4 py-3 text-info">
        <TruckIcon className="size-5 shrink-0" />
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-bold">Partially received</span>
          <span className="text-xs opacity-80 tnum">The rest is expected by {formatDate(po.expected_date)}</span>
        </div>
      </div>
    );
  }
  if (po.status === "cancelled") {
    return (
      <div className="flex items-center gap-3 rounded-tile bg-danger-soft px-4 py-3 text-danger">
        <TruckIcon className="size-5 shrink-0" />
        <span className="text-sm font-bold">Cancelled — nothing will be delivered</span>
      </div>
    );
  }
  const late = isLate(po, today());
  const overdue = Math.abs(daysBetween(po.expected_date, today()));
  return (
    <div
      className={
        "flex items-center gap-3 rounded-tile px-4 py-3 " +
        (late ? "bg-danger-soft text-danger" : "bg-info-soft text-info")
      }
    >
      {late ? <TruckIcon className="size-5 shrink-0" /> : <ClockIcon className="size-5 shrink-0" />}
      <div className="flex flex-col leading-tight">
        <span className="text-sm font-bold">
          {late ? `${overdue} day${overdue === 1 ? "" : "s"} overdue` : "In transit"}
        </span>
        <span className="text-xs opacity-80 tnum">
          {relativeDue(po.expected_date, today())} · expected {formatDate(po.expected_date)}
        </span>
      </div>
    </div>
  );
}

export function PurchaseOrderDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canApprove = useCan("operations.procurement.purchase_order.approve", "ORG");
  // Posting a receipt needs both of these on the server; the button is only presentation.
  const canWriteReceipt = useCan("operations.procurement.receipt.write", "ORG");
  const canReceiveStock = useCan("operations.inventory.stock.receive", "ORG");
  const canReceiveGoods = canWriteReceipt && canReceiveStock;
  const roomy = useMediaQuery(TABLET_QUERY, true);

  const [deleting, setDeleting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelReasonError, setCancelReasonError] = useState<string | undefined>(undefined);

  const loader = () => getPurchaseOrder(id);
  const state = useAsync(loader, [id]);
  const po = state.status === "success" ? state.data : null;

  // Every SKU the received (or closed) order's lines carry, checked against the org's live
  // inventory: a line with no matching item posted no stock when it was received (a
  // service/logistics line, not an error). The server says which at the moment of receiving
  // (the toast above); it does not keep saying so on later reads, so the standing note below
  // is rebuilt from what the client can see.
  const shouldCheckStock = po !== null && (po.status === "received" || po.status === "closed");
  const inventoryLoader = () =>
    shouldCheckStock ? listInventory({ size: 200 }) : Promise.resolve({ items: [], total: 0, page: 1, size: 0, pages: 0 });
  const inventoryState = useAsync(inventoryLoader, [shouldCheckStock, po?.id]);
  const unmatchedSkus = useMemo(() => {
    if (!po || inventoryState.status !== "success" || !shouldCheckStock) return [];
    const known = new Set(inventoryState.data.items.map((i) => i.sku));
    return po.line_items.filter((l) => l.received_qty > 0 && !known.has(l.sku)).map((l) => l.sku);
  }, [po, inventoryState, shouldCheckStock]);

  const approve = useMutation(() => approvePurchaseOrder(id), {
    onSuccess: (saved) => {
      toast.success(`${saved.po_number} approved.`);
      state.reload();
    },
  });
  const receive = useMutation(() => receivePurchaseOrder(id), {
    onSuccess: (saved) => {
      // The server's own answer, at the moment it matters: which lines added no stock.
      const skipped = saved.unmatched_skus;
      if (skipped.length === 0) {
        toast.success(`${saved.po_number} marked received. Stock has been posted for every line.`);
      } else {
        toast.info(`${saved.po_number} marked received.`, {
          description: `No stock was posted for ${skipped.join(", ")}: no inventory item has that SKU.`,
          duration: null,
        });
      }
      state.reload();
    },
  });
  const close = useMutation(() => closePurchaseOrder(id), {
    onSuccess: (saved) => {
      toast.success(`${saved.po_number} closed.`);
      state.reload();
    },
  });
  const cancel = useMutation(() => cancelPurchaseOrder(id, { reason: cancelReason.trim() }), {
    onSuccess: (saved) => {
      setCancelling(false);
      setCancelReason("");
      toast.success(`${saved.po_number} cancelled.`);
      state.reload();
    },
  });
  const remove = useMutation(() => deletePurchaseOrder(id), {
    onSuccess: () => {
      toast.success(`${po?.po_number ?? "Purchase order"} deleted.`);
      navigate("/purchase-orders");
    },
  });

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
      </div>
    );
  }

  if (state.status === "error" || !po) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <ErrorState
            message={
              state.status === "error"
                ? problemMessage(state.error)
                : "That purchase order could not be found."
            }
            {...(state.status === "error" ? { onRetry: state.reload } : {})}
          />
        </Card>
      </div>
    );
  }

  const total = po.line_items.reduce((s, l) => s + lineTotal(l), 0);
  const actions = purchaseOrderActions(po.status, canApprove);

  function submitCancel() {
    setCancelReasonError(undefined);
    if (cancelReason.trim().length < 1) {
      setCancelReasonError("Say why this order is being cancelled.");
      return;
    }
    void cancel.mutate(undefined);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-muted">
            {CATEGORY_LABEL[po.category]} · raised by {po.raised_by || "—"}
          </span>
          <div className="flex items-center gap-3">
            <h1 className="display text-3xl sm:text-[2.1rem] font-extrabold leading-tight tnum">
              {po.po_number}
            </h1>
            <PoStatusPill status={po.status} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actions.edit ? (
            <Can permission="operations.procurement.purchase_order.write" scope="OWN">
              <Button variant="outline" onClick={() => navigate(`/purchase-orders/${id}/edit`)}>
                Edit
              </Button>
              <Button variant="outline" onClick={() => setDeleting(true)}>
                Delete
              </Button>
            </Can>
          ) : null}
          {actions.approve ? (
            <Button
              variant="outline"
              loading={approve.pending}
              onClick={() => void approve.mutate(undefined)}
            >
              Approve
            </Button>
          ) : null}
          {/* The goods-receipt flow is the way to take a delivery: partial, short, tolerance-checked,
              with lots and rolls. Receiving everything outstanding in one click stays beside it. */}
          {actions.receiveGoods && canReceiveGoods && roomy ? (
            <Button leadingIcon={<TruckIcon className="size-4" />} onClick={() => navigate(`/purchase-orders/${id}/receive`)}>
              Receive goods
            </Button>
          ) : null}
          {/* Approval is a prerequisite for receiving: the service refuses to receive a draft, so
              the control only exists once someone with the approve permission has signed off. */}
          {actions.receive ? (
            <Can permission="operations.procurement.purchase_order.write" scope="OWN">
              <Button
                {...(actions.receiveGoods && canReceiveGoods ? { variant: "outline" as const } : {})}
                leadingIcon={<CheckCircleIcon className="size-4" />}
                loading={receive.pending}
                onClick={() => void receive.mutate(undefined)}
              >
                Mark as received
              </Button>
            </Can>
          ) : null}
          {/* Cancelling a draft undoes nothing. Cancelling an approved order reverses spend an
              approver committed, so the service asks for the approve permission as well. */}
          {actions.cancel ? (
            <Can permission="operations.procurement.purchase_order.write" scope="OWN">
              <Button variant="outline" onClick={() => setCancelling(true)}>
                Cancel order
              </Button>
            </Can>
          ) : null}
          {actions.close ? (
            <Can permission="operations.procurement.purchase_order.write" scope="OWN">
              <Button loading={close.pending} onClick={() => void close.mutate(undefined)}>
                Close order
              </Button>
            </Can>
          ) : null}
        </div>
      </header>

      {approve.error ? <InlineAlert tone="danger" title={problemMessage(approve.error)} /> : null}
      {receive.error ? <InlineAlert tone="danger" title={problemMessage(receive.error)} /> : null}
      {close.error ? <InlineAlert tone="danger" title={problemMessage(close.error)} /> : null}

      {(po.status === "received" || po.status === "closed") && unmatchedSkus.length > 0 ? (
        <InlineAlert tone="warning" title="Some lines posted no stock">
          {unmatchedSkus.join(", ")} — no inventory item matches this SKU, so it was treated as a
          service or logistics line.
        </InlineAlert>
      ) : null}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 items-start">
        {/* Line items */}
        <div className="flex flex-col gap-4 lg:col-span-2">
        <Card className="p-2 sm:p-4">
          <div className="px-3 pt-2 pb-3">
            <SectionHeader title="Line Items" subtitle={`${po.line_items.length} lines`} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Line items for {po.po_number}</caption>
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className="py-3 px-3 text-left text-xs font-semibold uppercase tracking-wide text-faint">
                    Item
                  </th>
                  <th scope="col" className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap">
                    Qty
                  </th>
                  <th scope="col" className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap">
                    Line total
                  </th>
                </tr>
              </thead>
              <tbody>
                {po.line_items.map((line) => (
                  <tr key={line.id} className="border-b border-line/70 last:border-0">
                    <td className="py-3.5 px-3">
                      <div className="flex flex-col leading-tight">
                        <span className="font-semibold text-ink">{line.description || line.sku}</span>
                        <span className="text-xs text-faint tnum">
                          {line.sku} · {formatMoneyPrecise(line.unit_price_minor, po.currency)} each
                        </span>
                      </div>
                    </td>
                    <td className="py-3.5 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {line.quantity} {line.uom}
                      {po.status === "partially_received" || line.short_closed ? (
                        <span className="block text-xs font-normal text-muted">
                          {formatQty(line.received_exact)} received{line.short_closed ? ", closed short" : ""}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-3.5 px-3 text-right tnum font-semibold text-ink whitespace-nowrap">
                      {formatMoney(lineTotal(line), po.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-line">
                  <td className="py-4 px-3 text-sm font-bold text-ink" colSpan={2}>
                    Total
                  </td>
                  <td className="py-4 px-3 text-right">
                    <span className="display text-xl font-extrabold tnum">{formatMoney(total, po.currency)}</span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>

        {po.status === "draft" || po.status === "cancelled" ? null : (
          <PurchaseOrderReceipts
            purchaseOrderId={po.id}
            reloadKey={`${po.status}:${po.line_items.map((l) => l.received_exact).join(",")}`}
          />
        )}
        </div>

        {/* Sidebar: delivery + supplier + meta */}
        <div className="flex flex-col gap-5">
          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Delivery" />
            <DeliveryStatus po={po} />
            <dl className="flex flex-col gap-2.5 text-sm">
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Warehouse</dt>
                <dd className="inline-flex items-center gap-1.5 font-semibold text-ink">
                  <MapPinIcon className="size-4 text-faint" />
                  {po.warehouse}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Order date</dt>
                <dd className="tnum font-semibold text-ink">{formatDate(po.created_date)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Expected</dt>
                <dd className="tnum font-semibold text-ink">{formatDate(po.expected_date)}</dd>
              </div>
            </dl>
          </Card>

          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Supplier" />
            <Link
              to={`/suppliers/${po.supplier_id}`}
              className="group flex items-center gap-3 rounded-tile hover:bg-ground -mx-2 px-2 py-2 transition-colors"
            >
              <Avatar name={po.supplier_name} size="md" />
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="truncate font-bold text-ink">{po.supplier_name}</span>
                <span className="truncate text-xs text-muted inline-flex items-center gap-1">
                  <BuildingIcon className="size-3.5" />
                  {CATEGORY_LABEL[po.category]}
                </span>
              </div>
              <ArrowRightIcon className="size-4 text-faint group-hover:text-ink transition-colors" />
            </Link>
          </Card>

          {po.notes ? (
            <Card className="p-6 flex flex-col gap-2">
              <SectionHeader title="Notes" />
              <p className="text-sm text-ink-soft leading-relaxed">{po.notes}</p>
            </Card>
          ) : null}
        </div>
      </div>

      {/* On a phone the delivery is the one thing this screen is for, so it stays in reach under a
          long order rather than sitting in a header that has scrolled away. */}
      {actions.receiveGoods && canReceiveGoods && !roomy ? (
        <StickyActionBar>
          <Button
            className="w-full"
            leadingIcon={<TruckIcon className="size-4" />}
            onClick={() => navigate(`/purchase-orders/${id}/receive`)}
          >
            Receive goods
          </Button>
        </StickyActionBar>
      ) : null}

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          remove.reset();
        }}
        onConfirm={() => void remove.mutate(undefined)}
        title="Delete this draft?"
        body={`${po.po_number} is removed permanently. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          setCancelReason("");
          setCancelReasonError(undefined);
          cancel.reset();
        }}
        onConfirm={submitCancel}
        title="Cancel this purchase order?"
        body={`${po.po_number} moves to cancelled and no longer counts as open spend. This can't be undone.`}
        confirmLabel="Cancel order"
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
          {...((cancelReasonError) !== undefined ? { error: cancelReasonError } : {})}
        />
      </ConfirmDialog>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/purchase-orders"
      className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to purchase orders
    </Link>
  );
}
