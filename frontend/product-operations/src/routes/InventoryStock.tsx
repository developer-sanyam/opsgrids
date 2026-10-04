import { useState } from "react";

import { listStock } from "@/api/tracking";
import { Can } from "@opsgrids/client";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { OpeningStockDialog } from "@/forms/OpeningStockDialog";
import { StockStatusChangeDialog } from "@/forms/StockStatusChangeDialog";
import { StockTransferDialog } from "@/forms/StockTransferDialog";
import { Button, PageHeader } from "@/ui";
import { PlusIcon, useToast } from "@opsgrids/ui";
import { RequireStockRead } from "@/tracking/pageParts";
import { StockTable } from "@/tracking/StockTable";
import { TrackingFilters } from "@/tracking/TrackingFilters";
import { useTrackingFilters } from "@/tracking/useTrackingFilters";
import { useCursorList } from "@/tracking/useCursorList";

/**
 * Stock by bucket — item × location × status × lot × roll — across the organization
 * (`GET /operations/inventory/stock`, `operations.inventory.stock.read`), narrowed by item,
 * warehouse, location, lot, roll and status, newest buckets paged with "Load more". Transfer and
 * status change act on a bucket; opening stock brings existing stock onto the books.
 */
export function InventoryStockPage() {
  return (
    <RequireStockRead what="stock">
      <StockScreen />
    </RequireStockRead>
  );
}

function StockScreen() {
  const toast = useToast();
  const { filters, itemId, setFilter, clear, active } = useTrackingFilters();
  const [opening, setOpening] = useState(false);
  const [transferring, setTransferring] = useState<OnSiteRow | null>(null);
  const [changing, setChanging] = useState<OnSiteRow | null>(null);

  const list = useCursorList(
    (cursor, signal) =>
      listStock({
        item_id: itemId,
        warehouse_id: filters.warehouseId,
        location_id: filters.locationId,
        lot_id: filters.lotId,
        roll_id: filters.rollId,
        status: filters.status === "" ? undefined : filters.status,
        cursor,
        limit: 50,
        signal,
      }),
    [itemId, filters.warehouseId, filters.locationId, filters.lotId, filters.rollId, filters.status],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Stock"
        description="Every bucket of stock by location, status, lot and roll."
        actions={
          <Can permission="operations.inventory.stock.adjust" scope="ORG">
            <Button onClick={() => setOpening(true)} leadingIcon={<PlusIcon className="size-4" />}>
              Opening stock
            </Button>
          </Can>
        }
      />

      <TrackingFilters
        label="Filter stock"
        filters={filters}
        itemId={itemId}
        setFilter={setFilter}
        onClear={clear}
        active={active}
        showStatus
      />

      <StockTable
        list={list}
        filtered={active}
        onTransfer={setTransferring}
        onChangeStatus={setChanging}
        emptyAction={
          <Can permission="operations.inventory.stock.adjust" scope="ORG">
            <Button size="sm" onClick={() => setOpening(true)}>
              Post opening stock
            </Button>
          </Can>
        }
      />

      {opening ? (
        <OpeningStockDialog
          item={filters.item}
          onClose={() => setOpening(false)}
          onSaved={() => {
            setOpening(false);
            toast.success("Opening stock posted.");
            list.reload();
          }}
        />
      ) : null}
      {transferring ? (
        <StockTransferDialog
          row={transferring}
          onClose={() => setTransferring(null)}
          onSaved={() => {
            setTransferring(null);
            toast.success("Stock transferred.");
            list.reload();
          }}
        />
      ) : null}
      {changing ? (
        <StockStatusChangeDialog
          row={changing}
          onClose={() => setChanging(null)}
          onSaved={() => {
            setChanging(null);
            toast.success("Status changed.");
            list.reload();
          }}
        />
      ) : null}
    </div>
  );
}
