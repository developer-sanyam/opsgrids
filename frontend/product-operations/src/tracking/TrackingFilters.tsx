import type { LedgerKind } from "@/api/trackingTypes";
import type { StockStatus } from "@/api/types";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { LEDGER_KIND_OPTIONS, STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { Button, Card, Select, TextField } from "@/ui";
import { ItemPicker } from "./ItemPicker";
import type { TrackingFilterState } from "./useTrackingFilters";
import { useLocationOptions } from "./useLocationOptions";
import { useLotOptions, useRollOptions } from "./useTrackedOptions";

const STATUS_OPTIONS = (Object.keys(STOCK_STATUS_LABEL) as StockStatus[]).map((value) => ({
  value,
  label: STOCK_STATUS_LABEL[value],
}));

interface TrackingFiltersProps {
  filters: TrackingFilterState;
  itemId: string;
  setFilter: <K extends keyof TrackingFilterState>(key: K, value: TrackingFilterState[K]) => void;
  onClear: () => void;
  active: boolean;
  /** Ledger-only controls. */
  showKind?: boolean | undefined;
  showDates?: boolean | undefined;
  /** Stock-only control. */
  showStatus?: boolean | undefined;
  /** Names the landmark: "Filter the ledger". */
  label: string;
}

/**
 * The filter card for the stock and ledger screens: item, warehouse and location, lot and roll
 * (offered once an item is chosen, since a lot belongs to one item), plus kind and date range on
 * the ledger or status on stock. Every control is a native select or input with its own label.
 */
export function TrackingFilters({
  filters,
  itemId,
  setFilter,
  onClear,
  active,
  showKind,
  showDates,
  showStatus,
  label,
}: TrackingFiltersProps) {
  const warehouses = useWarehouseIdOptions();
  const locations = useLocationOptions(filters.warehouseId);
  const lots = useLotOptions(itemId);
  const rolls = useRollOptions(itemId, filters.lotId);

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <form
        role="search"
        aria-label={label}
        onSubmit={(e) => e.preventDefault()}
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
      >
        <ItemPicker value={filters.item} onChange={(item) => setFilter("item", item)} label="Item" />
        <Select
          label="Warehouse"
          value={filters.warehouseId}
          onChange={(e) => setFilter("warehouseId", e.target.value)}
          placeholder="All warehouses"
          options={warehouses.options}
        />
        <Select
          label="Location"
          value={filters.locationId}
          onChange={(e) => setFilter("locationId", e.target.value)}
          placeholder="All locations"
          options={locations.options}
          disabled={filters.warehouseId === ""}
          helper={filters.warehouseId === "" ? "Choose a warehouse first." : undefined}
        />
        <Select
          label="Lot"
          value={filters.lotId}
          onChange={(e) => setFilter("lotId", e.target.value)}
          placeholder="All lots"
          options={withCurrent(lots.options, filters.lotId)}
          disabled={itemId === ""}
          helper={itemId === "" ? "Choose an item first." : undefined}
        />
        <Select
          label="Roll"
          value={filters.rollId}
          onChange={(e) => setFilter("rollId", e.target.value)}
          placeholder="All rolls"
          options={withCurrent(rolls.options, filters.rollId)}
          disabled={itemId === ""}
          helper={itemId === "" ? "Choose an item first." : undefined}
        />
        {showKind ? (
          <Select
            label="Kind"
            value={filters.kind}
            onChange={(e) => setFilter("kind", e.target.value as LedgerKind | "")}
            placeholder="All kinds"
            options={[...LEDGER_KIND_OPTIONS]}
          />
        ) : null}
        {showStatus ? (
          <Select
            label="Status"
            value={filters.status}
            onChange={(e) => setFilter("status", e.target.value as StockStatus | "")}
            placeholder="All statuses"
            options={STATUS_OPTIONS}
          />
        ) : null}
        {showDates ? (
          <>
            <TextField
              label="From"
              type="date"
              value={filters.from}
              max={filters.to || undefined}
              onChange={(e) => setFilter("from", e.target.value)}
            />
            <TextField
              label="To"
              type="date"
              value={filters.to}
              min={filters.from || undefined}
              onChange={(e) => setFilter("to", e.target.value)}
            />
          </>
        ) : null}
      </form>
      {active ? (
        <div>
          <Button variant="ghost" size="sm" onClick={onClear}>
            Clear filters
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

/** A filter value seeded from the address may not be among the loaded options yet; keep it
 *  selectable so the control never silently shows "All" while the list is filtered. */
function withCurrent(options: { value: string; label: string }[], current: string) {
  if (current === "" || options.some((o) => o.value === current)) return options;
  return [{ value: current, label: "Selected" }, ...options];
}
