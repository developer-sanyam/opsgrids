import { Link } from "react-router-dom";

import {
  Card,
  ChevronRightIcon,
  ClipboardListIcon,
  EmptyState,
  ErrorState,
  Skeleton,
  formatRelative,
} from "@opsgrids/ui";

import type { AssignmentOfferOut, PickTaskOut } from "@/api/types";
import { PickTaskStatusPill } from "@/ui/StatusPill";

export type TaskListStatus = "loading" | "empty" | "error" | "populated";

/** One row: the task, and whichever of its offers is the caller's own live one (if any) — the
 *  screen already resolved this while deciding which tasks are "mine" (`Home.tsx`), so the panel
 *  never has to repeat that search per row. */
export interface WorkerTaskRow {
  task: PickTaskOut;
  myOffer: AssignmentOfferOut | null;
}

interface TaskListPanelProps {
  status: TaskListStatus;
  /** Only read when `status === "populated"`. */
  tasks?: WorkerTaskRow[] | undefined;
  /** The problem's own sentence, for `status: "error"`. */
  errorMessage?: string | undefined;
  onRetry?: (() => void) | undefined;
}

/**
 * The task list's four states, reserving the same block of layout in every one of them
 * (frontend/CLAUDE.md: no layout shift on load).
 *
 * `populated` renders from `GET /operations/fulfilment/tasks`, filtered client-side to the
 * caller's own live offers (`Home.tsx`'s own `useMyTasks`) — there is no `mine`/`worker_id` filter
 * on the endpoint itself (`api/client.ts`'s note on `listTasks`).
 */
export function TaskListPanel({ status, tasks, errorMessage, onRetry }: TaskListPanelProps) {
  if (status === "loading") {
    return (
      <div role="status" aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">Loading tasks</span>
        {[0, 1, 2].map((row) => (
          <Card key={row} className="flex items-center gap-4 p-4">
            <Skeleton className="size-11 shrink-0" />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </div>
          </Card>
        ))}
      </div>
    );
  }

  if (status === "error") {
    return (
      <Card className="p-2">
        <ErrorState
          title="Couldn't load your tasks"
          message={errorMessage ?? "Something went wrong. Check your connection and try again."}
          {...(onRetry ? { onRetry } : {})}
        />
      </Card>
    );
  }

  if (status === "populated") {
    const rows = tasks ?? [];
    if (rows.length === 0) {
      return (
        <Card className="p-2">
          <EmptyState
            icon={<ClipboardListIcon className="size-6" aria-hidden />}
            title="No tasks yet"
            description="Pick, pack and dispatch tasks will appear here once your warehouse turns on task offers."
          />
        </Card>
      );
    }
    return (
      <ul className="flex flex-col gap-3">
        {rows.map(({ task, myOffer }) => (
          <li key={task.id}>
            <Link
              to={`/tasks/${task.id}`}
              className="flex items-center gap-4 rounded-card bg-surface p-4 shadow-card transition-colors hover:bg-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-sunken text-ink-soft">
                <ClipboardListIcon className="size-5" aria-hidden />
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="truncate font-semibold text-ink tnum">Order {task.order_id.slice(0, 8)}</span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <PickTaskStatusPill status={task.status} />
                  <span>
                    {task.lines.length} line{task.lines.length === 1 ? "" : "s"}
                  </span>
                  {task.status === "OFFERED" && myOffer ? (
                    <span>Expires {formatRelative(myOffer.expires_at)}</span>
                  ) : null}
                </span>
              </div>
              <ChevronRightIcon className="size-4 shrink-0 text-faint" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <Card className="p-2">
      <EmptyState
        icon={<ClipboardListIcon className="size-6" aria-hidden />}
        title="No tasks yet"
        description="Pick, pack and dispatch tasks will appear here once your warehouse turns on task offers."
      />
    </Card>
  );
}
