import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";

import { getGoodsReceipt } from "@/api/client";
import type { GoodsReceiptLineOut } from "@/api/types";
import { useAsync } from "@opsgrids/client";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { Card, InlineAlert, LoadError, ReceiptOutcomePill, SectionHeader, Skeleton, StockStatusPill } from "@/ui";
import { ArrowRightIcon } from "@opsgrids/ui";

const TH = "py-3 px-3 text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap";

function BackLink() {
  return (
    <Link
      to="/goods-receipts"
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to goods receipts
    </Link>
  );
}

function qtyIn(qty: string, uom: string | null): string {
  return `${formatQty(qty)}${uom ? ` ${uom}` : ""}`;
}

function RollsTable({ line }: { line: GoodsReceiptLineOut }) {
  return (
    <table className="w-full border-collapse text-xs">
      <caption className="sr-only">Rolls received on {line.sku}</caption>
      <thead>
        <tr className="border-b border-line">
          <th scope="col" className={`${TH} text-left`}>
            Roll
          </th>
          <th scope="col" className={`${TH} text-right`}>
            Length
          </th>
          <th scope="col" className={`${TH} text-right`}>
            Measured weight
          </th>
        </tr>
      </thead>
      <tbody>
        {line.rolls.map((roll) => (
          <tr key={roll.id} className="border-b border-line/70 last:border-0">
            <td className="py-2 px-3 font-semibold text-ink tnum">{roll.roll_no}</td>
            <td className="py-2 px-3 text-right tnum text-ink-soft">{qtyIn(roll.primary_qty, line.stock_uom)}</td>
            <td className="py-2 px-3 text-right tnum text-ink-soft">
              {roll.secondary_qty !== null ? qtyIn(roll.secondary_qty, roll.secondary_uom) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function GoodsReceiptDetailPage() {
  const { id = "" } = useParams();
  const state = useAsync(() => getGoodsReceipt(id), [id]);

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" role="status" aria-label="Loading goods receipt">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <LoadError error={state.error} noun="this goods receipt" onRetry={state.reload} />
        </Card>
      </div>
    );
  }

  const grn = state.data;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-col gap-2">
        <span className="text-sm font-medium text-muted">Goods receipt · {grn.supplier_name}</span>
        <h1 className="text-page-sm sm:text-page tnum">{grn.number}</h1>
      </header>

      {grn.replayed ? (
        <InlineAlert tone="info" title="This receipt was already posted">
          Your retry matched the first attempt, so nothing was received twice.
        </InlineAlert>
      ) : null}

      <Card className="p-6">
        <SectionHeader title="Delivery" />
        <dl className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-1">
            <dt className="text-muted">Purchase order</dt>
            <dd className="font-semibold text-ink tnum">
              <Link to={`/purchase-orders/${grn.purchase_order_id}`} className="text-accent hover:text-accent-strong">
                {grn.po_number}
              </Link>
            </dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-muted">Received on</dt>
            <dd className="font-semibold text-ink tnum">{formatDate(grn.received_on)}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-muted">Delivery note</dt>
            <dd className="font-semibold text-ink">{grn.delivery_note ?? "—"}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-muted">Posted</dt>
            <dd className="font-semibold text-ink tnum">
              {formatDateTime(grn.created_at)}
              {grn.created_by ? ` by ${grn.created_by}` : ""}
            </dd>
          </div>
        </dl>
        {grn.note ? <p className="mt-4 text-sm text-ink-soft leading-relaxed">{grn.note}</p> : null}
      </Card>

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="Lines" subtitle={`${grn.lines.length} line${grn.lines.length === 1 ? "" : "s"}`} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Lines received on {grn.number}</caption>
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className={`${TH} text-left`}>
                  Item
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Ordered
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Previously received
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  This receipt
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Cumulative
                </th>
                <th scope="col" className={`${TH} text-left`}>
                  Outcome
                </th>
                <th scope="col" className={`${TH} text-left`}>
                  Stock
                </th>
              </tr>
            </thead>
            <tbody>
              {grn.lines.map((line) => (
                <Fragment key={line.id}>
                  <tr className="border-b border-line/70 align-top last:border-0">
                    <td className="py-3 px-3">
                      <div className="flex flex-col leading-tight">
                        <span className="font-semibold text-ink">{line.description || line.sku}</span>
                        <span className="text-xs text-faint tnum">{line.sku}</span>
                        {line.lot_no ? (
                          <span className="text-xs text-muted tnum">
                            Lot {line.lot_no}
                            {line.supplier_lot_no ? ` (supplier lot ${line.supplier_lot_no})` : ""}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {qtyIn(line.ordered_qty, line.stock_uom)}
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {qtyIn(line.prior_received_qty, line.stock_uom)}
                    </td>
                    <td className="py-3 px-3 text-right tnum font-semibold text-ink whitespace-nowrap">
                      {qtyIn(line.received_qty, line.stock_uom)}
                      {line.entered_uom !== line.stock_uom ? (
                        <span className="block text-xs font-normal text-muted">
                          entered as {qtyIn(line.entered_qty, line.entered_uom)}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-3 px-3 text-right tnum text-ink-soft whitespace-nowrap">
                      {qtyIn(line.cumulative_qty, line.stock_uom)}
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex flex-col items-start gap-1">
                        <ReceiptOutcomePill outcome={line.outcome} />
                        {line.close_short ? <span className="text-xs text-muted">Closed short</span> : null}
                      </div>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex flex-col items-start gap-1">
                        {line.stock_status ? <StockStatusPill status={line.stock_status} /> : null}
                        <span className="text-xs text-muted">{line.location_label ?? "No stock posted"}</span>
                      </div>
                    </td>
                  </tr>
                  {line.rolls.length > 0 ? (
                    <tr className="border-b border-line/70 last:border-0">
                      <td colSpan={7} className="px-3 pb-4">
                        <RollsTable line={line} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
