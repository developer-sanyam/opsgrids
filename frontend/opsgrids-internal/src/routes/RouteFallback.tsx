import { Skeleton } from "@opsgrids/ui";

/**
 * What a lazily loaded screen shows while its chunk downloads: the page's own shape, so nothing
 * moves when it arrives, and a status for anyone not looking at the screen.
 */
export function RouteFallback() {
  return (
    <div className="flex flex-col gap-5" role="status" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <Skeleton className="h-9 w-48 rounded-tile" />
      <Skeleton className="h-24 w-full rounded-tile" />
      <Skeleton className="h-64 w-full rounded-tile" />
    </div>
  );
}
