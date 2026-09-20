import { Card, Skeleton } from "@opsgrids/ui";

/**
 * Holds the delivery screen's shape while its chunk arrives.
 *
 * It lives in its own file rather than in `index.tsx` on purpose: the router imports it eagerly to
 * use as the Suspense fallback, and a fallback that lived inside the lazy chunk would be
 * unavailable exactly when it is needed.
 *
 * The heights match the loaded screen — header, section rail, content card — so the route lands
 * once instead of jumping when the chunk and then the data arrive.
 */
export function DeliveryFallback() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-9 w-80 rounded-full" />
      <Card className="p-4">
        <Skeleton className="h-64 w-full rounded-tile" />
      </Card>
    </div>
  );
}
