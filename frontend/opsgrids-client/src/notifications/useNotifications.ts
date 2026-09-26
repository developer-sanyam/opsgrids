/**
 * The caller's own in-app notification feed, as a hook a bell icon or a notifications panel can
 * mount directly. Not wired into any shell here — this file only adds the hook.
 *
 * `markRead`/`markAllRead` update `items` and `unreadCount` optimistically so a click feels
 * instant rather than waiting on a round trip. A failed write is not rolled back onto the list —
 * the next `refresh()` (or the next page) reconciles it, the same "eventual truth over a
 * transient badge count" trade `usePlatformModules` and `PermissionProvider` already make
 * elsewhere in this package. `error` is still set on a failed mark-read so a caller can surface
 * it if it wants to.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMyNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "./api";

export interface UseNotificationsOptions {
  /** Page size for `GET /notifications/me`. Defaults to the service's own default (25). */
  limit?: number | undefined;
  /** Only ever load unread notifications. Default false — the full feed. */
  unreadOnly?: boolean | undefined;
}

export interface UseNotificationsResult {
  items: NotificationItem[];
  unreadCount: number;
  /** True only while the first page of the current filter is loading. */
  loading: boolean;
  /** True while `loadMore` has a request in flight. */
  loadingMore: boolean;
  /** The last failure from any of the four calls this hook makes, or `null`. */
  error: Error | null;
  /** Whether `loadMore` would fetch another page. */
  hasMore: boolean;
  /** Reload from the first page — after the panel opens, or on a timer the caller owns. */
  refresh: () => void;
  /** Append the next page. A no-op while the first page is loading, a page is already in
   *  flight, or there is nothing more. */
  loadMore: () => void;
  /** Mark one notification read, optimistically. Idempotent, like the endpoint it calls. */
  markRead: (id: string) => Promise<void>;
  /** Mark every unread notification read, optimistically. */
  markAllRead: () => Promise<void>;
}

function toError(thrown: unknown): Error {
  return thrown instanceof Error ? thrown : new Error(String(thrown));
}

export function useNotifications(options: UseNotificationsOptions = {}): UseNotificationsResult {
  const { limit, unreadOnly = false } = options;

  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // A plain mirror of `items`, read synchronously by `markRead` below. A `setState` updater's
  // body is not guaranteed to run before the next line of the caller — React may defer it to the
  // commit it belongs to — so deciding "was this one unread" by writing to a variable from
  // inside `setItems(prev => ...)` and reading it right after is a race, not a fact. This ref is
  // always current by the time an event handler runs.
  const itemsRef = useRef(items);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchMyNotifications({ limit, unreadOnly })
      .then((page) => {
        if (!mounted.current) return;
        setItems(page.items);
        setUnreadCount(page.unread_count);
        setNextCursor(page.next_cursor);
      })
      .catch((thrown: unknown) => {
        if (mounted.current) setError(toError(thrown));
      })
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    // `limit`/`unreadOnly` are read fresh each call; no ref indirection is needed since this
    // callback is only ever re-created when one of them actually changes.
  }, [limit, unreadOnly]);

  useEffect(() => {
    load();
  }, [load]);

  const loadMore = useCallback(() => {
    if (loading || loadingMore || nextCursor === null) return;
    setLoadingMore(true);
    fetchMyNotifications({ limit, unreadOnly, cursor: nextCursor })
      .then((page) => {
        if (!mounted.current) return;
        setItems((previous) => [...previous, ...page.items]);
        setUnreadCount(page.unread_count);
        setNextCursor(page.next_cursor);
      })
      .catch((thrown: unknown) => {
        if (mounted.current) setError(toError(thrown));
      })
      .finally(() => {
        if (mounted.current) setLoadingMore(false);
      });
  }, [limit, unreadOnly, nextCursor, loading, loadingMore]);

  const markRead = useCallback(
    async (id: string): Promise<void> => {
      const current = itemsRef.current.find((candidate) => candidate.id === id);
      // Known-read locally, so the server already agrees — nothing to send.
      if (current !== undefined && current.read_at !== null) return;

      if (current !== undefined) {
        // `unreadOnly` filters to unread rows server-side, so a row just marked read no longer
        // belongs on this page — dropping it here is what keeps the list matching what a
        // `refresh()` would show without waiting for one.
        const now = new Date().toISOString();
        setItems((previous) =>
          unreadOnly
            ? previous.filter((item) => item.id !== id)
            : previous.map((item) => (item.id === id ? { ...item, read_at: now } : item)),
        );
        setUnreadCount((previous) => Math.max(0, previous - 1));
      }
      // `current === undefined` — an id this hook's own list does not (or no longer) hold. Still
      // worth sending: the caller presumably has a reason to believe it exists, and the endpoint
      // is idempotent either way, but there is no local row to update optimistically.

      try {
        await markNotificationRead(id);
      } catch (thrown: unknown) {
        if (mounted.current) setError(toError(thrown));
      }
    },
    [unreadOnly],
  );

  const markAllRead = useCallback(async (): Promise<void> => {
    const now = new Date().toISOString();
    setItems((previous) =>
      unreadOnly ? [] : previous.map((item) => (item.read_at === null ? { ...item, read_at: now } : item)),
    );
    setUnreadCount(0);

    try {
      await markAllNotificationsRead();
    } catch (thrown: unknown) {
      if (mounted.current) setError(toError(thrown));
    }
  }, [unreadOnly]);

  return {
    items,
    unreadCount,
    loading,
    loadingMore,
    error,
    hasMore: nextCursor !== null,
    refresh: load,
    loadMore,
    markRead,
    markAllRead,
  };
}
