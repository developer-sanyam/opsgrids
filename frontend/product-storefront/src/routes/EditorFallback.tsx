import { Card, Skeleton } from "@opsgrids/ui";

/**
 * Holds the editor's shape while its chunk arrives.
 *
 * The editor is the app's one lazily-loaded route, so there are two waits before a merchant sees
 * their page: the chunk, then the store. This covers the first at the same height the editor's own
 * skeleton covers the second, so the route lands once rather than jumping twice.
 */
export function EditorFallback() {
  return (
    <div className="flex flex-col gap-5">
      <Skeleton className="h-10 w-64" />
      <Card className="lg:h-[calc(100dvh-11rem)]">
        <Skeleton className="h-96 w-full rounded-card lg:h-full" />
      </Card>
    </div>
  );
}
