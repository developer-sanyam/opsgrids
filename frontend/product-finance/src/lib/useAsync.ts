import { useEffect, useState } from "react";

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "success"; data: T; error: null }
  | { status: "error"; data: null; error: Error };

/**
 * Run an async loader and track loading / success / error. `deps` re-runs it; a mounted flag
 * drops results from a superseded call so a fast filter change never flashes stale data.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: React.DependencyList,
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    status: "loading",
    data: null,
    error: null,
  });

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
  }, deps);

  return state;
}
