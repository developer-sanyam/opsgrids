import { useCallback, useEffect, useRef, useState } from "react";
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
  /**
   * Run the loader again **in the background**: the current data stays on screen (and
   * `refreshing` is true) until the new answer replaces it.
   *
   * For a screen whose children hold state of their own that a skeleton would destroy — a
   * payment card mid-poll, a form, an error the user has not read yet. Unmounting them to show a
   * loading state loses that state for good. Before any data has loaded, and after the deps
   * change, it is an ordinary reload: there is nothing current to keep.
   */
  refresh: () => void;
  /** True while a `refresh` is in flight over data still shown. */
  refreshing: boolean;
}

function sameDeps(a: DependencyList, b: DependencyList): boolean {
  return a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
}

/**
 * Run an async loader and track loading / success / error. `deps` re-runs it; a mounted flag
 * drops results from a superseded call so a fast filter change never flashes stale data.
 *
 * This is the read half of every screen in the platform — the write half is `useMutation`. The
 * two meet at `reload` (or `refresh`), which replaces the `const [reloadKey, setReloadKey] =
 * useState(0)` nonce screens were threading through `deps` by hand.
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
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);
  const keepRef = useRef(false);
  const depsRef = useRef<DependencyList>(deps);

  const reload = useCallback(() => {
    keepRef.current = false;
    setNonce((previous) => previous + 1);
  }, []);

  const refresh = useCallback(() => {
    keepRef.current = true;
    setNonce((previous) => previous + 1);
  }, []);

  useEffect(() => {
    let active = true;
    // `state` is this render's: what is on screen when the refresh was asked for.
    const keep = keepRef.current && state.status === "success" && sameDeps(depsRef.current, deps);
    keepRef.current = false;
    depsRef.current = deps;
    if (keep) {
      setRefreshing(true);
    } else {
      setRefreshing(false);
      setState({ status: "loading", data: null, error: null });
    }
    loader()
      .then((data) => {
        if (active) {
          setRefreshing(false);
          setState({ status: "success", data, error: null });
        }
      })
      .catch((err: unknown) => {
        if (active) {
          const error = err instanceof Error ? err : new Error(String(err));
          setRefreshing(false);
          setState({ status: "error", data: null, error });
        }
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload, refresh, refreshing };
}
