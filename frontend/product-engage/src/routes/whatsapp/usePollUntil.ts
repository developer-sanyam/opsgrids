import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@opsgrids/client";

/**
 * `loading` until the first answer; `polling` while it is not yet final; `settled` once `isFinal`
 * says so; `stale` when a later read failed but an earlier answer is still on screen; `timedOut`
 * when the answer was still not final after `maxMs`; `error` when there has never been an answer.
 */
export type PollUntilState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "polling" | "settled" | "timedOut"; data: T; error: null }
  | { status: "stale"; data: T; error: Error }
  | { status: "error"; data: null; error: Error };

export interface PollUntilOptions<T> {
  loader: () => Promise<T>;
  /** True when the answer will not change any more and polling should stop. */
  isFinal: (data: T) => boolean;
  intervalMs: number;
  /** Stop asking after this long, so a job that never finishes does not poll for ever. */
  maxMs: number;
}

/** A failed poll waits longer each time, up to this many intervals, and resets on success. */
const MAX_BACKOFF = 8;
/** A read that keeps failing is given up on after this many in a row. */
const MAX_FAILURES = 6;

function isClientError(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

/**
 * Ask until the answer is final. The setup screen polls a background job with it, and the number
 * chooser polls until Meta's numbers have arrived.
 *
 * - **A 4xx is never retried.** An authorization failure or a 404 does not fix itself; the poll
 *   stops and the screen says so (the platform's rule, and `usePolling`'s).
 * - **Anything else backs off**, and keeps the last good answer on screen, marked `stale`, so a
 *   flaky network reads as "couldn't refresh" instead of a blank screen or a false failure.
 * - **It stops after `maxMs`** and says `timedOut`, with `retry()` to start again.
 * - Pauses while the tab is hidden and asks once as soon as it is visible again.
 */
export function usePollUntil<T>(options: PollUntilOptions<T>): PollUntilState<T> & { retry: () => void } {
  const [state, setState] = useState<PollUntilState<T>>({ status: "loading", data: null, error: null });
  const [nonce, setNonce] = useState(0);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let finished = false;
    const startedAt = Date.now();

    function clearTimer() {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
    }

    function schedule() {
      if (!active || finished || document.hidden) return;
      clearTimer();
      const { intervalMs } = optionsRef.current;
      const wait = intervalMs * Math.min(2 ** failures, MAX_BACKOFF);
      timer = setTimeout(() => void tick(), wait);
    }

    async function tick() {
      const { loader, isFinal, maxMs } = optionsRef.current;
      try {
        const data = await loader();
        if (!active) return;
        failures = 0;
        if (isFinal(data)) {
          finished = true;
          setState({ status: "settled", data, error: null });
          return;
        }
        if (Date.now() - startedAt >= maxMs) {
          finished = true;
          setState({ status: "timedOut", data, error: null });
          return;
        }
        setState({ status: "polling", data, error: null });
      } catch (caught: unknown) {
        if (!active) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        failures += 1;
        if (isClientError(error) || failures >= MAX_FAILURES) {
          finished = true;
          setState((previous) =>
            previous.data === null
              ? { status: "error", data: null, error }
              : { status: "stale", data: previous.data, error },
          );
          return;
        }
        setState((previous) =>
          previous.data === null
            ? { status: "loading", data: null, error: null }
            : { status: "stale", data: previous.data, error },
        );
      }
      schedule();
    }

    function onVisibilityChange() {
      if (finished) return;
      if (document.hidden) clearTimer();
      else void tick();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    // A retry keeps what is on screen while it asks again, so the step list does not blank.
    setState((previous) =>
      previous.data === null
        ? { status: "loading", data: null, error: null }
        : { status: "polling", data: previous.data, error: null },
    );
    void tick();
    return () => {
      active = false;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [nonce]);

  const retry = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, retry };
}
