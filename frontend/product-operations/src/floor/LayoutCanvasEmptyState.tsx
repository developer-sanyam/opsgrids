import { Link } from "react-router-dom";

import { Button } from "@/ui";
import { MapPinIcon } from "@opsgrids/ui";

interface LayoutCanvasEmptyStateProps {
  warehouseId: string;
  /** The warehouse's locations are still loading (render nothing), failed, or loaded. */
  locations: "loading" | "error" | "success";
  /** Every location in the warehouse, and how many of them are not on this draft. */
  totalLocations: number;
  unplacedCount: number;
  /** An open draft and permission to write to it: the only case that is offered a way forward. */
  canPlace: boolean;
  onArrange: () => void;
  onPlaceOneByOne: () => void;
  onRetry: () => void;
}

/**
 * What the floor canvas says when nothing is on it — drawn over the (empty) canvas, because an
 * empty grey box tells nobody what to do. The overlay lets pointer events through so an empty
 * canvas still takes a click in "Add walk node" mode; only its own controls catch them.
 *
 * Four cases: the warehouse has no locations at all; there are some and the caller can place them
 * (two ways forward, with the count); there are some and the caller cannot (a read-only
 * published layout, or no write permission); the locations could not be loaded.
 */
export function LayoutCanvasEmptyState({
  warehouseId,
  locations,
  totalLocations,
  unplacedCount,
  canPlace,
  onArrange,
  onPlaceOneByOne,
  onRetry,
}: LayoutCanvasEmptyStateProps) {
  if (locations === "loading") return null;

  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-4">
      <div className="pointer-events-auto flex max-w-sm flex-col items-center gap-3 text-center">
        <span className="grid size-12 place-items-center rounded-tile bg-surface text-ink-soft">
          <MapPinIcon className="size-6" />
        </span>
        {locations === "error" ? (
          <>
            <p className="text-base font-bold text-ink">Couldn't load this warehouse's locations</p>
            <p className="text-sm text-muted">The floor can't be drawn until they load.</p>
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
        ) : canPlace ? (
          <>
            <p className="text-base font-bold text-ink">No locations on this floor yet</p>
            <p className="text-sm text-muted">
              <span className="tnum font-semibold text-ink">{unplacedCount}</span> unplaced{" "}
              {unplacedCount === 1 ? "location" : "locations"} in this warehouse. Auto-arrange puts them on the floor in
              a tidy grid, or place them one at a time from the list below.
            </p>
            <div className="flex flex-col items-stretch gap-2 sm:flex-row">
              <Button size="sm" onClick={onArrange}>
                {unplacedCount === 1 ? "Auto-arrange 1 unplaced location" : `Auto-arrange all ${unplacedCount} unplaced locations`}
              </Button>
              <Button size="sm" variant="secondary" onClick={onPlaceOneByOne}>
                Place one at a time
              </Button>
            </div>
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
