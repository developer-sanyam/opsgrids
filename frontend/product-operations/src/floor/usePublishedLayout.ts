import { getPublishedLayout } from "@/api/client";
import type { PublishedLayoutOut } from "@/api/types";
import { useAsync } from "@opsgrids/client";

/**
 * A floor's published layout, loaded once and shared by every view of that floor on the screen (the
 * 2-D plan and the 3-D scene read the same response). `null` for no floor, which resolves at once.
 */
export function usePublishedLayout(floorId: string | null) {
  return useAsync<PublishedLayoutOut | null>(
    () => (floorId === null ? Promise.resolve(null) : getPublishedLayout(floorId)),
    [floorId],
  );
}
