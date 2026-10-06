import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { listStock } from "@/api/tracking";
import type { StockRowOut } from "@/api/trackingTypes";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { formatNumber } from "@/lib/format";
import { formatQty, qtyToMilli } from "@/lib/quantity";
import { STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Drawer, EmptyState, ErrorState, InlineAlert, Skeleton } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { BoxIcon } from "@opsgrids/ui";
import type { BinSummary } from "./binSummary";

/** The drawer shows this many stock rows; a bin holding more is finished on the Stock screen. */
const DRAWER_STOCK_LIMIT = 50;

interface BinDrawerProps {
  bin: BinSummary | null;
  onClose: () => void;
}

function FieldRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-3 text-sm first:border-t-0 first:pt-0">
      <dt className="text-muted">{label}</dt>
      <dd className="tnum text-right font-semibold text-ink">{value}</dd>
    </div>
  );
}

/**
 * One location from a floor view: what it is, and what is stored in it right now.
 *
 * The stock section is its own request in its own boundary. If the balances fail to load, the
 * drawer says so with a retry and the location's own fields above stay readable; a render error in
 * the stock list takes only that list down. A caller without `stock.read` is told, not shown an
 * error — the endpoint would refuse the call, and a UI that fires it anyway is only noise.
 */
export function BinDrawer({ bin, onClose }: BinDrawerProps) {
  return (
    <Drawer
      open={bin !== null}
      onClose={onClose}
      title={bin ? bin.name : "Location"}
      description={bin ? bin.code : undefined}
    >
      {bin ? <BinBody bin={bin} /> : null}
    </Drawer>
  );
}

function BinBody({ bin }: { bin: BinSummary }) {
  const { details } = bin;
  return (
    <div className="flex flex-col gap-6">
      <dl className="flex flex-col">
        <FieldRow label="Kind" value={LOCATION_KIND_LABEL[bin.kind]} />
        <FieldRow label="Code" value={bin.code} />
        {details ? (
          <>
            <FieldRow label="Barcode" value={details.barcode ?? "—"} />
            <FieldRow
              label="Pick sequence"
              value={details.pick_sequence !== null ? formatNumber(details.pick_sequence) : "—"}
            />
            <FieldRow
              label="Max weight"
              value={details.max_weight_grams !== null ? `${formatNumber(details.max_weight_grams)} g` : "—"}
            />
            <FieldRow
              label="Max volume"
              value={details.max_volume_milli !== null ? `${formatNumber(details.max_volume_milli)} mL` : "—"}
            />
            <FieldRow label="Storage type" value={details.storage_type ?? "—"} />
            <FieldRow label="Default location" value={details.is_default ? "Yes" : "No"} />
          </>
        ) : null}
      </dl>

      <section aria-labelledby="bin-stock-heading" className="flex flex-col gap-3">
        <h3 id="bin-stock-heading" className="text-sm font-bold text-ink">
          Stored here
        </h3>
        <PanelBoundary title="The stock list">
          <BinStock locationId={bin.id} />
        </PanelBoundary>
      </section>
    </div>
  );
}

function BinStock({ locationId }: { locationId: string }) {
  const canRead = useCan("operations.inventory.stock.read", "ORG");
  const stock = useAsync(
    () => (canRead ? listStock({ location_id: locationId, limit: DRAWER_STOCK_LIMIT }) : Promise.resolve(null)),
    [locationId, canRead],
  );
  const slow = useSlowLoad(stock.status === "loading");

  if (!canRead) {
    return (
      <InlineAlert tone="info" title="You can't see stock levels" live="off">
        Reading what is stored in a location needs the stock read permission.
      </InlineAlert>
    );
  }
  if (stock.status === "loading") {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        <Skeleton className="h-12 w-full rounded-tile" />
        <Skeleton className="h-12 w-full rounded-tile" />
        <SlowLoadNotice show={slow} what="this location's stock" />
      </div>
    );
  }
  if (stock.status === "error") {
    return (
      <ErrorState title="Couldn't load this location's stock" message={problemMessage(stock.error)} onRetry={stock.reload} />
    );
  }
  const rows = stock.data?.items.filter((r) => qtyToMilli(r.on_hand_qty) > 0) ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<BoxIcon className="size-6" />}
        title="Nothing stored here"
        description="This location holds no stock right now."
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <StockLine key={row.id} row={row} />
        ))}
      </ul>
      {stock.data?.next_cursor ? (
        <p className="text-sm text-muted">
          Showing the first {DRAWER_STOCK_LIMIT} rows.{" "}
          <Link
            to={`/inventory/stock?location=${encodeURIComponent(locationId)}`}
            className="font-semibold text-accent hover:text-accent-strong transition-colors"
          >
            See all stock in this location
          </Link>
        </p>
      ) : (
        <p className="text-sm text-muted">
          <Link
            to={`/inventory/stock?location=${encodeURIComponent(locationId)}`}
            className="font-semibold text-accent hover:text-accent-strong transition-colors"
          >
            Open in Stock
          </Link>
        </p>
      )}
    </div>
  );
}

function StockLine({ row }: { row: StockRowOut }) {
  const tracked = [row.lot_no ? `Lot ${row.lot_no}` : null, row.roll_no ? `Roll ${row.roll_no}` : null].filter(
    (part): part is string => part !== null,
  );
  return (
    <li className="flex items-start justify-between gap-3 rounded-tile border border-line p-3">
      <div className="flex min-w-0 flex-col leading-tight">
        <Link to={`/inventory/${row.inventory_item_id}`} className="tnum font-semibold text-ink hover:text-accent">
          {row.sku}
        </Link>
        <span className="truncate text-xs text-muted">{row.item_name}</span>
        <span className="text-xs text-muted">
          {STOCK_STATUS_LABEL[row.status]}
          {tracked.length > 0 ? ` · ${tracked.join(" · ")}` : ""}
        </span>
      </div>
      <span className="tnum whitespace-nowrap font-semibold text-ink">
        {formatQty(row.on_hand_qty)} <span className="text-xs font-normal text-muted">{row.uom}</span>
      </span>
    </li>
  );
}
