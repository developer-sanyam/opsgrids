import { useEffect, useState } from "react";

import { useMutation } from "@opsgrids/client";

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
  /** The latest refresh failed, so `rows` is the last list that did load: it may be out of date. */
  stale: boolean;
  reload: () => void;
}

interface Loaded {
  rows: StockRowOut[];
  next: string | null;
}

/**
 * The stock a worker can put away: the first page of `GET /inventory/stock?status=AVAILABLE`, then
 * more on demand. The endpoint filters by exact id only — there is no text search — and a worker has
 * no item list to pick an id from, so the screen filters what is loaded and pages on with the
 * server's cursor rather than pretend to search what it hasn't fetched.
 *
 * A refresh that fails does not throw away a list that had loaded: a worker on a weak signal who
 * has just queued one put-away must still be able to enter the next line of the same pallet. The
 * list on screen is then the last good one, `stale`, until a refresh works. While a refresh is in
 * flight the old rows are not shown — a quantity that is about to change is not one to pick from.
 * Only the very first load, with nothing to fall back on, is an error.
 */
export function useAvailableStock(): AvailableStock {
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  const refresh = useMutation(() => listAvailableStock(), {
    onSuccess: (page) => setLoaded({ rows: page.items, next: page.next_cursor }),
  });
  const loadMore = useMutation((cursor: string) => listAvailableStock({ cursor }), {
    // A refresh that landed first replaced the list this page was the continuation of.
    onSuccess: (page, cursor) =>
      setLoaded((previous) =>
        previous?.next === cursor ? { rows: [...previous.rows, ...page.items], next: page.next_cursor } : previous,
      ),
  });

  const refreshNow = refresh.mutate;
  useEffect(() => {
    void refreshNow(undefined);
  }, [refreshNow]);

  const status = refresh.pending ? "loading" : loaded !== null ? "success" : refresh.error ? "error" : "loading";

  return {
    status,
    error: loaded === null ? refresh.error : null,
    rows: (loaded?.rows ?? []).filter(hasAvailableStock),
    hasMore: loaded?.next != null,
    loadMore: () => {
      if (loaded?.next) void loadMore.mutate(loaded.next);
    },
    loadingMore: loadMore.pending,
    loadMoreError: loadMore.error,
    stale: !refresh.pending && loaded !== null && refresh.error !== null,
    reload: () => {
      loadMore.reset();
      void refresh.mutate(undefined);
    },
  };
}
