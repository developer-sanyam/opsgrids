import { useCallback, useEffect, useRef, useState } from "react";

interface QuietRefreshOptions<T> {
  /** Whether what is on screen is still worth re-reading. Asked of the value *shown* — the
   *  refreshed one once there is one — so the timer stops when the last poll found everything at
   *  rest, rather than running on because the first load did not. Polling a settled screen is load
   *  the server has no use for. */
  active: (shown: T) => boolean;
  intervalMs: number;
}

/**
 * Re-read what a screen already shows, in the background, without going back through `loading`.
 *
 * `useAsync.reload` re-enters the loading state — right after a write, where showing the row you
 * just changed as it was would be a lie, and wrong on a timer, where a table that flashed to
 * skeletons every few seconds would unmount every countdown and dialog on it. This keeps the page
 * on screen and swaps in the fresh one when it arrives.
 *
 * Three guarantees, each of which is a way this is usually got wrong:
 *
 * - **A fresh load wins.** A new `loaded` value (a new filter, a new page) discards whatever the
 *   background read held, and a background read still in flight *for the previous load* is
 *   dropped when it lands rather than painted over the new one.
 * - **A failed background read changes nothing.** The page on screen is good data; the next tick
 *   asks again. Replacing it with an error because a poll dropped would be worse than stale.
 * - **`refresh` is stable**, so an effect that depends on it is not re-armed every render.
 */
export function useQuietRefresh<T>(
  loaded: T | null,
  fetcher: () => Promise<T>,
  { active, intervalMs }: QuietRefreshOptions<T>,
): { shown: T | null; refresh: () => void } {
  const [refreshed, setRefreshed] = useState<T | null>(null);
  const fetcherRef = useRef(fetcher);
  const generation = useRef(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    generation.current += 1;
    setRefreshed(null);
  }, [loaded]);

  const refresh = useCallback(() => {
    const started = generation.current;
    void fetcherRef
      .current()
      .then((next) => {
        if (generation.current === started) setRefreshed(next);
      })
      .catch(() => undefined);
  }, []);

  const shown = loaded === null ? null : (refreshed ?? loaded);
  const polling = shown !== null && active(shown);

  useEffect(() => {
    if (!polling) return;
    const id = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(id);
  }, [polling, intervalMs, refresh]);

  return { shown, refresh };
}
