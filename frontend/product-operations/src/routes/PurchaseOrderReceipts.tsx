import { Link } from "react-router-dom";

import { listGoodsReceipts } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import { Card, EmptyState, LoadError, SectionHeader, Skeleton } from "@/ui";
import { PackageIcon } from "@opsgrids/ui";

const SHOWN = 10;

/**
 * The deliveries received against one purchase order, newest first, from
 * `GET /operations/goods-receipts?purchase_order_id=`. A viewer without `receipt.read` gets the
 * server's 403 rendered as a plain "no access" note rather than a failure.
 */
export function PurchaseOrderReceipts({ purchaseOrderId, reloadKey }: { purchaseOrderId: string; reloadKey: string }) {
  const state = useAsync(
    () => listGoodsReceipts({ purchase_order_id: purchaseOrderId, limit: SHOWN }),
    [purchaseOrderId, reloadKey],
  );

  return (
    <Card className="p-2 sm:p-4">
      <div className="px-3 pt-2 pb-3">
        <SectionHeader title="Goods receipts" subtitle="Each delivery posted against this order." />
      </div>
      {state.status === "loading" ? (
        <div className="flex flex-col gap-3 p-3" role="status" aria-label="Loading goods receipts">
          <Skeleton className="h-12 w-full rounded-tile" />
          <Skeleton className="h-12 w-full rounded-tile" />
        </div>
      ) : null}
      {state.status === "error" ? (
        <LoadError error={state.error} noun="goods receipts" onRetry={state.reload} />
      ) : null}
      {state.status === "success" && state.data.items.length === 0 ? (
        <EmptyState
          icon={<PackageIcon className="size-6" />}
          title="Nothing received yet"
          description="Deliveries posted against this order appear here."
        />
      ) : null}
      {state.status === "success" && state.data.items.length > 0 ? (
        <>
          <ul className="flex flex-col">
            {state.data.items.map((r) => (
              <li key={r.id} className="border-b border-line/70 last:border-0">
                <Link
                  to={`/goods-receipts/${r.id}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-tile px-3 py-3 hover:bg-ground transition-colors"
                >
                  <span className="flex flex-col leading-tight">
                    <span className="font-semibold text-ink tnum">{r.number}</span>
                    <span className="text-xs text-muted">
                      {r.line_count} line{r.line_count === 1 ? "" : "s"}
                      {r.delivery_note ? ` · note ${r.delivery_note}` : ""}
                    </span>
                  </span>
                  <span className="tnum text-sm text-muted">{formatDate(r.received_on)}</span>
                </Link>
              </li>
            ))}
          </ul>
          {state.data.next_cursor !== null ? (
            <div className="px-3 py-3">
              <Link
                to={`/goods-receipts?purchase_order_id=${encodeURIComponent(purchaseOrderId)}`}
                className="text-sm font-semibold text-accent hover:text-accent-strong"
              >
                View all receipts for this order
              </Link>
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
