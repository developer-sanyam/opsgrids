import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getPurchaseOrder } from "@/api/client";
import { TODAY } from "@/api/fixtures";
import type { PoLineItem, PoStatus, PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, isLate, PO_STATUS_LABEL } from "@/data/derive";
import { useAsync } from "@opsgrids/client";
import { daysBetween, formatDate, formatMoney, formatMoneyPrecise, relativeDue } from "@/lib/format";
import { Avatar, Button, Card, ErrorState, PoStatusPill, SectionHeader, Skeleton } from "@/ui";
import {
  ArrowRightIcon,
  BuildingIcon,
  CheckCircleIcon,
  ClockIcon,
  MapPinIcon,
  TruckIcon,
} from "@opsgrids/ui";

function lineTotal(line: PoLineItem): number {
  return line.quantity * line.unit_price_minor;
}

/** The delivery banner: on-time / late for an open order, or the received date once fulfilled. */
function DeliveryStatus({ po, received }: { po: PurchaseOrderOut; received: boolean }) {
  if (received || po.status === "received" || po.status === "closed") {
    const when = po.received_date ?? TODAY;
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
  const late = isLate(po, TODAY);
  const overdue = Math.abs(daysBetween(po.expected_date, TODAY));
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
          {relativeDue(po.expected_date, TODAY)} · expected {formatDate(po.expected_date)}
        </span>
      </div>
    </div>
  );
}

export function PurchaseOrderDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const loader = useCallback(() => getPurchaseOrder(id), [id]);
  const state = useAsync(loader, [id]);

  // Local "receive" affordance — fixtures have no backend, so receiving marks the order locally.
  const [received, setReceived] = useState(false);

  const po = state.status === "success" ? state.data : null;
  const total = useMemo(
    () => (po ? po.line_items.reduce((s, l) => s + lineTotal(l), 0) : 0),
    [po],
  );

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
                ? state.error.message
                : "That purchase order could not be found."
            }
            onRetry={() => navigate("/purchase-orders")}
          />
        </Card>
      </div>
    );
  }

  const effectiveStatus: PoStatus = received ? "received" : po.status;
  const canReceive = po.status === "draft" || po.status === "approved";

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-muted">
            {CATEGORY_LABEL[po.category]} · raised by {po.raised_by}
          </span>
          <div className="flex items-center gap-3">
            <h1 className="display text-3xl sm:text-[2.1rem] font-extrabold leading-tight tnum">
              {po.po_number}
            </h1>
            <PoStatusPill status={effectiveStatus} />
          </div>
        </div>
        {canReceive && !received ? (
          <Button
            leadingIcon={<CheckCircleIcon className="size-4" />}
            onClick={() => setReceived(true)}
          >
            Mark as received
          </Button>
        ) : (
          <span className="inline-flex items-center gap-2 rounded-full bg-present-soft px-4 h-11 text-sm font-semibold text-present">
            <CheckCircleIcon className="size-4" />
            {PO_STATUS_LABEL[effectiveStatus]}
          </span>
        )}
      </header>

      {received ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-tile bg-present-soft px-4 py-3 text-sm font-medium text-present"
        >
          All line items marked received. In the live product this posts a goods-receipt note and
          updates stock on hand.
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 items-start">
        {/* Line items */}
        <Card className="p-2 sm:p-4 lg:col-span-2">
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
                  <th scope="col" className="py-3 px-3 text-right text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap hidden sm:table-cell">
                    Unit price
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
                        <span className="font-semibold text-ink">{line.description}</span>
                        <span className="text-xs text-faint tnum">{line.sku}</span>
                      </div>
                    </td>
                    <td className="py-3.5 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {line.quantity} {line.uom}
                    </td>
                    <td className="py-3.5 px-3 text-right tnum text-muted whitespace-nowrap hidden sm:table-cell">
                      {formatMoneyPrecise(line.unit_price_minor)}
                    </td>
                    <td className="py-3.5 px-3 text-right tnum font-semibold text-ink whitespace-nowrap">
                      {formatMoney(lineTotal(line))}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-line">
                  <td className="py-4 px-3 text-sm font-bold text-ink" colSpan={3}>
                    Total
                  </td>
                  <td className="py-4 px-3 text-right">
                    <span className="display text-xl font-extrabold tnum">{formatMoney(total)}</span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>

        {/* Sidebar: delivery + supplier + meta */}
        <div className="flex flex-col gap-5">
          <Card className="p-6 flex flex-col gap-4">
            <SectionHeader title="Delivery" />
            <DeliveryStatus po={po} received={received} />
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
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/purchase-orders"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to purchase orders
    </Link>
  );
}
