import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { locateStock } from "@/api/tracking";
import type { LocateOut, LocateResultOut } from "@/api/trackingTypes";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { formatNumber } from "@/lib/format";
import { autoArrangedHref, countBins, floorViewHref, groupLocateResults, highlightIdsFor, placementNote } from "@/lib/locate";
import type { FloorGroup, WarehouseGroup } from "@/lib/locate";
import { mapsEnabled } from "@/lib/mapsConfig";
import { formatQty } from "@/lib/quantity";
import { STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Button, Card, EmptyState, ErrorState, InlineAlert, SectionHeader, Skeleton, Tag } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { MapPinIcon, SearchIcon } from "@opsgrids/ui";

const LINK = "font-semibold text-accent hover:text-accent-strong transition-colors";

interface FindStockPanelProps {
  /** The item to look for. Empty means none is chosen yet. */
  itemId: string;
  lotId?: string | undefined;
  rollId?: string | undefined;
  /** What is being looked for, in words: "FAB-100", "lot LOT-7". */
  subject?: string | undefined;
  /** The item's unit, shown beside each quantity. */
  uom?: string | undefined;
}

/**
 * "Where is it?" — which bins hold this item, lot or roll right now, grouped by warehouse and
 * floor, with a way to see each on its floor plan. A bin is a row per status it holds stock in, and
 * the counts here are of bins. The results do not say whether a warehouse has a position on the
 * map, so in a build with a map "Show on map" is shown disabled, with that reason beside it.
 *
 * It looks only when asked, so a screen that mounts it pays nothing until someone clicks. It owns
 * its own request, its own error with a retry and its own slow-load note, and sits inside a boundary
 * (two: one around the whole card, one around the results): a failed lookup, or a response it cannot
 * draw, is a message in this card and the stock table beside it is untouched. Mount it with a `key`
 * made of the item, lot and roll so changing any of them starts it over.
 */
export function FindStockPanel(props: FindStockPanelProps) {
  return (
    <PanelBoundary title="Find stock">
      <FindStock {...props} />
    </PanelBoundary>
  );
}

function FindStock({ itemId, lotId, rollId, subject, uom }: FindStockPanelProps) {
  const [searches, setSearches] = useState(0);
  const found = useAsync<LocateOut | null>(
    () =>
      searches === 0 || itemId === ""
        ? Promise.resolve(null)
        : locateStock({ inventory_item_id: itemId, lot_id: lotId, roll_id: rollId }),
    [searches, itemId, lotId, rollId],
  );
  const slow = useSlowLoad(searches > 0 && found.status === "loading");
  const ready = found.status === "success" ? found.data : null;
  const groups = ready ? groupLocateResults(ready.results) : [];
  // The server answers one row per bin and status; people count bins.
  const bins = ready ? countBins(ready.results) : 0;

  const announcement = (() => {
    if (searches === 0) return "";
    if (found.status === "loading") return "Looking for where it is stored…";
    if (!ready) return "";
    if (ready.results.length === 0) return "Nothing is stored anywhere right now.";
    return `Found in ${bins} location${bins === 1 ? "" : "s"} across ${groups.length} warehouse${groups.length === 1 ? "" : "s"}.`;
  })();

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <SectionHeader
          title="Find stock"
          subtitle={
            subject
              ? `Which bins hold ${subject} right now, and where they are.`
              : "Which bins hold the item you choose, and where they are."
          }
        />
        <Button
          size="sm"
          className="w-fit shrink-0"
          leadingIcon={<SearchIcon className="size-4" />}
          onClick={() => setSearches((n) => n + 1)}
          disabled={itemId === "" || (searches > 0 && found.status === "loading")}
        >
          {searches === 0 ? "Find where it is" : "Search again"}
        </Button>
      </div>

      <p className="text-sm text-muted" role="status" aria-live="polite">
        {announcement}
      </p>

      {itemId === "" ? <p className="text-sm text-muted">Choose an item above to see which bins hold it.</p> : null}

      {searches > 0 && found.status === "loading" ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-12 w-full rounded-tile" />
          <Skeleton className="h-12 w-full rounded-tile" />
          <SlowLoadNotice show={slow} what="the results" />
        </div>
      ) : null}

      {found.status === "error" ? (
        <ErrorState title="Couldn't look up where it is" message={problemMessage(found.error)} onRetry={found.reload} />
      ) : null}

      {ready && ready.results.length === 0 ? (
        <EmptyState
          icon={<MapPinIcon className="size-6" />}
          title="Nothing is stored anywhere right now"
          description="No bin holds a positive balance of this. Post receipts or opening stock to bring some in."
        />
      ) : null}

      {ready && ready.results.length > 0 ? (
        <PanelBoundary title="The results">
          <div className="flex flex-col gap-6">
            {ready.truncated ? (
              <InlineAlert tone="warning" title={`Showing the first ${formatNumber(bins)} bins — narrow the search`}>
                More bins hold this than can be listed at once. Open a single lot or roll to see where that part is held.
              </InlineAlert>
            ) : null}
            {groups.map((group) => (
              <WarehouseResults key={group.warehouseId} group={group} uom={uom} />
            ))}
          </div>
        </PanelBoundary>
      ) : null}
    </Card>
  );
}

