import { useState } from "react";

import { useAsync, useMutation } from "@opsgrids/client";

import { listAvailableStock } from "@/api/client";
import type { StockRowOut } from "@/api/types";
import { hasAvailableStock } from "@/lib/putawayForm";

export interface AvailableStock {
  status: "loading" | "success" | "error";
  error: Error | null;
  /** Every loaded bucket with something available to move, in the server's order. */
  rows: StockRowOut[];
  hasMore: boolean;
  loadMore: () => void;
  loadingMore: boolean;
  loadMoreError: Error | null;
  reload: () => void;
}

/**
 * The stock a worker can put away: the first page of `GET /inventory/stock?status=AVAILABLE`, then
 * more on demand. The endpoint filters by exact id only — there is no text search — and a worker has
 * no item list to pick an id from, so the screen filters what is loaded and pages on with the
 * server's cursor rather than pretend to search what it hasn't fetched.
 */
export function useAvailableStock(): AvailableStock {
  const first = useAsync(() => listAvailableStock(), []);
  const [more, setMore] = useState<{ rows: StockRowOut[]; next: string | null } | null>(null);

  const next = more !== null ? more.next : first.status === "success" ? first.data.next_cursor : null;

  const loadMore = useMutation(() => listAvailableStock({ cursor: next ?? undefined }), {
    onSuccess: (page) =>
      setMore((previous) => ({ rows: [...(previous?.rows ?? []), ...page.items], next: page.next_cursor })),
  });

  const loaded = first.status === "success" ? [...first.data.items, ...(more?.rows ?? [])] : [];

  return {
    status: first.status,
    error: first.error,
    rows: loaded.filter(hasAvailableStock),
    hasMore: next !== null,
    loadMore: () => void loadMore.mutate(undefined),
    loadingMore: loadMore.pending,
    loadMoreError: loadMore.error,
    reload: () => {
      setMore(null);
      loadMore.reset();
      first.reload();
    },
  };
}
