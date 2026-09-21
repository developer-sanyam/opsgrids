import { useCallback, useEffect, useState } from "react";
import type { DependencyList } from "react";

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "success"; data: T; error: null }
  | { status: "error"; data: null; error: Error };

export interface AsyncControls {
  /**
   * Run the loader again with the same deps — what a screen calls after a write lands.
   *
   * It re-enters the loading state rather than holding the stale rows, because a list that
   * shows the row you just deleted while it refetches is lying for as long as that takes.
   */
  reload: () => void;
}

/**
 * Run an async loader and track loading / success / error. `deps` re-runs it; a mounted flag
 * drops results from a superseded call so a fast filter change never flashes stale data.
 *
 * This is the read half of every screen in the platform — the write half is `useMutation`. The
 * two meet at `reload`, which replaces the `const [reloadKey, setReloadKey] = useState(0)` nonce
 * screens were threading through `deps` by hand.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: DependencyList,
): AsyncState<T> & AsyncControls {
  const [state, setState] = useState<AsyncState<T>>({
    status: "loading",
    data: null,
    error: null,
  });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => {
    setNonce((previous) => previous + 1);
  }, []);

  useEffect(() => {
    let active = true;
    setState({ status: "loading", data: null, error: null });
    loader()
      .then((data) => {
        if (active) setState({ status: "success", data, error: null });
      })
      .catch((err: unknown) => {
        if (active) {
          const error = err instanceof Error ? err : new Error(String(err));
          setState({ status: "error", data: null, error });
        }
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload };
}