function WarehouseResults({ group, uom }: { group: WarehouseGroup; uom: string | undefined }) {
  const hintId = useId();
  const mapInBuild = mapsEnabled();
  return (
    <section aria-label={`Stock in ${group.warehouseName}`} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-bold text-ink">
          {group.warehouseName}{" "}
          <span className="text-sm font-normal text-muted">
            · {formatNumber(group.resultCount)} location{group.resultCount === 1 ? "" : "s"}
          </span>
        </h3>
        {mapInBuild ? (
          <Button size="sm" variant="ghost" disabled aria-describedby={hintId}>
            Show on map
          </Button>
        ) : null}
      </div>
      {mapInBuild ? (
        <p id={hintId} className="text-xs text-muted">
          Not available from here: these results don&apos;t say whether {group.warehouseName} has a position on the map.
        </p>
      ) : null}
      {group.floors.map((floor) => (
        <FloorResults key={floor.floorId ?? "no-floor"} warehouseId={group.warehouseId} floor={floor} uom={uom} />
      ))}
    </section>
  );
}

function FloorResults({ warehouseId, floor, uom }: { warehouseId: string; floor: FloorGroup; uom: string | undefined }) {
  const allOnFloor = floorViewHref(warehouseId, floor.floorId, floor.results);
  const placedCount = highlightIdsFor(floor.results).length;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-ink-soft">{floor.floorName ?? "No floor assigned"}</h4>
        {allOnFloor && placedCount > 1 ? (
          <Link to={allOnFloor} className={`text-sm ${LINK}`}>
            Show all {placedCount} on floor
          </Link>
        ) : null}
      </div>
      <ul className="flex flex-col gap-2">
        {floor.results.map((result) => (
          // One row per bin and status, so the bin alone is not a unique key.
          <ResultRow key={`${result.location_id}:${result.status}`} warehouseId={warehouseId} result={result} uom={uom} />
        ))}
      </ul>
    </div>
  );
}

function ResultRow({ warehouseId, result, uom }: { warehouseId: string; result: LocateResultOut; uom: string | undefined }) {
  const note = placementNote(result);
  const onFloor = floorViewHref(warehouseId, result.floor_id, [result]);
  return (
    <li className="flex flex-col gap-2 rounded-tile border border-line p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="tnum font-semibold text-ink">{result.code}</span>
          <span className="text-sm text-ink-soft">{result.name}</span>
          <Tag>{LOCATION_KIND_LABEL[result.kind]}</Tag>
          {note ? <Tag tone="warning">{note}</Tag> : null}
        </div>
        <p className="truncate text-xs text-muted">{result.path}</p>
      </div>
      <div className="flex items-center justify-between gap-4 sm:justify-end">
        <span className="tnum whitespace-nowrap font-semibold text-ink">
          {formatQty(result.quantity)}
          {uom ? <span className="text-xs font-normal text-muted"> {uom}</span> : null}
          <span className="text-xs font-normal text-muted"> · {STOCK_STATUS_LABEL[result.status]}</span>
        </span>
        {onFloor ? (
          <Link to={onFloor} className={`text-sm whitespace-nowrap ${LINK}`}>
            Show on floor<span className="sr-only"> for {result.code}</span>
          </Link>
        ) : result.placement_reason === "no_published_layout" || result.placement_reason === "unplaced" ? (
          <Link to={autoArrangedHref(result)} className={`text-sm whitespace-nowrap ${LINK}`}>
            Show in 3-D<span className="sr-only"> for {result.code}</span>
          </Link>
        ) : null}
      </div>
    </li>
  );
}
