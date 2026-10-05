import { useEffect, useRef, useState } from "react";

import type { OrderCollectionOut } from "@/api/types";
import { ApiError } from "@opsgrids/client";

/** How often a pending payment is asked about, and how many visible ticks it is asked for: every
 *  3 seconds for about a minute. */
export const POLL_INTERVAL_MS = 3000;
export const POLL_TICKS = 20;

/** A payment the provider has settled one way or the other: nothing left to wait for. */
const SETTLED: ReadonlySet<OrderCollectionOut["state"]> = new Set(["captured", "failed", "cancelled", "expired"]);

export type PollOutcome = { kind: "settled"; collection: OrderCollectionOut } | { kind: "gave_up" };

/**
 * After a checkout closes with the payment still pending, ask the server — which asks the
 * provider — again every few seconds for about a minute, so the operator sees the answer without
 * pressing anything, and is told meanwhile not to take payment a second time.
 *
 * Bounded on purpose: each tick is a server-to-server status check, so it stops at a settled
 * state, after `POLL_TICKS` visible ticks, on a 4xx (an authorization or a missing payment does
 * not fix itself), or when the card unmounts. A hidden tab does not tick at all — nobody is
 * looking, and the budget is kept for when somebody is.
 */
export function usePaymentStatusPoll(
  check: (chargeId: string) => Promise<OrderCollectionOut>,
  onDone: (outcome: PollOutcome) => void,
) {
  const [watching, setWatching] = useState<string | null>(null);
  const checkRef = useRef(check);
  const doneRef = useRef(onDone);
  useEffect(() => {
    checkRef.current = check;
    doneRef.current = onDone;
  });

  useEffect(() => {
    if (watching === null) return;
    const chargeId = watching;
    let cancelled = false;
    let timer: number | undefined;
    let ticks = 0;

    const finish = (outcome: PollOutcome) => {
      if (cancelled) return;
      setWatching(null);
      doneRef.current(outcome);
    };

    const tick = async () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
        return;
      }
      ticks += 1;
      try {
        const collection = await checkRef.current(chargeId);
        if (SETTLED.has(collection.state)) {
          finish({ kind: "settled", collection });
          return;
        }
      } catch (error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          finish({ kind: "gave_up" });
          return;
        }
        // Unreachable or 5xx: the next tick asks again.
      }
      if (cancelled) return;
      if (ticks >= POLL_TICKS) {
        finish({ kind: "gave_up" });
        return;
      }
      timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };

    timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [watching]);

  return { watching, start: setWatching, stop: () => setWatching(null) };
}
