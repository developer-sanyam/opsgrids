import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  acceptOrder,
  cancelOrder,
  getOrder,
  getWarehouseById,
  holdOrder,
  placeOrder,
  quoteOrder,
  releaseOrder,
} from "@/api/client";
import type { OrderOut } from "@/api/types";
import { orderActions } from "@/lib/orderActions";
import { isStaleOrderConflict, stockShortfalls } from "@/lib/orderErrors";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate, formatMoney } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { QuotePreview } from "@/forms/QuotePreview";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  OrderStatusPill,
  SectionHeader,
  Skeleton,
  TextArea,
} from "@/ui";
import { ArrowRightIcon, useToast } from "@opsgrids/ui";

const CHANNEL_LABEL: Record<OrderOut["channel"], string> = {
  phone: "Phone",
  manual: "Manual",
  sales: "Sales",
  walk_in: "Walk-in",
};

function BackLink() {
  return (
    <Link
      to="/orders"
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to orders
    </Link>
  );
}

export function OrderDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const canAccept = useCan("operations.orders.order.accept", "ORG");
  const canCancel = useCan("operations.orders.order.cancel", "ORG");

  const [holding, setHolding] = useState(false);
  const [holdReason, setHoldReason] = useState("");
  const [holdReasonError, setHoldReasonError] = useState<string | undefined>(undefined);
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelReasonError, setCancelReasonError] = useState<string | undefined>(undefined);
  // A stale-version (or moved-status) 409 on any action below: not an error to toast raw. The
  // order is refetched immediately and this names what happened, in front of the now-current data.
  const [conflictNotice, setConflictNotice] = useState<string | null>(null);

  const loader = () => getOrder(id);
  const state = useAsync(loader, [id]);
  const order = state.status === "success" ? state.data : null;

  const warehouseLoader = () => (order ? getWarehouseById(order.warehouse_id) : Promise.resolve(null));
  const warehouseState = useAsync(warehouseLoader, [order?.warehouse_id]);

  // A live re-check before Place: pricing or stock may have moved since the order was drafted.
  // Once accepted, the lines are already priced and reserved — the preview stops being asked.
  const showQuote = order !== null && (order.status === "DRAFT" || order.status === "PLACED");
  const quoteLoader = () =>
    order && showQuote
      ? quoteOrder({
          warehouse_id: order.warehouse_id,
          currency: order.currency,
          lines: order.lines.map((l) => ({
            inventory_item_id: l.inventory_item_id,
            quantity: l.quantity,
            ...(l.selling_price_minor !== null ? { selling_price_minor: l.selling_price_minor } : {}),
          })),
        })
      : Promise.resolve(null);
  const quoteState = useAsync(quoteLoader, [order?.id, order?.status, order?.updated_at]);

  function onConflict(error: Error) {
    if (isStaleOrderConflict(error)) {
      setConflictNotice(problemMessage(error));
      state.reload();
    }
  }

  const place = useMutation((version: number) => placeOrder(id, { version }), {
    onSuccess: (saved) => {
      toast.success(`${saved.number} placed.`);
      setConflictNotice(null);
      state.reload();
    },
    onError: onConflict,
  });
  const accept = useMutation((version: number) => acceptOrder(id, { version }), {
    onSuccess: (saved) => {
      toast.success(`${saved.number} accepted — stock reserved.`);
      setConflictNotice(null);
      state.reload();
    },
    onError: onConflict,
  });
  const hold = useMutation((input: { version: number; reason: string }) => holdOrder(id, input), {
    onSuccess: (saved) => {
      setHolding(false);
      setHoldReason("");
      toast.success(`${saved.number} put on hold.`);
      setConflictNotice(null);
      state.reload();
    },
    onError: onConflict,
  });
  const release = useMutation((version: number) => releaseOrder(id, { version }), {
    onSuccess: (saved) => {
      toast.success(`${saved.number} released.`);
      setConflictNotice(null);
      state.reload();
    },
    onError: onConflict,
  });
  const cancel = useMutation((input: { version: number; reason: string }) => cancelOrder(id, input), {
    onSuccess: (saved) => {
      setCancelling(false);
      setCancelReason("");
      toast.success(`${saved.number} cancelled.`);
      setConflictNotice(null);
      state.reload();
    },
    onError: onConflict,
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

  if (state.status === "error" || !order) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <ErrorState
            message={state.status === "error" ? problemMessage(state.error) : "That order could not be found."}
            {...(state.status === "error" ? { onRetry: state.reload } : {})}
          />
        </Card>
      </div>
    );
  }

  const actions = orderActions(order.status, canAccept, canCancel);
  const shortfalls = stockShortfalls(accept.error);

  function submitHold() {
    setHoldReasonError(undefined);
    if (holdReason.trim().length < 1) {
      setHoldReasonError("Say why this order is going on hold.");
      return;
    }
    // A nested function declaration re-widens `order`'s type even though it was already narrowed
    // above (only `const`-bound closures over the same synchronous scope keep it); this guard is
    // for the compiler, not runtime — the early return above already made this unreachable null.
    if (!order) return;
    void hold.mutate({ version: order.version, reason: holdReason.trim() });
  }

  function submitCancel() {
    setCancelReasonError(undefined);
    if (cancelReason.trim().length < 1) {
      setCancelReasonError("Say why this order is being cancelled.");
      return;
    }
    if (!order) return;
    void cancel.mutate({ version: order.version, reason: cancelReason.trim() });
  }

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-muted">{CHANNEL_LABEL[order.channel]} order</span>
          <div className="flex items-center gap-3">
            <h1 className="display text-3xl font-extrabold leading-tight tnum">{order.number}</h1>
            <OrderStatusPill status={order.status} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actions.place ? (
            <Can permission="operations.orders.order.write">
              <Button loading={place.pending} onClick={() => void place.mutate(order.version)}>
                Place order
              </Button>
            </Can>
          ) : null}
          {actions.accept ? (
            <Can permission="operations.orders.order.accept" scope="ORG">
              <Button loading={accept.pending} onClick={() => void accept.mutate(order.version)}>
                Accept order
              </Button>
            </Can>
          ) : null}
          {actions.hold ? (
            <Can permission="operations.orders.order.write">
              <Button variant="outline" onClick={() => setHolding(true)}>
                Put on hold
              </Button>
            </Can>
          ) : null}
          {actions.release ? (
            <Can permission="operations.orders.order.write">
              <Button variant="outline" loading={release.pending} onClick={() => void release.mutate(order.version)}>
                Release hold
              </Button>
            </Can>
          ) : null}
          {actions.cancel ? (
            <Can permission="operations.orders.order.cancel" scope="ORG">
              <Button variant="outline" onClick={() => setCancelling(true)}>
                Cancel order
              </Button>
            </Can>
          ) : null}
        </div>
      </header>

      {conflictNotice ? (
        <InlineAlert tone="warning" title="This order changed since it was loaded">
          {conflictNotice} The order below is the current version — review it and try again.
        </InlineAlert>
      ) : null}
      {/* A stale-version (or moved-status) 409 already has its own banner above — the warning
          conflict notice — so the generic danger banner below only ever repeats a message the
          operator has already seen once. */}
      {place.error && !isStaleOrderConflict(place.error) ? (
        <InlineAlert tone="danger" title={problemMessage(place.error)} />
      ) : null}
      {accept.error && shortfalls.length === 0 && !isStaleOrderConflict(accept.error) ? (
        <InlineAlert tone="danger" title={problemMessage(accept.error)} />
      ) : null}
      {shortfalls.length > 0 ? (
        <InlineAlert tone="danger" title="Not enough stock to accept this order">
          <ul className="mt-1 flex flex-col gap-1">
            {shortfalls.map((s) => (
              <li key={s.sku} className="tnum">
                {s.sku}: needs {s.requested}
                {s.uom ? ` ${s.uom}` : ""}, only {s.available} available ({s.on_hand} on hand, {s.reserved} already
                reserved)
              </li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}
      {/* `hold`'s own error, including a stale-version one, renders inside its still-open
          `ConfirmDialog` (below) instead of a second banner here — the dialog is the only thing
          on screen while it's open. */}
      {release.error && !isStaleOrderConflict(release.error) ? (
        <InlineAlert tone="danger" title={problemMessage(release.error)} />
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 items-start">
        <Card className="p-2 sm:p-4 lg:col-span-2">
          <div className="px-3 pt-2 pb-3">
            <SectionHeader title="Line items" subtitle={`${order.lines.length} lines`} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Line items for {order.number}</caption>
              <thead>
                <tr className="border-b border-line">
                  <th
                    scope="col"
                    className="py-3 px-3 text-left text-xs font-semibold uppercase tracking-wide text-faint"
                  >
                    Item
                  </th>
                  <th
                    scope="col"
                    className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap"
                  >
                    Qty
                  </th>
                  <th
                    scope="col"
                    className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap"
                  >
                    Line total
                  </th>
                </tr>
              </thead>
              <tbody>
                {order.lines.map((line) => (
                  <tr key={line.id} className="border-b border-line/70 last:border-0">
                    <td className="py-3 px-3">
                      <span className="font-semibold text-ink">{line.sku}</span>
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {formatQty(line.quantity)}
                    </td>
                    <td className="py-3 px-3 text-right tnum font-semibold text-ink whitespace-nowrap">
                      {line.line_total_minor !== null ? (
                        formatMoney(line.line_total_minor, order.currency)
                      ) : (
                        <span className="font-normal text-faint">Not priced</span>
                      )}
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
                    <span className="display text-xl font-extrabold tnum">
                      {formatMoney(order.total_minor, order.currency)}
                    </span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Customer" />
            <dl className="flex flex-col gap-2 text-sm">
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Name</dt>
                <dd className="font-semibold text-ink">{order.customer_name || "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Phone</dt>
                <dd className="tnum font-semibold text-ink">{order.customer_phone || "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Warehouse</dt>
                <dd className="font-semibold text-ink">
                  {warehouseState.status === "success" && warehouseState.data ? warehouseState.data.name : "—"}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Created</dt>
                <dd className="tnum font-semibold text-ink">{formatDate(order.created_at.slice(0, 10))}</dd>
              </div>
            </dl>
          </Card>

          {showQuote ? (
            <Card className="p-6 flex flex-col gap-4">
              <SectionHeader title="Pricing & availability" subtitle="Checked live, right now" />
              <QuotePreview
                status={quoteState.status}
                quote={quoteState.status === "success" ? quoteState.data : null}
                {...(quoteState.status === "error" ? { error: quoteState.error, onRetry: quoteState.reload } : {})}
              />
            </Card>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={holding}
        onClose={() => {
          setHolding(false);
          setHoldReason("");
          setHoldReasonError(undefined);
          hold.reset();
        }}
        onConfirm={submitHold}
        title="Put this order on hold?"
        body={`${order.number} stops moving through fulfilment until it's released.`}
        confirmLabel="Put on hold"
        pendingLabel="Saving…"
        tone="primary"
        pending={hold.pending}
        {...(hold.error ? { error: problemMessage(hold.error) } : {})}
      >
        <TextArea
          label="Reason"
          value={holdReason}
          onChange={(e) => setHoldReason(e.target.value)}
          maxLength={500}
          {...(holdReasonError !== undefined ? { error: holdReasonError } : {})}
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          setCancelReason("");
          setCancelReasonError(undefined);
          cancel.reset();
        }}
        onConfirm={submitCancel}
        title="Cancel this order?"
        body={`${order.number} moves to cancelled and releases any stock it has reserved. This can't be undone.`}
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
          {...(cancelReasonError !== undefined ? { error: cancelReasonError } : {})}
        />
      </ConfirmDialog>
    </div>
  );
}
