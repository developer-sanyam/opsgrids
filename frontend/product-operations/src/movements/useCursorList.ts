import { useCallback, useEffect, useRef, useState } from "react";

import type { CursorPage } from "@/api/movementTypes";

export type CursorListStatus = "loading" | "error" | "ready";

export interface CursorList<T> {
  rows: T[];
  status: CursorListStatus;
  /** Why the first page failed. */
  error: Error | null;
  hasMore: boolean;
  /** A "Load more" fetch is in flight. */
  loadingMore: boolean;
  /** Why the last "Load more" failed — the rows already on screen stay put. */
  moreError: Error | null;
  loadMore: () => void;
  /** Start again from the first page. */
  reload: () => void;
}

/**
 * A keyset-paginated list that grows as the person asks for more.
 *
 * The first page is fetched whenever `deps` change; "Load more" appends the server's next page and
 * leaves what is on screen alone, so a failed second page never takes the first away. A response
 * for a superseded request (a filter changed while it was in flight) is dropped, so a slow answer
 * can never overwrite a newer one. Nothing is re-sorted or re-filtered here: the server decides.
 */
export function useCursorList<T>(
  fetchPage: (cursor: string | undefined, signal: AbortSignal) => Promise<CursorPage<T>>,
  deps: readonly unknown[],
): CursorList<T> {
  const [rows, setRows] = useState<T[]>([]);
  const [status, setStatus] = useState<CursorListStatus>("loading");
  const [error, setError] = useState<Error | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);

  const fetchRef = useRef(fetchPage);
  useEffect(() => {
    fetchRef.current = fetchPage;
  });
  const generation = useRef(0);
  const moreInFlight = useRef(false);

  useEffect(() => {
    const mine = ++generation.current;
    const controller = new AbortController();
    moreInFlight.current = false;
    setStatus("loading");
    setError(null);
    setMoreError(null);
    setLoadingMore(false);
    fetchRef
      .current(undefined, controller.signal)
      .then((page) => {
        if (generation.current !== mine) return;
        setRows(page.items);
        setNextCursor(page.next_cursor);
        setStatus("ready");
      })
      .catch((thrown: unknown) => {
        if (generation.current !== mine) return;
        setError(thrown instanceof Error ? thrown : new Error(String(thrown)));
        setStatus("error");
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const loadMore = useCallback(() => {
    if (nextCursor === null || moreInFlight.current) return;
    const mine = generation.current;
    moreInFlight.current = true;
    setLoadingMore(true);
    setMoreError(null);
    const controller = new AbortController();
    fetchRef
      .current(nextCursor, controller.signal)
      .then((page) => {
        if (generation.current !== mine) return;
        setRows((previous) => [...previous, ...page.items]);
        setNextCursor(page.next_cursor);
      })
      .catch((thrown: unknown) => {
        if (generation.current !== mine) return;
        setMoreError(thrown instanceof Error ? thrown : new Error(String(thrown)));
      })
      .finally(() => {
        if (generation.current !== mine) return;
        moreInFlight.current = false;
        setLoadingMore(false);
      });
  }, [nextCursor]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { rows, status, error, hasMore: nextCursor !== null, loadingMore, moreError, loadMore, reload };
}
