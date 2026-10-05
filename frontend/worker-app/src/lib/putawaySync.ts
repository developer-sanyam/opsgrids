/**
 * One pass over the put-away queue: send each waiting entry, in the order it was confirmed, with
 * its own idempotency key. No React in here — `PutawayQueueProvider` decides when a pass runs;
 * this decides what each answer means.
 *
 * - **Sent and answered 2xx** (201, or 200 for a replay of a key the server already holds): the
 *   entry, coordinates and all, is dropped.
 * - **No answer, or a server error** (network failure, timeout, 5xx, 408, 429): the entry stays
 *   exactly as it was and goes again later under the same key. A network failure ends the pass —
 *   the next entry would fail the same way — while a 5xx moves on, so one entry the server chokes
 *   on cannot hold every other worker's put-away behind it.
 * - **Refused for good** (any other 4xx — 404 unknown bin, 409 reserved stock or a key reused for
 *   a different body, 422 a rule): retrying cannot change the answer (frontend/CLAUDE.md: never
 *   retry a 4xx). The entry is parked for a person to read and discard, and the queue carries on.
 *   The one entry the worker is looking at, submitted a moment ago, is not parked: its refusal
 *   goes straight back to the screen so the bin can be rescanned, and it leaves the queue. That is
 *   only so while the screen is still there to show it — a worker who has walked away (the request
 *   can take half a minute on a weak signal) would otherwise lose the refusal along with the entry,
 *   so then it is parked like any other and the attention banner tells them.
 * - **Gone from the queue before its turn** (signed out and purged, or discarded by hand while the
 *   pass was running): not sent. The pass re-reads the live queue before every send, so it never
 *   puts on the wire something the worker has since thrown away. The one request already in flight
 *   when that happens cannot be recalled.
 * - **401**: the session is gone. The gateway client has already tried a silent refresh. The entry
 *   is untouched and the pass stops; it syncs when the same person signs back in.
 */

import { ApiError, fieldError, problemMessage } from "@opsgrids/client";

import { postPutaway } from "@/api/client";
import type { PutawayOut } from "@/api/types";
import { getPutawayQueue, isOwnedBy, parkPutaway, removePutaway } from "@/lib/putawayQueue";
import type { PutawayOwner, PutawayQueueEntry } from "@/lib/putawayQueue";

/** A request that has not answered in this long is given up on and retried under the same key —
 *  a phone on a failing signal can otherwise hold a request open for minutes and with it the pass. */
const ATTEMPT_TIMEOUT_MS = 30_000;

/** Which kind of problem an error is, for deciding whether to try again. */
export type FailureClass = "unauthorized" | "network" | "retry" | "reject";

export function classifyFailure(error: unknown): FailureClass {
  // Anything that is not an `ApiError` never got an answer from the server: a dropped connection,
  // a timeout of ours, or a 2xx whose body would not parse. All safe to resend under the same key.
  if (!(error instanceof ApiError)) return "network";
  const { status } = error;
  if (status === 401) return "unauthorized";
  // 408 and 429 are the server asking for another go, not refusing the put-away.
  if (status >= 500 || status === 408 || status === 429) return "retry";
  if (status >= 400) return "reject";
  return "retry";
}

export type QueuedReason = "offline" | "network" | "server" | "session";

export type PutawayOutcome =
  | { kind: "synced"; result: PutawayOut }
  | { kind: "queued"; reason: QueuedReason }
  | { kind: "rejected"; status: number; message: string };

export interface DrainResult {
  /** What happened to each entry attempted this pass, by idempotency key. */
  outcomes: ReadonlyMap<string, PutawayOutcome>;
  synced: number;
  /** Entries are still waiting for a retry: a network failure, a server error, a lapsed session. */
  stalled: boolean;
}

export interface DrainOptions {
  /** The entry the worker just confirmed and is watching the screen for; see the file header. */
  foregroundKey?: string | undefined;
  /** Whether that screen is still there. Asked when a refusal arrives, not when the entry was
   *  confirmed — the worker may have left in between. Omitted, it is taken to be. */
  foregroundWatched?: (() => boolean) | undefined;
  signal?: AbortSignal | undefined;
}

/** What to tell the worker about a refusal: the server's own sentence, except for a date after its
 *  own today. Only a phone whose clock is ahead can send one, and neither "occurred_on cannot be in
 *  the future" nor the generic "payload failed validation" the same response carries as its
 *  `detail` means anything to the person holding the phone. */
function refusalMessage(error: ApiError): string {
  if (error.status === 422 && fieldError(error.problem, "occurred_on") !== undefined) {
    return "The date on this phone is ahead of the server's. Fix the phone's date and time settings, then put it away again.";
  }
  return problemMessage(error);
}

/** Still queued and still waiting to be sent: not purged, discarded or parked since the pass began. */
function stillWaiting(key: string): boolean {
  return getPutawayQueue().some((entry) => entry.idempotencyKey === key && entry.status === "pending");
}

async function send(entry: PutawayQueueEntry, outer: AbortSignal | undefined): Promise<PutawayOut> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ATTEMPT_TIMEOUT_MS);
  const abort = () => controller.abort();
  outer?.addEventListener("abort", abort);
  try {
    return await postPutaway(entry.body, entry.idempotencyKey, controller.signal);
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", abort);
  }
}

export async function drainPutawayQueue(owner: PutawayOwner, options: DrainOptions = {}): Promise<DrainResult> {
  const waiting = getPutawayQueue().filter((entry) => entry.status === "pending" && isOwnedBy(entry, owner));
  const outcomes = new Map<string, PutawayOutcome>();
  let synced = 0;
  let stalled = false;

  for (const entry of waiting) {
    if (options.signal?.aborted) {
      stalled = true;
      break;
    }
    const key = entry.idempotencyKey;
    // `waiting` was read once, before the first send; the queue may not be what it was.
    if (!stillWaiting(key)) continue;
    try {
      const result = await send(entry, options.signal);
      removePutaway(key);
      outcomes.set(key, { kind: "synced", result });
      synced += 1;
    } catch (error) {
      const failure = classifyFailure(error);
      if (failure === "reject" && error instanceof ApiError) {
        const message = refusalMessage(error);
        if (key === options.foregroundKey) {
          outcomes.set(key, { kind: "rejected", status: error.status, message });
          if (options.foregroundWatched?.() ?? true) removePutaway(key);
          else parkPutaway(key, { httpStatus: error.status, message });
        } else {
          parkPutaway(key, { httpStatus: error.status, message });
        }
        continue;
      }
      stalled = true;
      outcomes.set(key, {
        kind: "queued",
        reason: failure === "unauthorized" ? "session" : failure === "network" ? "network" : "server",
      });
      if (failure === "network" || failure === "unauthorized") break;
    }
  }

  return { outcomes, synced, stalled };
}
