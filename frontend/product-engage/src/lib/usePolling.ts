import { useEffect, useRef, useState } from "react";
import type { DependencyList } from "react";

/**
 * `useAsync`'s shape, plus what only a poll needs: `refresh` for an immediate out-of-band poll
 * (an action just taken should not wait for the interval), and `isRefreshing` so a screen can show
 * a quiet indicator instead of the loading skeleton it would show on the first load.
 *
 * There is no cross-product `usePoll` in `@opsgrids/client` yet — the inbox is the first screen
 * on the platform that polls at all, so this stays local until a second product needs the same
 * shape (ADR-0001's rule cuts the other way too: promoting a one-screen need into the shared
 * package before a second caller exists is how that package grows knobs nobody but this screen
 * turns).
 */
export type PollState<T> =
  | { status: "loading"; data: null; error: null; isRefreshing: false }
  | { status: "success"; data: T; error: null; isRefreshing: boolean }
  | { status: "error"; data: null; error: Error; isRefreshing: false };

export interface PollControls {
  refresh: () => void;
}

/**
 * Runs `loader` immediately, then again every `intervalMs` until unmounted or `deps` change.
 *
 * The loading skeleton is shown once, on the very first call: every poll after that keeps the
 * last successful page on screen while it refetches quietly, and only replaces it once the new
 * page has actually arrived — the platform's "loading state that does not shift the layout"
 * rule applies to a poll exactly as it does to the first paint. A poll that fails leaves the
 * last good page in place rather than flashing an error over live data every `intervalMs`; only
 * a failure on the *first* load reaches the error state.
 */
export function usePolling<T>(
  loader: () => Promise<T>,
  deps: DependencyList,
  intervalMs: number,
): PollState<T> & PollControls {
  const [state, setState] = useState<PollState<T>>({
    status: "loading",
    data: null,
    error: null,
    isRefreshing: false,
  });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick(isFirst: boolean) {
      if (!isFirst) {
        setState((previous) =>
          previous.status === "success" ? { ...previous, isRefreshing: true } : previous,
        );
      }
      try {
        const data = await loaderRef.current();
        if (active) setState({ status: "success", data, error: null, isRefreshing: false });
      } catch (caught: unknown) {
        if (!active) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        setState((previous) =>
          previous.status === "success"
            ? { ...previous, isRefreshing: false }
            : { status: "error", data: null, error, isRefreshing: false },
        );
      } finally {
        if (active) timer = setTimeout(() => void tick(false), intervalMs);
      }
    }

    setState({ status: "loading", data: null, error: null, isRefreshing: false });
    void tick(true);
    return () => {
      active = false;
      if (timer !== undefined) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, intervalMs]);

  return { ...state, refresh: () => setNonce((n) => n + 1) };
}
