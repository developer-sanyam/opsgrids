import { Card, Skeleton } from "@opsgrids/ui";

/**
 * What shows while a lazy-loaded route's chunk downloads. Reserves roughly the shape a loaded
 * screen occupies — a header-height bar over a tall card — so the swap from fallback to the real
 * screen does not shift the page under a reader who has already started scanning it.
 */
export function RouteFallback() {
  return (
    <div className="rise flex flex-col gap-6">
      <Skeleton className="h-9 w-48 rounded-tile" />
      <Card className="p-6">
        <Skeleton className="h-64 w-full rounded-tile" />
      </Card>
    </div>
  );
}
