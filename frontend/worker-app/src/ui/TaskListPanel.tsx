import { Card, ClipboardListIcon, EmptyState, ErrorState, Skeleton } from "@opsgrids/ui";

export type TaskListStatus = "loading" | "empty" | "error";

interface TaskListPanelProps {
  status: TaskListStatus;
  /** The problem's own sentence, for `status: "error"`. */
  errorMessage?: string | undefined;
  onRetry?: (() => void) | undefined;
}

/**
 * The task list's three built states, reserving the same block of layout in every one of them
 * (frontend/CLAUDE.md: no layout shift on load).
 *
 * There is no fourth, "populated" state here yet: F3's pick-task/offer backend does not exist
 * (`docs/fulfilment/plan.md`), so there is nothing to fetch and nothing to populate this with.
 * `HomePage` drives this with a constant `status="empty"` today — which is also the honest
 * answer, since a caller with no assignable tasks and a caller whose warehouse has not turned
 * on task offers look the same until there is a real endpoint to tell them apart. `loading` and
 * `error` are built and tested here so wiring a real `useAsync(loadMyTasks, …)` later is a
 * one-line change in `HomePage`, not a new component.
 */
export function TaskListPanel({ status, errorMessage, onRetry }: TaskListPanelProps) {
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
