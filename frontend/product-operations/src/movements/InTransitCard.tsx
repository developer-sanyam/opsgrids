import { listInTransit } from "@/api/movements";
import type { InTransitRowOut } from "@/api/movementTypes";
import { problemMessage, useAsync } from "@opsgrids/client";
import { formatQty } from "@/lib/quantity";
import { Card, DataTable, EmptyState, ErrorState, SectionHeader } from "@/ui";
import type { Column } from "@/ui";
import { TruckIcon } from "@opsgrids/ui";

const COLUMNS: Column<InTransitRowOut>[] = [
  {
    key: "item",
    header: "Item",
    mobile: "title",
    render: (r) => (
      <div className="flex flex-col leading-tight">
        <span className="font-semibold text-ink">{r.sku}</span>
        <span className="text-xs text-muted">{r.item_name}</span>
      </div>
    ),
  },
  {
    key: "destination",
    header: "Arriving at",
    render: (r) => <span className="text-ink-soft">{r.warehouse_code}</span>,
  },
  {
    key: "in_transit",
    header: "In transit",
    align: "right",
    render: (r) => (
      <span className="tnum font-semibold text-ink">
        {formatQty(r.in_transit)} {r.uom}
      </span>
    ),
  },
  {
    key: "physical",
    header: "On hand there now",
    align: "right",
    render: (r) => (
      <span className="tnum text-ink-soft">
        {formatQty(r.physical)} {r.uom}
      </span>
    ),
  },
];

/**
 * Stock on its way, by item and destination. In transit and physical on-hand are two separate
 * columns and are never added: in-transit stock is owned but not yet there, so it is outside the
 * destination's on-hand until it is received.
 */
export function InTransitCard() {
  const state = useAsync(() => listInTransit(), []);
  return (
    <Card className="p-2 sm:p-4">
      <div className="px-3 pt-2 pb-3">
        <SectionHeader
          title="In transit now"
          subtitle="Left its source, not yet received. Shown beside what is physically on hand at the destination — the two are never added together."
        />
      </div>
      <DataTable
        columns={COLUMNS}
        rows={state.status === "success" ? state.data : []}
        rowKey={(r) => `${r.inventory_item_id}:${r.warehouse_id}`}
        caption="Stock in transit by item and destination"
        mobileCard="auto"
        loading={state.status === "loading"}
        loadingRows={3}
        errorState={
          state.status === "error" ? (
            <ErrorState title="Couldn't load stock in transit" message={problemMessage(state.error)} onRetry={state.reload} />
          ) : undefined
        }
        emptyState={
          <EmptyState
            icon={<TruckIcon className="size-6" />}
            title="Nothing is in transit"
            description="Stock appears here once a transfer is dispatched, until the destination receives it."
          />
        }
      />
    </Card>
  );
}
