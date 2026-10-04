import { useCallback, useEffect, useRef, useState } from "react";
import type { DependencyList } from "react";

import type { CursorPage } from "@/api/trackingTypes";

export interface CursorList<T> {
  /** `loading` until the first page lands; `error` if the first page failed; `ready` after. */
  status: "loading" | "error" | "ready";
  items: T[];
  /** The first page's failure. */
  error: Error | null;
  /** The server named another page (`next_cursor`). */
  hasMore: boolean;
  loadingMore: boolean;
  /** A "Load more" that failed. The rows already shown are kept; only the next page is missing. */
  moreError: Error | null;
  loadMore: () => void;
  /** Start again from the first page. */
  reload: () => void;
}

interface Snapshot<T> {
  status: CursorList<T>["status"];
  items: T[];
  error: Error | null;
  cursor: string | null;
  loadingMore: boolean;
  moreError: Error | null;
}

function toError(thrown: unknown): Error {
  return thrown instanceof Error ? thrown : new Error(String(thrown));
}

/**
 * A keyset-paginated list: the first page on mount and whenever `deps` change, further pages
 * appended by `loadMore` using the server's opaque `next_cursor`.
 *
 * A change of `deps` abandons whatever was in flight, so a slow response for the old filters can
 * never land on the new ones; a `loadMore` for an abandoned list is dropped the same way. A failed
 * "Load more" keeps the rows on screen and offers the retry beside them, because throwing away
 * four pages to say the fifth failed is the wrong trade.
 */
export function useCursorList<T>(
  fetchPage: (cursor: string | undefined, signal: AbortSignal) => Promise<CursorPage<T>>,
  deps: DependencyList,
): CursorList<T> {
  const [state, setState] = useState<Snapshot<T>>({
    status: "loading",
    items: [],
    error: null,
    cursor: null,
    loadingMore: false,
    moreError: null,
  });
  const [nonce, setNonce] = useState(0);

  const fetchRef = useRef(fetchPage);
  useEffect(() => {
    fetchRef.current = fetchPage;
  });
  /** The in-flight request's controller; replaced whenever the list starts over. */
  const controller = useRef<AbortController | null>(null);
  const cursorRef = useRef<string | null>(null);
  const moreInFlight = useRef(false);

  useEffect(() => {
    const abort = new AbortController();
    controller.current?.abort();
    controller.current = abort;
    moreInFlight.current = false;
    cursorRef.current = null;
    setState({ status: "loading", items: [], error: null, cursor: null, loadingMore: false, moreError: null });
    fetchRef
      .current(undefined, abort.signal)
      .then((page) => {
        if (abort.signal.aborted) return;
        cursorRef.current = page.next_cursor;
        setState({
          status: "ready",
          items: page.items,
          error: null,
          cursor: page.next_cursor,
          loadingMore: false,
          moreError: null,
        });
      })
      .catch((thrown: unknown) => {
        if (abort.signal.aborted) return;
        setState({
          status: "error",
          items: [],
          error: toError(thrown),
          cursor: null,
          loadingMore: false,
          moreError: null,
        });
      });
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const loadMore = useCallback(() => {
    const cursor = cursorRef.current;
    const abort = controller.current;
    if (cursor === null || abort === null || moreInFlight.current) return;
    moreInFlight.current = true;
    setState((previous) => ({ ...previous, loadingMore: true, moreError: null }));
    fetchRef
      .current(cursor, abort.signal)
      .then((page) => {
        if (abort.signal.aborted) return;
        moreInFlight.current = false;
        cursorRef.current = page.next_cursor;
        setState((previous) => ({
          ...previous,
          items: [...previous.items, ...page.items],
          cursor: page.next_cursor,
          loadingMore: false,
        }));
      })
      .catch((thrown: unknown) => {
        if (abort.signal.aborted) return;
        moreInFlight.current = false;
        setState((previous) => ({ ...previous, loadingMore: false, moreError: toError(thrown) }));
      });
  }, []);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return {
    status: state.status,
    items: state.items,
    error: state.error,
    hasMore: state.cursor !== null,
    loadingMore: state.loadingMore,
    moreError: state.moreError,
    loadMore,
    reload,
  };
}
