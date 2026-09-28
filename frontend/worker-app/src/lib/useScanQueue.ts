import { useCallback, useEffect, useState } from "react";

import { scanTask } from "@/api/client";
import type { ScanIn } from "@/api/types";
import { dequeueScan, enqueueScan, queuedScansForTask } from "@/lib/scanQueue";
import { useOnlineStatus } from "@/lib/useOnlineStatus";

export interface ScanQueueControls {
  /** Waiting to sync, oldest first. */
  pending: number;
  online: boolean;
  replaying: boolean;
  /** The most recent replay failure — still queued, will try again on the next reconnect. */
  replayError: Error | null;
  /** Save a scan locally and (if online) start replaying immediately. */
  enqueue: (payload: ScanIn) => void;
  /** Ask again by hand — the banner's own "Sync now". */
  replay: () => void;
}

/**
 * The scan screen's queue for one task. Replays in order, oldest first, and stops at the first
 * failure rather than skip past it — a scan queued at 9:02 must sync before one queued at 9:05,
 * or the append-only `scan_events` trail reads out of order.
 */
export function useScanQueue(taskId: string): ScanQueueControls {
  const online = useOnlineStatus();
  const [pending, setPending] = useState(() => queuedScansForTask(taskId).length);
  const [replaying, setReplaying] = useState(false);
  const [replayError, setReplayError] = useState<Error | null>(null);

  const refresh = useCallback(() => setPending(queuedScansForTask(taskId).length), [taskId]);

  const replay = useCallback(() => {
    if (replaying) return;
    setReplaying(true);
    setReplayError(null);
    void (async () => {
      try {
        for (const entry of queuedScansForTask(taskId)) {
          await scanTask(taskId, entry.payload);
          dequeueScan(entry.id);
          refresh();
        }
      } catch (err) {
        setReplayError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        setReplaying(false);
      }
    })();
  }, [taskId, replaying, refresh]);

  const enqueue = useCallback(
    (payload: ScanIn) => {
      enqueueScan(taskId, payload);
      refresh();
    },
    [taskId, refresh],
  );

  // Reconnect: try the queue again. Also runs once on mount, so a queue left over from a session
  // that closed mid-shift still drains the next time this screen opens online.
  useEffect(() => {
    if (online) replay();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online, taskId]);

  return { pending, online, replaying, replayError, enqueue, replay };
}
