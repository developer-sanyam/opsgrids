import { listLedger } from "@/api/tracking";
import { PageHeader } from "@/ui";
import { RequireStockRead } from "@/tracking/pageParts";
import { LedgerTable } from "@/tracking/LedgerTable";
import { TrackingFilters } from "@/tracking/TrackingFilters";
import { useTrackingFilters } from "@/tracking/useTrackingFilters";
import { useCursorList } from "@/tracking/useCursorList";

/**
 * The organization-wide stock ledger (`GET /operations/inventory/ledger`,
 * `operations.inventory.stock.read`), newest first, narrowed by item, location, lot, roll, kind and
 * a date range on the move's business date, paged with "Load more" on the server's cursor.
 * Append-only: nothing on this screen edits a row.
 */
export function InventoryLedgerPage() {
  return (
    <RequireStockRead what="the stock ledger">
      <LedgerScreen />
    </RequireStockRead>
  );
}

function LedgerScreen() {
  const { filters, itemId, setFilter, clear, active } = useTrackingFilters();

  const list = useCursorList(
    (cursor, signal) =>
      listLedger({
        item_id: itemId,
        location_id: filters.locationId,
        lot_id: filters.lotId,
        roll_id: filters.rollId,
        kind: filters.kind === "" ? undefined : filters.kind,
        from_date: filters.from,
        to_date: filters.to,
        cursor,
        limit: 50,
        signal,
      }),
    [itemId, filters.locationId, filters.lotId, filters.rollId, filters.kind, filters.from, filters.to],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Stock ledger"
        description="Every change to stock, newest first. Nothing here can be edited."
      />

      <TrackingFilters
        label="Filter the ledger"
        filters={filters}
        itemId={itemId}
        setFilter={setFilter}
        onClear={clear}
        active={active}
        showKind
        showDates
      />

      <LedgerTable list={list} filtered={active} />
    </div>
  );
}
