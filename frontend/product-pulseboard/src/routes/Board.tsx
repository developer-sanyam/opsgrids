import { useCallback } from "react";

import { getPeople, getProjects, getTasks } from "@/api/client";
import type { Person, Project, Task } from "@/api/types";
import { useAsync } from "@opsgrids/client";
import { Card, ErrorState, KanbanBoard, PageHeader, Skeleton, TASK_STATUS_META } from "@/ui";
import { ColumnsIcon } from "@opsgrids/ui";

const COLUMN_ORDER = ["backlog", "in_progress", "review", "done"] as const;

interface BoardData {
  tasks: Task[];
  people: Person[];
  projects: Project[];
}

async function loadBoard(): Promise<BoardData> {
  const [tasks, people, projects] = await Promise.all([getTasks(), getPeople(), getProjects()]);
  return { tasks, people, projects };
}

/** The four column headers previewed while the board loads, so nothing shifts on resolve. */
function BoardSkeleton() {
  return (
    <div className="flex gap-4 overflow-x-auto pb-2">
      {COLUMN_ORDER.map((col) => (
        <div key={col} className="flex w-[280px] shrink-0 flex-col gap-3 rounded-card bg-ground/60 p-3">
          <div className="flex items-center gap-2 px-1">
            <span className="size-2.5 rounded-full" style={{ backgroundColor: TASK_STATUS_META[col].color }} aria-hidden />
            <span className="text-sm font-bold text-ink">{TASK_STATUS_META[col].label}</span>
          </div>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-tile" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function BoardPage() {
  const state = useAsync(useCallback(loadBoard, []), []);
  const total = state.status === "success" ? state.data.tasks.length : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Sprint 34"
        title="Board"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <ColumnsIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="text-muted font-medium">tasks</span>
          </span>
        }
      />

      <p className="text-sm text-muted -mt-2">
        Drag a card between columns, or use its menu to move it. Changes are announced for screen
        readers.
      </p>

      {state.status === "loading" ? <BoardSkeleton /> : null}
      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} />
        </Card>
      ) : null}
      {state.status === "success" ? (
        <KanbanBoard tasks={state.data.tasks} people={state.data.people} projects={state.data.projects} />
      ) : null}
    </div>
  );
}
