/**
 * The put-away queue's context and hook.
 *
 * Kept in a `.ts` file with no component export so React Fast Refresh stays happy — the provider
 * lives next door in `PutawayQueueProvider.tsx`, the same split `worker/context.ts` makes.
 */
import { createContext, useContext } from "react";

import type { PutawayIn } from "@/api/types";
import type { PutawayQueueEntry, PutawaySummary } from "@/lib/putawayQueue";
import type { PutawayOutcome } from "@/lib/putawaySync";

export interface PutawayQueueControls {
  /** The signed-in user's entries, oldest first — waiting and parked. */
  entries: readonly PutawayQueueEntry[];
  /** Waiting to sync (will be retried). */
  pending: number;
  /** Refused by the server for good: held for the worker to read and discard. */
  attention: readonly PutawayQueueEntry[];
  online: boolean;
  syncing: boolean;
  /** `false` when the browser would not store the queue: closing the app loses what is waiting. */
  persistent: boolean;
  /**
   * Confirm a put-away. The request is frozen into the queue first, so a crash or a dropped
   * connection mid-request loses nothing, then sent now if the phone believes it is online.
   * Resolves with what became of it: synced, still queued, or refused (in which case it has left
   * the queue and the screen shows why).
   */
  submit: (input: { body: PutawayIn; summary: PutawaySummary }) => Promise<PutawayOutcome>;
  /** Try the queue now, by hand. */
  syncNow: () => void;
  /** Throw a parked entry away. */
  discard: (idempotencyKey: string) => void;
}

export const PutawayQueueContext = createContext<PutawayQueueControls | null>(null);

export function usePutawayQueue(): PutawayQueueControls {
  const value = useContext(PutawayQueueContext);
  if (value === null) throw new Error("usePutawayQueue must be used within a <PutawayQueueProvider>");
  return value;
}
