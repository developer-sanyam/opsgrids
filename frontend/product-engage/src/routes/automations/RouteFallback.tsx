import { Card, Skeleton } from "@/ui";

/** What shows while a lazily loaded automation screen's code arrives: a placeholder the size of
 *  the content, so the page does not jump when the screen replaces it. */
export function RouteFallback() {
  return (
    <Card className="flex flex-col gap-4 p-6" aria-busy="true">
      <span className="sr-only" role="status">
        Loading
      </span>
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-64 w-full rounded-tile" />
    </Card>
  );
}
