import { Card, Skeleton } from "@opsgrids/ui";

/**
 * Holds a list screen's shape while its chunk arrives — shared by Products, Categories and
 * Enquiries, whose loaded shape (header, toolbar, table) is the same.
 */
export function CatalogueFallback() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-10 w-64" />
      <Card className="p-4">
        <Skeleton className="h-11 w-full" />
        <Skeleton className="mt-4 h-64 w-full rounded-tile" />
      </Card>
    </div>
  );
}
