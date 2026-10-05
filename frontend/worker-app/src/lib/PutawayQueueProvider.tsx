import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import { useToast } from "@opsgrids/ui";

import {
  enqueuePutaway,
  getPutawayQueue,
  isOwnedBy,
  isPutawayQueuePersistent,
  removePutaway,
  subscribePutawayQueue,
} from "@/lib/putawayQueue";
import { PutawayQueueContext } from "@/lib/putawayQueueContext";
import type { PutawayQueueControls } from "@/lib/putawayQueueContext";
import { drainPutawayQueue } from "@/lib/putawaySync";
import type { DrainResult } from "@/lib/putawaySync";
import { useOnlineStatus } from "@/lib/useOnlineStatus";

/** Between background retries while the server or the signal keeps failing: 15 s, then doubling
 *  up to five minutes. A manual "Sync now", or the phone coming back online, starts over. */
const RETRY_BASE_MS = 15_000;
const RETRY_MAX_MS = 5 * 60_000;

const NOTHING_SENT: DrainResult = { outcomes: new Map(), synced: 0, stalled: false };

function phoneThinksItIsOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

interface PutawayQueueProviderProps {
  /** The signed-in user (`MeOut.user_id`) — whose entries these are, and the only one whose
   *  entries this provider will ever send. */
  ownerSub: string;
  organizationId: string | null;
  children: ReactNode;
}

/**
 * The put-away drainer, mounted once at the app shell — never per screen, so a put-away queued on
 * a basement floor still syncs when the worker is on Home, or Counts, or anywhere, by the time the
 * signal returns. It replays on mount (a queue left by a closed app), on reconnect, by hand, and on
 * a backing-off timer while something keeps failing — and one pass at a time, so two triggers can
 * never put the same entry on the wire twice at once.
 *
 * What a pass does with each answer is `lib/putawaySync.ts`; the queue itself, and its rules about
 * who owns an entry and what happens to coordinates, is `lib/putawayQueue.ts`.
 */
export function PutawayQueueProvider({ ownerSub, organizationId, children }: PutawayQueueProviderProps) {
  const toast = useToast();
  const online = useOnlineStatus();
  const all = useSyncExternalStore(subscribePutawayQueue, getPutawayQueue);
  const persistent = useSyncExternalStore(subscribePutawayQueue, isPutawayQueuePersistent);
  const [passesInFlight, setPassesInFlight] = useState(0);

  const entries = useMemo(
    () => all.filter((entry) => isOwnedBy(entry, { ownerSub, organizationId })),
    [all, ownerSub, organizationId],
  );
  const pending = entries.filter((entry) => entry.status === "pending").length;
  const attention = useMemo(() => entries.filter((entry) => entry.status === "needs_attention"), [entries]);

  const ownerRef = useRef({ ownerSub, organizationId });
  const toastRef = useRef(toast);
  useEffect(() => {
    ownerRef.current = { ownerSub, organizationId };
    toastRef.current = toast;
  });

  const abortRef = useRef<AbortController | null>(null);
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());
  const backgroundQueuedRef = useRef(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryRoundRef = useRef(0);
  const mountedRef = useRef(false);
  /** A pass that stalls schedules the next background pass, and `requestBackground` is built on
   *  `schedulePass` — so the retry reaches it through a ref rather than a circular dependency. */
  const requestBackgroundRef = useRef<() => void>(() => undefined);

  /** Queue a pass behind any already running — one at a time, in order. */
  const schedulePass = useCallback((foregroundKey?: string, foregroundWatched?: () => boolean): Promise<DrainResult> => {
    const run = chainRef.current.then(async (): Promise<DrainResult> => {
      if (foregroundKey === undefined) backgroundQueuedRef.current = false;
      if (!mountedRef.current || phoneThinksItIsOffline()) return NOTHING_SENT;

      setPassesInFlight((n) => n + 1);
      let result: DrainResult;
      try {
        result = await drainPutawayQueue(ownerRef.current, {
          foregroundKey,
          foregroundWatched,
          ...(abortRef.current ? { signal: abortRef.current.signal } : {}),
        });
      } finally {
        if (mountedRef.current) setPassesInFlight((n) => n - 1);
      }

      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
      if (result.synced > 0) retryRoundRef.current = 0;
      if (result.stalled && mountedRef.current) {
        const delay = Math.min(RETRY_BASE_MS * 2 ** retryRoundRef.current, RETRY_MAX_MS);
        retryRoundRef.current += 1;
        retryTimerRef.current = setTimeout(() => requestBackgroundRef.current(), delay);
      }

      // The worker watching the screen is told about their own put-away there; a toast is for the
      // ones that synced while they were doing something else.
      const watched = foregroundKey !== undefined && result.outcomes.get(foregroundKey)?.kind === "synced" ? 1 : 0;
      const quietly = result.synced - watched;
      if (quietly > 0) {
        toastRef.current.success(`${quietly} queued put-away${quietly === 1 ? "" : "s"} synced.`);
      }
      return result;
    });
    chainRef.current = run.catch(() => undefined);
    return run;
  }, []);

  const requestBackground = useCallback(() => {
    if (backgroundQueuedRef.current) return;
    backgroundQueuedRef.current = true;
    void schedulePass();
  }, [schedulePass]);
  useEffect(() => {
    requestBackgroundRef.current = requestBackground;
  }, [requestBackground]);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    return () => {
      mountedRef.current = false;
      controller.abort();
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    };
  }, []);

  // On mount, on reconnect and whenever the signed-in person changes: send whatever is waiting for
  // them. Nothing waiting means nothing is scheduled.
  useEffect(() => {
    if (!online) return;
    retryRoundRef.current = 0;
    const owner = { ownerSub, organizationId };
    if (getPutawayQueue().some((entry) => entry.status === "pending" && isOwnedBy(entry, owner))) {
      requestBackground();
    }
  }, [online, ownerSub, organizationId, requestBackground]);

  const submit = useCallback<PutawayQueueControls["submit"]>(
    async (input) => {
      const entry = enqueuePutaway({ ...ownerRef.current, body: input.body, summary: input.summary });
      if (phoneThinksItIsOffline()) return { kind: "queued", reason: "offline" };
      const result = await schedulePass(entry.idempotencyKey, input.watching);
      // Not reached this pass (an earlier entry hit a dead connection first): it is still queued.
      return result.outcomes.get(entry.idempotencyKey) ?? { kind: "queued", reason: "network" };
    },
    [schedulePass],
  );

  const syncNow = useCallback(() => {
    retryRoundRef.current = 0;
    requestBackground();
  }, [requestBackground]);

  const discard = useCallback((idempotencyKey: string) => removePutaway(idempotencyKey), []);

  const value = useMemo<PutawayQueueControls>(
    () => ({
      entries,
      pending,
      attention,
      online,
      syncing: passesInFlight > 0,
      persistent,
      submit,
      syncNow,
      discard,
    }),
    [entries, pending, attention, online, passesInFlight, persistent, submit, syncNow, discard],
  );

  return <PutawayQueueContext value={value}>{children}</PutawayQueueContext>;
}
