import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listGoodsReceipts } from "@/api/client";
import type { GoodsReceiptSummaryOut } from "@/api/types";
import { useCursorList } from "@/tracking/useCursorList";
import { formatDate } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  InlineAlert,
  LoadError,
  PageHeader,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { PackageIcon, useDebouncedValue } from "@opsgrids/ui";

const PAGE = 25;

/**
 * Goods receipts, newest first. The list is keyset-paginated, so there is no page count — only
 * "Load more" while the server says there is more. `?purchase_order_id=` narrows it to one order's
 * deliveries (the purchase order screen links here).
 */
export function GoodsReceiptsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const purchaseOrderId = searchParams.get("purchase_order_id") ?? undefined;
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);

  const list = useCursorList(
    (cursor, signal) =>
      listGoodsReceipts({
        ...(purchaseOrderId !== undefined ? { purchase_order_id: purchaseOrderId } : {}),
        q: debouncedSearch,
        ...(cursor !== undefined ? { cursor } : {}),
        limit: PAGE,
        signal,
      }),
    [purchaseOrderId, debouncedSearch],
  );

  const columns: Column<GoodsReceiptSummaryOut>[] = [
    {
      key: "number",
      header: "Receipt",
      render: (r) => <span className="font-semibold text-ink tnum">{r.number}</span>,
    },
    {
      key: "po",
      header: "Purchase order",
      render: (r) => <span className="tnum text-ink-soft">{r.po_number}</span>,
    },
    {
      key: "supplier",
      header: "Supplier",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="text-ink-soft">{r.supplier_name}</span>,
    },
    {
      key: "note",
      header: "Delivery note",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => <span className="text-ink-soft">{r.delivery_note ?? "—"}</span>,
    },
    {
      key: "lines",
      header: "Lines",
      align: "right",
      render: (r) => <span className="tnum text-muted">{r.line_count}</span>,
    },
    {
      key: "received",
      header: "Received",
      align: "right",
      render: (r) => <span className="tnum text-muted">{formatDate(r.received_on)}</span>,
    },
  ];

  const filtersActive = debouncedSearch !== "" || purchaseOrderId !== undefined;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Procurement" title="Goods Receipts" />

      <Card className="p-4 sm:p-4 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search goods receipts"
            placeholder="Search by receipt number or delivery note…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          {purchaseOrderId !== undefined ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted">Filtered to one purchase order</span>
              <Button variant="outline" size="sm" onClick={() => setSearchParams({})}>
                Show all receipts
              </Button>
            </div>
          ) : null}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {list.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" role="status" aria-label="Loading goods receipts">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {list.error !== null ? (
          <LoadError error={list.error} noun="goods receipts" onRetry={list.reload} />
        ) : null}
        {list.status === "ready" ? (
          <>
            <DataTable
              columns={columns}
              rows={list.items}
              rowKey={(r) => r.id}
              caption="Goods receipts"
              onRowClick={(r) => navigate(`/goods-receipts/${r.id}`)}
              emptyState={
                <EmptyState
                  icon={<PackageIcon className="size-6" />}
                  title={filtersActive ? "No goods receipts match these filters" : "No goods receipts yet"}
                  description={
                    filtersActive
                      ? "Try a different search, or show every purchase order's receipts."
                      : "Open an approved purchase order and choose Receive goods to post the first one."
                  }
                />
              }
            />
            <div className="flex flex-col items-center gap-3 px-3 py-4" aria-live="polite">
              {list.moreError ? (
                <InlineAlert tone="danger" title="Couldn't load more receipts. The ones above are unchanged." />
              ) : null}
              <span className="text-sm text-muted tnum">
                {list.items.length} receipt{list.items.length === 1 ? "" : "s"} shown
                {list.hasMore ? "" : list.items.length > 0 ? ", that's all of them" : ""}
              </span>
              {list.hasMore ? (
                <Button variant="outline" loading={list.loadingMore} onClick={() => void list.loadMore()}>
                  {list.moreError ? "Try again" : "Load more"}
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}
