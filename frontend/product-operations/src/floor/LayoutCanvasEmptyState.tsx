import { Link } from "react-router-dom";

import { Button } from "@/ui";
import { MapPinIcon } from "@opsgrids/ui";

interface LayoutCanvasEmptyStateProps {
  warehouseId: string;
  /** What is needed to say anything: the warehouse's locations and which of them other floors hold. */
  status: "loading" | "error" | "success";
  /** Which of the two loads failed, when `status` is `error`. */
  problem: "locations" | "floors";
  /** Every location in the warehouse. */
  totalLocations: number;
  /** How many are on no floor and not on this draft — what Auto-arrange would place. */
  unplacedCount: number;
  /** How many more another floor's published layout already holds. */
  elsewhereCount: number;
  /** An open draft and permission to write to it: the only case that is offered a way forward. */
  canPlace: boolean;
  onArrange: () => void;
  onPlaceOneByOne: () => void;
  onRetry: () => void;
}

/**
 * What the floor canvas says when no location is on it — drawn over the (otherwise empty) canvas,
 * because an empty grey box tells nobody what to do. The overlay lets pointer events through so an
 * empty canvas still takes a click in "Add walk node" mode; only its own controls catch them.
 *
 * Five cases: the warehouse has no locations at all; some are on no floor and the caller can place
 * them (two ways forward, with the count); every one is already on another floor (nothing to
 * arrange, one way forward); there are some and the caller cannot place them (a read-only
 * published layout, or no write permission); the locations or floors could not be loaded.
 */
export function LayoutCanvasEmptyState({
  warehouseId,
  status,
  problem,
  totalLocations,
  unplacedCount,
  elsewhereCount,
  canPlace,
  onArrange,
  onPlaceOneByOne,
  onRetry,
}: LayoutCanvasEmptyStateProps) {
  if (status === "loading") return null;

  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-4">
      <div className="pointer-events-auto flex max-w-sm flex-col items-center gap-3 text-center">
        <span className="grid size-12 place-items-center rounded-tile bg-surface text-ink-soft">
          <MapPinIcon className="size-6" />
        </span>
        {status === "error" ? (
          <>
            <p className="text-base font-bold text-ink">
              {problem === "floors" ? "Couldn't check the warehouse's other floors" : "Couldn't load this warehouse's locations"}
            </p>
            <p className="text-sm text-muted">
              {problem === "floors"
                ? "Without them a location that is already on another floor can't be told from one that is on none."
                : "The floor can't be drawn until they load."}
            </p>
            <Button size="sm" variant="secondary" onClick={onRetry}>
              Try again
            </Button>
          </>
        ) : totalLocations === 0 ? (
          <>
            <p className="text-base font-bold text-ink">No locations in this warehouse yet</p>
            <p className="text-sm text-muted">Add the warehouse's zones, racks and bins first, then place them on this floor.</p>
            <Link
              to={`/warehouses/${warehouseId}/locations`}
              className="inline-flex h-9 items-center justify-center rounded-tile border border-field-edge bg-surface px-3 text-sm font-semibold text-ink transition-colors duration-(--duration-quick) hover:bg-sunken"
            >
              Go to locations
            </Link>
          </>
        ) : canPlace && unplacedCount > 0 ? (
          <>
            <p className="text-base font-bold text-ink">No locations on this floor yet</p>
            <p className="text-sm text-muted">
              <span className="tnum font-semibold text-ink">{unplacedCount}</span> unplaced{" "}
              {unplacedCount === 1 ? "location" : "locations"} in this warehouse, on no floor yet.{" "}
              {unplacedCount === 1
                ? "Auto-arrange puts it on this floor in a tidy grid, or place it from the Unplaced locations list."
                : "Auto-arrange puts them on this floor in a tidy grid, or place them one at a time from the Unplaced locations list."}
              {elsewhereCount > 0
                ? ` ${elsewhereCount} more ${elsewhereCount === 1 ? "is" : "are"} already on another floor and left out.`
                : ""}
            </p>
            <div className="flex flex-col items-stretch gap-2">
              <Button size="sm" onClick={onArrange}>
                {unplacedCount === 1 ? "Auto-arrange 1 unplaced location" : `Auto-arrange all ${unplacedCount} unplaced locations`}
              </Button>
              <Button size="sm" variant="secondary" onClick={onPlaceOneByOne}>
                Place one at a time
              </Button>
            </div>
          </>
        ) : canPlace && elsewhereCount > 0 ? (
          <>
            <p className="text-base font-bold text-ink">No locations on this floor yet</p>
            <p className="text-sm text-muted">
              Every location in this warehouse is already on another floor, so there is nothing to auto-arrange. If one
              belongs on this floor as well, choose it from the Unplaced locations list.
            </p>
            <Button size="sm" variant="secondary" onClick={onPlaceOneByOne}>
              Show locations on other floors
            </Button>
          </>
        ) : (
          <>
            <p className="text-base font-bold text-ink">No locations on this floor yet</p>
            <p className="text-sm text-muted">
              Nothing is placed on this layout. Locations are placed on a draft by someone who can edit layouts.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
