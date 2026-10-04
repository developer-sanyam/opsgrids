import { useEffect, useRef, useState } from "react";
import type { DependencyList } from "react";

import type { CursorPage } from "@/api/types";
import { useAsync } from "@opsgrids/client";

interface Continuation<T> {
  /** The first page these rows continue; a different first page (a new filter) discards them. */
  base: CursorPage<T>;
  items: T[];
  next: string | null;
  error: Error | null;
}

/**
 * A keyset-paginated list with a "Load more": the first page loads like any other read (and
 * re-loads when `deps` change); later pages append under it. A failure loading more keeps the rows
 * already shown and offers a retry rather than replacing the list with an error.
 */
export function useCursorList<T>(load: (cursor: string | null) => Promise<CursorPage<T>>, deps: DependencyList) {
  const loadRef = useRef(load);
  // Declared before `useAsync`'s own effect, so the ref is current when the first page loads.
  useEffect(() => {
    loadRef.current = load;
  });

  const first = useAsync(() => loadRef.current(null), deps);
  const [continuation, setContinuation] = useState<Continuation<T> | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const base = first.status === "success" ? first.data : null;
  const active = base !== null && continuation?.base === base ? continuation : null;
  const items = base === null ? [] : [...base.items, ...(active?.items ?? [])];
  const nextCursor = base === null ? null : active ? active.next : base.next_cursor;

  async function loadMore() {
    if (base === null || nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    const shown = active ?? { base, items: [], next: nextCursor, error: null };
    try {
      const page = await loadRef.current(nextCursor);
      setContinuation({ base, items: [...shown.items, ...page.items], next: page.next_cursor, error: null });
    } catch (thrown: unknown) {
      setContinuation({ ...shown, error: thrown instanceof Error ? thrown : new Error(String(thrown)) });
    } finally {
      setLoadingMore(false);
    }
  }

  return {
    status: first.status,
    error: first.error,
    reload: first.reload,
    items,
    hasMore: nextCursor !== null,
    loadMore,
    loadingMore,
    loadMoreError: active?.error ?? null,
  };
}
