import { useAsync } from "@opsgrids/client";
import type { AsyncControls, AsyncState } from "@opsgrids/client";

import { FULL_PAGE, listTasks } from "@/api/client";
import type { PickTaskStatus } from "@/api/types";
import type { WorkerTaskRow } from "@/ui/TaskListPanel";
import { findMyLiveOffer } from "@/worker/offers";

/** Every status a task can be in while it is still live work for a worker: waiting on their
 *  answer, or already theirs to execute. `CREATED`/`ESCALATED` are not here — those have no live
 *  offer to anyone yet, and `PICKED`/`CLOSED`/`CANCELLED` are done with this worker either way. */
const LIVE_STATUSES: PickTaskStatus[] = ["OFFERED", "ACCEPTED", "IN_PROGRESS", "EXCEPTION"];

/** Lower sorts first: an offer waiting on the worker's own answer outranks work already in hand,
 *  which outranks a task paused on an exception (still needs attention, but not a fresh decision). */
const STATUS_RANK: Record<PickTaskStatus, number> = {
  OFFERED: 0,
  IN_PROGRESS: 1,
  ACCEPTED: 2,
  EXCEPTION: 3,
  CREATED: 4,
  ESCALATED: 4,
  PICKED: 5,
  CLOSED: 6,
  CANCELLED: 6,
};

/**
 * "My tasks": `GET /operations/fulfilment/tasks?assigned_to_me=true`, so the server returns only
 * the tasks this worker holds a live offer on — not the whole org's board filtered here. One
 * request per live status, in parallel (the endpoint takes one status at a time), each small by
 * construction: a worker holds a handful of live offers, never a warehouse's worth. The offer is
 * still checked against `now` client-side, because an `OFFERED` offer can have expired before the
 * sweep marks it so.
 */
export function useMyTasks(workerId: string | null): AsyncState<WorkerTaskRow[]> & AsyncControls {
  return useAsync(async () => {
    if (!workerId) return [];
    const pages = await Promise.all(
      LIVE_STATUSES.map((status) => listTasks({ status, assigned_to_me: true, size: FULL_PAGE })),
    );
    const now = new Date();
    const rows: WorkerTaskRow[] = [];
    for (const page of pages) {
      for (const task of page.items) {
        const myOffer = findMyLiveOffer(task, workerId, now);
        if (myOffer) rows.push({ task, myOffer });
      }
    }
    rows.sort((a, b) => {
      const rank = STATUS_RANK[a.task.status] - STATUS_RANK[b.task.status];
      if (rank !== 0) return rank;
      return a.task.created_at.localeCompare(b.task.created_at);
    });
    return rows;
  }, [workerId]);
}
