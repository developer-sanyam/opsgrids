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
 * "My tasks": `GET /operations/fulfilment/tasks` carries no `worker_id`/`mine` filter
 * (`api/client.ts`'s own note), so this fetches every live-status task org-wide — which
 * `operations.fulfilment.task.read:ORG` already lets a worker do, same as the dispatcher board —
 * and keeps only the ones with a live offer that is this worker's own. One request per status,
 * in parallel, each capped at `FULL_PAGE` (200): the org-wide "board" a `task.read:ORG` holder can
 * see is not expected to exceed that in the open statuses at once for the MVP's warehouse sizes;
 * a busier one would need the `worker_id` filter this endpoint does not have (this app's
 * `README.md` gap list).
 */
export function useMyTasks(workerId: string | null): AsyncState<WorkerTaskRow[]> & AsyncControls {
  return useAsync(async () => {
    if (!workerId) return [];
    const pages = await Promise.all(
      LIVE_STATUSES.map((status) => listTasks({ status, size: FULL_PAGE })),
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
