import { useId } from "react";
import type { Ref } from "react";

import type { LocationOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { Button, Card, InlineAlert, Skeleton, Tag } from "@/ui";
import { ChevronDown } from "@opsgrids/ui";

interface UnplacedLocationsCardProps {
  /** The locations of this warehouse that are not on the draft, in the order to list them. */
  locations: LocationOut[];
  /** Every location in the warehouse — tells "all placed" from "none exist". */
  totalLocations: number;
  status: "loading" | "error" | "success";
  error?: string | undefined;
  onRetry: () => void;
  open: boolean;
  onToggle: () => void;
  onPlace: (location: LocationOut) => void;
  /** The header's own button — what "Place one at a time" sends focus to. */
  headerRef?: Ref<HTMLButtonElement> | undefined;
}

/**
 * The locations not yet on the floor, one "Place" each. The header is the control: it carries the
 * count, so the number is visible without scrolling, and it folds the list away — on a phone the
 * list is the longest thing on the page, and a list that scrolls inside a short box on a page that
 * scrolls is a scroll trap. Beside the canvas on a wide screen the list keeps a height of its own.
 */
export function UnplacedLocationsCard({
  locations,
  totalLocations,
  status,
  error,
  onRetry,
  open,
  onToggle,
  onPlace,
  headerRef,
}: UnplacedLocationsCardProps) {
  const listId = useId();
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

      <div id={listId} hidden={!open}>
        {status === "loading" ? <Skeleton className="h-24 w-full" /> : null}
        {status === "error" ? (
          <InlineAlert
            tone="danger"
            title="Couldn't load locations"
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
          locations.length === 0 ? (
            <p className="text-sm text-muted">
              {totalLocations === 0
                ? "This warehouse has no locations yet."
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
                    aria-describedby={`${listId}-${location.id}`}
                    onClick={() => onPlace(location)}
                  >
                    Place
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>
    </Card>
  );
}
