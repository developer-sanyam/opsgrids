import { useEffect, useId, useRef } from "react";
import type { RefObject } from "react";

import type { LocationOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import type { ElsewhereLocation } from "@/lib/floorLayoutEditor";
import { Button, Card, InlineAlert, Skeleton, Tag } from "@/ui";
import { ChevronDown } from "@opsgrids/ui";

/** Where keyboard focus goes after a location is placed: the Place button of this location, or
 *  the list's header when nothing is left in it. `nonce` makes the same target a new request. */
export interface ListFocusRequest {
  locationId: string | null;
  nonce: number;
}

interface UnplacedLocationsCardProps {
  /** The locations of this warehouse that are on no floor and not on the draft, in the order to list them. */
  locations: LocationOut[];
  /** Locations another floor's published layout already holds — listed apart, placed only deliberately. */
  elsewhere: ElsewhereLocation[];
  /** Every location in the warehouse — tells "all placed" from "none exist". */
  totalLocations: number;
  status: "loading" | "error" | "success";
  /** What failed when `status` is `error`: the locations themselves, or which floors hold which. */
  problem: "locations" | "floors";
  error?: string | undefined;
  onRetry: () => void;
  open: boolean;
  onToggle: () => void;
  onPlace: (location: LocationOut) => void;
  onPlaceElsewhere: (entry: ElsewhereLocation) => void;
  focusRequest: ListFocusRequest | null;
  /** The header's own button — what "Place one at a time" sends focus to. */
  headerRef: RefObject<HTMLButtonElement | null>;
}

/**
 * The locations not yet on the floor, one "Place" each. The header is the control: it carries the
 * count, so the number is visible without scrolling, and it folds the list away — on a phone the
 * list is the longest thing on the page, and a list that scrolls inside a short box on a page that
 * scrolls is a scroll trap. Beside the canvas on a wide screen the list keeps a height of its own.
 *
 * Locations that are already on another floor are not in that list. They follow it, under the
 * floor each is on, with a button that says what it does: publishing this floor with one of them
 * puts it on this floor as well.
 *
 * Placing a location removes its button, so focus is handed on rather than lost: to the next
 * button in the list, or to the header when the list is empty (see `focusRequest`).
 */
export function UnplacedLocationsCard({
  locations,
  elsewhere,
  totalLocations,
  status,
  problem,
  error,
  onRetry,
  open,
  onToggle,
  onPlace,
  onPlaceElsewhere,
  focusRequest,
  headerRef,
}: UnplacedLocationsCardProps) {
  const listId = useId();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focusRequest === null) return;
    const target =
      focusRequest.locationId === null
        ? headerRef.current
        : (Array.from(listRef.current?.querySelectorAll<HTMLElement>("[data-place-for]") ?? []).find(
            (button) => button.dataset.placeFor === focusRequest.locationId,
          ) ?? headerRef.current);
    target?.focus({ preventScroll: true });
  }, [focusRequest, headerRef]);

  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="text-section">
        <button
          ref={headerRef}
          type="button"
          aria-expanded={open}
          aria-controls={listId}
          onClick={onToggle}
          className="flex w-full items-center justify-between gap-2 text-left"
        >
          <span className="flex items-center gap-2">
            Unplaced locations
            {status === "success" ? <Tag className="tnum">{locations.length}</Tag> : null}
          </span>
          <ChevronDown
            aria-hidden
            className={`size-4 shrink-0 text-muted transition-transform duration-(--duration-quick) ${open ? "rotate-180" : ""}`}
          />
        </button>
      </h2>

      <div id={listId} ref={listRef} hidden={!open}>
        <div className="flex flex-col gap-4">
          {status === "loading" ? <Skeleton className="h-24 w-full" /> : null}
          {status === "error" ? (
            <InlineAlert
              tone="danger"
              title={problem === "floors" ? "Couldn't check the other floors" : "Couldn't load locations"}
              action={
                <Button size="sm" variant="secondary" onClick={onRetry}>
                  Try again
                </Button>
              }
            >
              {error}
            </InlineAlert>
          ) : null}
          {status === "success" ? (
            <>
              {locations.length === 0 ? (
                <p className="text-sm text-muted">
                  {totalLocations === 0
                    ? "This warehouse has no locations yet."
                    : elsewhere.length > 0
                      ? "Nothing is waiting: every other location in this warehouse is on another floor."
                      : "Every location in this warehouse is on the floor."}
                </p>
              ) : (
                <ul className="flex flex-col gap-1 xl:max-h-96 xl:overflow-auto">
                  {locations.map((location) => (
                    <li key={location.id} className="flex items-center justify-between gap-2 text-sm">
                      <span id={`${listId}-${location.id}`}>
                        <span className="tnum font-semibold text-ink">{location.code}</span>{" "}
                        <span className="text-ink-soft">{LOCATION_KIND_LABEL[location.kind]}</span>
                      </span>
                      <Button
                        size="sm"
                        variant="secondary"
                        data-place-for={location.id}
                        aria-describedby={`${listId}-${location.id}`}
                        onClick={() => onPlace(location)}
                      >
                        Place
                      </Button>
                    </li>
                  ))}
                </ul>
              )}

              {elsewhere.length > 0 ? (
                <section aria-label="Locations on other floors" className="flex flex-col gap-2 border-t border-line pt-3">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
                    On other floors
                    <Tag className="tnum">{elsewhere.length}</Tag>
                  </h3>
                  <p className="text-caption text-muted">
                    Already on a published layout. Placing one here as well means it is on this floor once this layout is
                    published.
                  </p>
                  <ul className="flex flex-col gap-1 xl:max-h-72 xl:overflow-auto">
                    {elsewhere.map((entry) => (
                      <li key={entry.location.id} className="flex items-center justify-between gap-2 text-sm">
                        <span id={`${listId}-${entry.location.id}`} className="flex min-w-0 flex-col">
                          <span className="tnum font-semibold text-ink">{entry.location.code}</span>
                          <span className="text-caption text-ink-soft">{entry.floorTitle}</span>
                        </span>
                        <Button
                          size="sm"
                          variant="secondary"
                          className="shrink-0 whitespace-nowrap"
                          data-place-for={entry.location.id}
                          aria-describedby={`${listId}-${entry.location.id}`}
                          onClick={() => onPlaceElsewhere(entry)}
                        >
                          Place here too
                        </Button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
