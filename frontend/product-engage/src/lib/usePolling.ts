import { useEffect, useRef, useState } from "react";
import type { DependencyList } from "react";

import { ApiError } from "@opsgrids/client";

/**
 * `useAsync`'s shape, plus what only a poll needs: `refresh` for an immediate out-of-band poll
 * (an action just taken should not wait for the interval), `isRefreshing` so a screen can show
 * a quiet indicator instead of the loading skeleton it would show on the first load, and
 * `stopped` so a screen can say the data on it is no longer being kept current. `refresh` re-reads
 * *without* clearing what is on screen — an action just taken should not blank the list it is
 * about.
 *
 * There is no cross-product `usePoll` in `@opsgrids/client` yet — the inbox is the first screen
 * on the platform that polls at all, so this stays local until a second product needs the same
 * shape (ADR-0001's rule cuts the other way too: promoting a one-screen need into the shared
 * package before a second caller exists is how that package grows knobs nobody but this screen
 * turns).
 */
export type PollState<T> =
  | { status: "loading"; data: null; error: null; isRefreshing: false; stopped: false }
  | { status: "success"; data: T; error: null; isRefreshing: boolean; stopped: boolean }
  | { status: "error"; data: null; error: Error; isRefreshing: false; stopped: boolean };

export interface PollControls {
  refresh: () => void;
}

/** Never longer than eight times the caller's own interval — a poll that backed off forever on a
 *  flaky connection would look like the screen had simply stopped working. */
const MAX_BACKOFF_MULTIPLIER = 8;

function isAuthFailure(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

/**
 * Runs `loader` immediately, then again every `intervalMs` until unmounted or `deps` change. With
 * `intervalMs: null` it runs exactly once and never schedules another — a screen whose content
 * only sometimes changes underneath it (a campaign is live while it is `sending` and settled
 * afterwards) can ask for both behaviours from one hook, because hooks cannot be called
 * conditionally.
 *
 * A poll does three things a naive `setInterval` does not, each closing a real failure mode of a
 * polling screen left open:
 *
 * - **Stops outright on a 401/403.** The platform's own rule — "never retry a 4xx; an
 *   authorization failure does not fix itself" — applies to a poll exactly as it does to a single
 *   request. Retrying one every `intervalMs` forever is not resilience, it is a background tab
 *   quietly hammering an endpoint it will never be let into again. `stopped` says so, and **the
 *   last good data stays**: figures that were true a minute ago are not replaced by nothing, and a
 *   screen that keeps them owes the person looking a sentence saying they are no longer current.
 * - **Backs off on every other failure**, doubling the interval up to `MAX_BACKOFF_MULTIPLIER`×
 *   and resetting the moment a poll succeeds — so a gateway having a bad minute is not answered
 *   with a poll hammering it every `intervalMs` regardless.
 * - **Pauses while the tab is hidden** and polls once, immediately, the moment it becomes visible
 *   again — a background tab has no one reading its live data, and a laptop left open overnight
 *   on this screen should not spend that whole time polling a server nobody is looking at.
 *
 * The loading skeleton is shown once, on the very first call: every poll after that keeps the
 * last successful page on screen while it refetches quietly, and only replaces it once the new
 * page has actually arrived — the platform's "loading state that does not shift the layout" rule
 * applies to a poll exactly as it does to the first paint. A poll that fails leaves the last good
 * page in place rather than flashing an error over live data every `intervalMs`; only a failure
 * on the *first* load reaches the error state.
 */
export function usePolling<T>(
  loader: () => Promise<T>,
  deps: DependencyList,
  intervalMs: number | null,
): PollState<T> & PollControls {
  const [state, setState] = useState<PollState<T>>({
    status: "loading",
    data: null,
    error: null,
    isRefreshing: false,
    stopped: false,
  });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const stateRef = useRef(state);
  stateRef.current = state;
  /* Set by `refresh()` and read by the effect it re-runs. A refresh is *not* a change of what is
     being read — the deps did not move — so it must not throw away what is on screen. */
  const softRefresh = useRef(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let backoff = 1;
    /* Every poll is numbered as it is issued, and an answer is dropped if a *later* poll has
       already been answered. Two polls can be in flight at once — the timer's and the one a tab
       becoming visible again fires — and nothing orders their answers: the older one arriving last
       would put old data back over new. */
    let issued = 0;
    let applied = 0;

    function clearTimer() {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
    }

    function scheduleNext() {
      if (intervalMs === null || !active || stopped || document.hidden) return;
      clearTimer();
      timer = setTimeout(() => void tick(false), intervalMs * backoff);
    }

    async function tick(isFirst: boolean) {
      const mine = ++issued;
      let superseded = false;
      if (!isFirst) {
        setState((previous) =>
          previous.status === "success" ? { ...previous, isRefreshing: true } : previous,
        );
      }
      try {
        const data = await loaderRef.current();
        if (!active) return;
        if (mine < applied) {
          superseded = true;
          return;
        }
        applied = mine;
        backoff = 1;
        setState({ status: "success", data, error: null, isRefreshing: false, stopped: false });
      } catch (caught: unknown) {
        if (!active) return;
        if (mine < applied) {
          superseded = true;
          return;
        }
        applied = mine;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        if (isAuthFailure(error)) {
          stopped = true;
          setState((previous) =>
            previous.status === "success"
              ? { ...previous, isRefreshing: false, stopped: true }
              : { status: "error", data: null, error, isRefreshing: false, stopped: true },
          );
          return;
        }
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MULTIPLIER);
        setState((previous) =>
          previous.status === "success"
            ? { ...previous, isRefreshing: false }
            : { status: "error", data: null, error, isRefreshing: false, stopped: false },
        );
      } finally {
        if (!superseded) scheduleNext();
      }
    }

    function onVisibilityChange() {
      if (intervalMs === null) return;
      if (document.hidden) {
        clearTimer();
      } else if (!stopped) {
        // Caught up the moment someone looks back at the tab, rather than waiting out
        // whatever fraction of the interval was left when it was hidden.
        void tick(false);
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    const soft = softRefresh.current && stateRef.current.status === "success";
    softRefresh.current = false;
    if (soft) {
      // The last good data stays on screen — and stays if this read is refused too. `stopped`
      // clears only when a read succeeds: a person asked for this one, so it is not the automatic
      // retry of a refusal that the platform's rule forbids.
      void tick(false);
    } else {
      setState({ status: "loading", data: null, error: null, isRefreshing: false, stopped: false });
      void tick(true);
    }
    return () => {
      active = false;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, intervalMs]);

  return {
    ...state,
    refresh: () => {
      softRefresh.current = true;
      setNonce((n) => n + 1);
    },
  };
}
