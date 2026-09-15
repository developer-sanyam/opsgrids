import { getAllProjects, listTasks } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import { Card, ErrorState, KanbanBoard, PageHeader, Skeleton } from "@/ui";
import { BoardIcon } from "@opsgrids/ui";

export function BoardPage() {
  const tasksState = useAsync(listTasks, []);
  // The board only needs tasks; projects are prefetched so the header count is ready in one paint.
  useAsync(getAllProjects, []);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Workflow"
        title="Board"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <BoardIcon className="size-4 text-muted" />
            <span className="tnum">
              {tasksState.status === "success" ? tasksState.data.total : "—"}
            </span>
            <span className="text-muted font-medium">cards</span>
          </span>
        }
      />

      {tasksState.status === "loading" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, col) => (
            <div key={col} className="flex flex-col gap-3 rounded-card bg-ground/60 p-3">
              <Skeleton className="h-5 w-28 rounded-full" />
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-28 w-full rounded-tile" />
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {tasksState.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={tasksState.error.message} />
        </Card>
      ) : null}

      {tasksState.status === "success" ? <KanbanBoard tasks={tasksState.data.items} /> : null}
    </div>
  );
}
